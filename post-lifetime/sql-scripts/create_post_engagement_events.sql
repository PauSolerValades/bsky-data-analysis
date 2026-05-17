-- post-lifetime/sql-scripts/create_post_engagement_events.sql
-- =============================================================================
-- Creates pau_db.post_engagement_events — the raw event timeline for every
-- top-level post.  Each row is one engagement event (repost, like, or direct
-- reply) targeting a top-level post.
--
-- Joined with post_lifetime, this enables:
--   - Per-post temporal decay fitting (Phase 2b)
--   - Engagement cascade ordering (Phase 6)
--   - Inter-arrival time distributions
--   - First-engagement time (redundant with post_lifetime.first_* but useful
--     for per-event analysis)
--
-- Columns:
--   post_did        DID of the target post author
--   post_rkey       Record key of the target post
--   event_time_us   When the engagement happened (µs epoch)
--   event_type      'repost', 'like', or 'reply'
--   actor_did       DID of the user who performed the engagement
--
-- Run:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < post-lifetime/sql-scripts/create_post_engagement_events.sql
--
-- ⚠️  If the table already exists, ask a DBA to drop it first.
-- =============================================================================

USE pau_db;

CREATE TABLE IF NOT EXISTS post_engagement_events (
    post_did        VARCHAR(128)  NOT NULL COMMENT 'DID of the target post author',
    post_rkey       VARCHAR(16)   NOT NULL COMMENT 'Record key of the target post',
    event_time_us   BIGINT        NOT NULL COMMENT 'Engagement timestamp (µs epoch)',
    event_type      VARCHAR(16)   NOT NULL COMMENT 'repost | like | reply',
    actor_did       VARCHAR(128)  NULL     COMMENT 'DID of the user who engaged'
)
ENGINE = OLAP
DUPLICATE KEY(`post_did`, `post_rkey`, `event_time_us`)
DISTRIBUTED BY HASH(`post_did`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1"
);
