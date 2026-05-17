-- =============================================================================
-- Create pau_db.user_core_events
-- =============================================================================
-- A filtered table containing only the engagement events relevant for
-- session-based analysis, following Twitter session-study methodology:
--
--   Event type  | Twitter equivalent     | Bluesky source
--   ------------|------------------------|-----------------------------
--   'post'      | Original tweet         | bsky.posts (top-level, incl. quote posts)
--   'reply'     | Reply / quote-tweet    | bsky.posts (has reply parent)
--   'repost'    | Retweet                | bsky.records (feed.repost)
--
-- Quote posts are regular app.bsky.feed.post records that embed another post
-- via record_json. They are NOT a separate record type, so they fall into
-- 'post' (if top-level) or 'reply' (if part of a thread) automatically.
--
-- Table design:
--   - DUPLICATE KEY on (did, time_us) for fast per-user time-range scans.
--   - Same bucket count as sessions_tukey so joins stay local.
-- =============================================================================

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
