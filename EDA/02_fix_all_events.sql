-- =============================================================================
-- EDA/02_fix_all_events.sql
-- =============================================================================
-- Rebuilds pau_db.all_events with deduplication.
--
-- Fixes:
--   1. Duplicate (did, time_us) rows — SELECT DISTINCT on (did, time_us, event_type)
--   2. Post double-counting — posts from bsky.records (feed_post) excluded
--
-- Prerequisites: none.
-- Run time: ~30–60 seconds.
--
-- Usage:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < EDA/02_fix_all_events.sql
-- =============================================================================

USE pau_db;

-- ---------------------------------------------------------------------------
-- Drop old table
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS all_events;

-- ---------------------------------------------------------------------------
-- Recreate
-- ---------------------------------------------------------------------------

CREATE TABLE all_events (
    did         VARCHAR(128) NOT NULL,
    time_us     BIGINT       NOT NULL,
    event_type  VARCHAR(32)  NOT NULL
)
ENGINE = OLAP
DUPLICATE KEY(did, time_us)
DISTRIBUTED BY HASH(did) BUCKETS 32
PROPERTIES ("replication_num" = "1");

-- ---------------------------------------------------------------------------
-- Populate — deduplicated
-- ---------------------------------------------------------------------------

INSERT INTO all_events (did, time_us, event_type)

WITH eligible AS (
    -- Users with ≥8 events in the window (deduplicated count)
    SELECT did
    FROM (
        SELECT DISTINCT did, time_us,
               REPLACE(REPLACE(collection, 'app.bsky.', ''), '.', '_') AS event_type
        FROM bsky.records
        WHERE time_us >= 1775865600000000          -- 2026-04-11 00:00:00 UTC
          AND time_us <  1776556800000000          -- 2026-04-19 00:00:00 UTC
          AND collection LIKE 'app.bsky.%'
          AND operation = 'create'
          AND collection != 'app.bsky.feed.post'   -- exclude posts (handled below)

        UNION ALL

        SELECT DISTINCT did, time_us,
               CASE WHEN reply_root_uri IS NULL THEN 'post_top' ELSE 'post_reply' END
        FROM bsky.posts
        WHERE time_us >= 1775865600000000
          AND time_us <  1776556800000000
    ) raw
    GROUP BY did
    HAVING COUNT(*) >= 8
)

-- Records (excluding app.bsky.feed.post — handled by posts below)
SELECT DISTINCT
    r.did,
    r.time_us,
    REPLACE(REPLACE(r.collection, 'app.bsky.', ''), '.', '_') AS event_type
FROM bsky.records r
JOIN eligible e ON r.did = e.did
WHERE r.time_us >= 1775865600000000
  AND r.time_us <  1776556800000000
  AND r.collection LIKE 'app.bsky.%'
  AND r.operation = 'create'
  AND r.collection != 'app.bsky.feed.post'

UNION ALL

-- Top-level posts
SELECT DISTINCT p.did, p.time_us, 'post_top' AS event_type
FROM bsky.posts p
JOIN eligible e ON p.did = e.did
WHERE p.time_us >= 1775865600000000
  AND p.time_us <  1776556800000000
  AND p.reply_root_uri IS NULL

UNION ALL

-- Replies
SELECT DISTINCT p.did, p.time_us, 'post_reply' AS event_type
FROM bsky.posts p
JOIN eligible e ON p.did = e.did
WHERE p.time_us >= 1775865600000000
  AND p.time_us <  1776556800000000
  AND p.reply_root_uri IS NOT NULL;


-- ---------------------------------------------------------------------------
-- Validation
-- ---------------------------------------------------------------------------

SELECT '--- all_events (fixed) ---' AS info;

SELECT 'total rows' AS metric, COUNT(*) AS value FROM all_events
UNION ALL
SELECT 'distinct users', COUNT(DISTINCT did) FROM all_events
UNION ALL
SELECT 'distinct event types', COUNT(DISTINCT event_type) FROM all_events;
