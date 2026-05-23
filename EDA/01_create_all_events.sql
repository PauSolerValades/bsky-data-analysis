-- =============================================================================
-- EDA/01_create_all_events.sql
-- =============================================================================
-- Creates and populates pau_db.all_events — every event from every major
-- collection, for users with ≥8 total events in the 8-day firehose window.
--
-- Filters:
--   1. time_us within [2026-04-11, 2026-04-19) — the firehose capture window
--   2. Users with ≥8 total events (power-law xmin from EDA §4)
--   3. Only CREATE operations (no deletes/updates)
--   4. All app.bsky.* collections (no arbitrary cutoff — the ≥8 event
--      threshold is the only filter)
--
-- Event types included:
--   All app.bsky.* collections (records) + all posts (top-level + replies).
--   The event_type column uses the short collection name without the
--   'app.bsky.' prefix (e.g. 'feed.like', 'graph.follow', 'feed.threadgate').
--   Posts are mapped to 'feed.post.top' and 'feed.post.reply'.
--
-- Prerequisites: none (reads from bsky.records + bsky.posts directly).
-- Run time: ~20–30 seconds.
--
-- Usage:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < EDA/01_create_all_events.sql
-- =============================================================================

USE pau_db;

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS all_events (
    did         VARCHAR(128) NOT NULL,
    time_us     BIGINT       NOT NULL,
    event_type  VARCHAR(32)  NOT NULL   -- e.g. 'like', 'repost', 'follow', 'post.top', 'post.reply'
)
ENGINE = OLAP
DUPLICATE KEY(did, time_us)
DISTRIBUTED BY HASH(did) BUCKETS 32
PROPERTIES ("replication_num" = "1");

-- ---------------------------------------------------------------------------
-- Populate
-- ---------------------------------------------------------------------------

INSERT INTO all_events (did, time_us, event_type)

WITH eligible AS (
    -- Users with ≥8 events of any major type in the window
    SELECT did
    FROM (
        SELECT did, time_us
        FROM bsky.records
        WHERE time_us >= 1775865600000000          -- 2026-04-11 00:00:00 UTC
          AND time_us <  1776556800000000          -- 2026-04-19 00:00:00 UTC
          AND collection LIKE 'app.bsky.%'
          AND operation = 'create'

        UNION ALL

        SELECT did, time_us
        FROM bsky.posts
        WHERE time_us >= 1775865600000000
          AND time_us <  1776556800000000
    ) raw
    GROUP BY did
    HAVING COUNT(*) >= 8
)

-- All records (any app.bsky.* collection)
SELECT
    r.did,
    r.time_us,
    -- Short event type: collection name without 'app.bsky.' prefix,
    -- with '.' replaced by '_' for compactness.
    REPLACE(REPLACE(r.collection, 'app.bsky.', ''), '.', '_') AS event_type
FROM bsky.records r
JOIN eligible e ON r.did = e.did
WHERE r.time_us >= 1775865600000000
  AND r.time_us <  1776556800000000
  AND r.collection LIKE 'app.bsky.%'
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

SELECT '--- all_events ---' AS info;

SELECT 'total rows' AS metric, COUNT(*) AS value FROM all_events
UNION ALL
SELECT 'distinct users', COUNT(DISTINCT did) FROM all_events
UNION ALL
SELECT 'distinct event types', COUNT(DISTINCT event_type) FROM all_events;
