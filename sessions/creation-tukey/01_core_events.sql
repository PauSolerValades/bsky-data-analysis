-- =============================================================================
-- 01_core_events.sql
-- =============================================================================
-- Creates and populates pau_db.user_core_events — the base table of filtered
-- engagement events (posts, replies, reposts) for all 1.75M users.
--
-- This is the first step of the Tukey session pipeline.  The three event types
-- follow the Twitter session-study methodology (Kooti et al., SocInfo 2016):
--
--   'post'   → top-level app.bsky.feed.post (no reply parent)
--   'reply'  → app.bsky.feed.post with a reply parent
--   'repost' → app.bsky.feed.repost
--
-- Likes, follows, and other record types are NOT included here — they are
-- fetched directly from bsky.records / bsky.posts by cluster_tukey.py
-- because the Tukey method uses ALL event types for gap estimation.
--
-- This table is used ONLY to filter which DIDs to process (≥6 events → not a
-- tourist; ≤500 events → not a bot).  The actual session clustering data comes
-- from the raw source tables.
--
-- Prerequisites: access to bsky database (read-only).
-- Run time: ~30 seconds.
--
-- Usage:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < 01_core_events.sql
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pau_db.user_core_events (
    `did`        varchar(128) NOT NULL,
    `time_us`    bigint       NOT NULL,
    `event_type` varchar(16)  NOT NULL   -- 'post', 'reply', 'repost'
) ENGINE = OLAP
DUPLICATE KEY(`did`, `time_us`)
DISTRIBUTED BY HASH(`did`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1"
);

-- ---------------------------------------------------------------------------
-- Populate
-- ---------------------------------------------------------------------------
INSERT INTO pau_db.user_core_events (did, time_us, event_type)

-- 1. Top-level posts (original content, NOT replies)
SELECT did,
       time_us,
       'post' AS event_type
FROM bsky.posts
WHERE reply_root_uri IS NULL

UNION ALL

-- 2. Replies (posts that are part of a reply chain)
SELECT did,
       time_us,
       'reply' AS event_type
FROM bsky.posts
WHERE reply_root_uri IS NOT NULL

UNION ALL

-- 3. Reposts (content amplification)
SELECT did,
       time_us,
       'repost' AS event_type
FROM bsky.records
WHERE collection = 'app.bsky.feed.repost'
  AND operation   = 'create';
