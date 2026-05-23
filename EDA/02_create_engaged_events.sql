-- =============================================================================
-- EDA/02_create_engaged_events.sql
-- =============================================================================
-- Creates and populates pau_db.engaged_events — content-creation and curation
-- events only (no likes). These are actions where the user is actively
-- producing something: posting, replying, reposting, following, blocking.
--
-- Filters:
--   1. time_us within [2026-04-11, 2026-04-19) — the firehose capture window
--   2. Users with ≥4 engaged events in the window (power-law xmin from
--      the engaged-events distribution: α=1.67, xmin=4, KS=0.105,
--      53.0% of users in the tail)
--   3. Only CREATE operations (no deletes/updates)
--
-- Event types included:
--   All app.bsky.* records EXCEPT feed.like (passive engagement).
--   All posts (top-level + replies).
--   Event types use collection names without 'app.bsky.' prefix.
--
-- Contrast with all_events:  all_events includes likes and answers "when is
-- the user browsing?"  engaged_events answers "when is the user creating or
-- curating content?"  Different questions, different session boundaries.
--
-- Prerequisites: none (reads from bsky.records + bsky.posts directly).
-- Run time: ~15 seconds.
--
-- Usage:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < EDA/02_create_engaged_events.sql
-- =============================================================================

USE pau_db;

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS engaged_events (
    did         VARCHAR(128) NOT NULL,
    time_us     BIGINT       NOT NULL,
    event_type  VARCHAR(32)  NOT NULL   -- e.g. 'repost', 'follow', 'post_top', 'post_reply'
)
ENGINE = OLAP
DUPLICATE KEY(did, time_us)
DISTRIBUTED BY HASH(did) BUCKETS 32
PROPERTIES ("replication_num" = "1");

-- ---------------------------------------------------------------------------
-- Populate
-- ---------------------------------------------------------------------------

INSERT INTO engaged_events (did, time_us, event_type)

WITH eligible AS (
    -- Users with ≥4 engaged events in the window.
    -- Power-law fit on raw engaged-events distribution: α=1.67, xmin=4
    -- (see EDA §4 — the tail covers 53.0% of engaged users).
    SELECT did
    FROM (
        SELECT did, time_us
        FROM bsky.posts
        WHERE time_us >= 1775865600000000          -- 2026-04-11 00:00:00 UTC
          AND time_us <  1776556800000000          -- 2026-04-19 00:00:00 UTC

        UNION ALL

        SELECT did, time_us
        FROM bsky.records
        WHERE time_us >= 1775865600000000
          AND time_us <  1776556800000000
          AND collection LIKE 'app.bsky.%'
          AND collection != 'app.bsky.feed.like'
          AND operation = 'create'
    ) raw
    GROUP BY did
    HAVING COUNT(*) >= 4
)

-- All engaged records (any app.bsky.* except feed.like)
SELECT
    r.did,
    r.time_us,
    REPLACE(REPLACE(r.collection, 'app.bsky.', ''), '.', '_') AS event_type
FROM bsky.records r
JOIN eligible e ON r.did = e.did
WHERE r.time_us >= 1775865600000000
  AND r.time_us <  1776556800000000
  AND r.collection LIKE 'app.bsky.%'
  AND r.collection != 'app.bsky.feed.like'
  AND r.operation = 'create'

UNION ALL

-- Posts (top-level)
SELECT p.did, p.time_us, 'post_top' AS event_type
FROM bsky.posts p
JOIN eligible e ON p.did = e.did
WHERE p.time_us >= 1775865600000000
  AND p.time_us <  1776556800000000
  AND p.reply_root_uri IS NULL

UNION ALL

-- Replies
SELECT p.did, p.time_us, 'post_reply' AS event_type
FROM bsky.posts p
JOIN eligible e ON p.did = e.did
WHERE p.time_us >= 1775865600000000
  AND p.time_us <  1776556800000000
  AND p.reply_root_uri IS NOT NULL;


-- ---------------------------------------------------------------------------
-- Validation
-- ---------------------------------------------------------------------------

SELECT '--- engaged_events ---' AS info;

SELECT 'total rows' AS metric, COUNT(*) AS value FROM engaged_events
UNION ALL
SELECT 'distinct users', COUNT(DISTINCT did) FROM engaged_events
UNION ALL
SELECT 'distinct event types', COUNT(DISTINCT event_type) FROM engaged_events;
