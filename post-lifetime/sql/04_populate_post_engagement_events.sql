-- post-lifetime/sql/04_populate_post_engagement_events.sql
-- =============================================================================
-- Populates pau_db.post_engagement_events.
-- Run 02_create_post_engagement_events.sql first.
-- Requires pau_db.post_lifetime to be populated (used as the top-level filter).
--
-- Sources:
--   - Reposts: bsky.records  (app.bsky.feed.repost, operation = 'create')
--   - Likes:   bsky.records  (app.bsky.feed.like,   operation = 'create')
--   - Replies: bsky.posts    (reply_parent_uri IS NOT NULL)
--
-- All three are UNION ALL'd, then INNER JOINed with post_lifetime to keep
-- only events targeting top-level posts.  ~140M rows expected.
--
-- Run:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < post-lifetime/sql/04_populate_post_engagement_events.sql
-- =============================================================================

USE pau_db;

INSERT INTO post_engagement_events
    (post_did, post_rkey, event_time_us, event_type, actor_did)

SELECT
    e.post_did,
    e.post_rkey,
    e.event_time_us,
    e.event_type,
    e.actor_did
FROM (
    -- ── Reposts ─────────────────────────────────────────────────────────────
    SELECT
        SUBSTRING_INDEX(SUBSTRING_INDEX(subject_uri, '/', 3), '/', -1)
            AS post_did,
        SUBSTRING_INDEX(subject_uri, '/', -1)
            AS post_rkey,
        time_us
            AS event_time_us,
        'repost'
            AS event_type,
        did
            AS actor_did
    FROM bsky.records
    WHERE collection = 'app.bsky.feed.repost'
      AND operation   = 'create'

    UNION ALL

    -- ── Likes ───────────────────────────────────────────────────────────────
    SELECT
        SUBSTRING_INDEX(SUBSTRING_INDEX(subject_uri, '/', 3), '/', -1),
        SUBSTRING_INDEX(subject_uri, '/', -1),
        time_us,
        'like',
        did
    FROM bsky.records
    WHERE collection = 'app.bsky.feed.like'
      AND operation   = 'create'

    UNION ALL

    -- ── Direct replies ──────────────────────────────────────────────────────
    SELECT
        SUBSTRING_INDEX(SUBSTRING_INDEX(reply_parent_uri, '/', 3), '/', -1),
        SUBSTRING_INDEX(reply_parent_uri, '/', -1),
        time_us,
        'reply',
        did
    FROM bsky.posts
    WHERE reply_parent_uri IS NOT NULL

) e
INNER JOIN pau_db.post_lifetime pl
    ON e.post_did  = pl.post_did
   AND e.post_rkey = pl.post_rkey;


-- ── Validation ──────────────────────────────────────────────────────────────

SELECT 'total events'        AS metric, COUNT(*)    AS value FROM post_engagement_events
UNION ALL
SELECT 'repost events',      COUNT(*) FROM post_engagement_events WHERE event_type = 'repost'
UNION ALL
SELECT 'like events',        COUNT(*) FROM post_engagement_events WHERE event_type = 'like'
UNION ALL
SELECT 'reply events',       COUNT(*) FROM post_engagement_events WHERE event_type = 'reply'
UNION ALL
SELECT 'distinct target posts', COUNT(DISTINCT CONCAT(post_did, '|', post_rkey))
       FROM post_engagement_events
UNION ALL
SELECT 'distinct actors',    COUNT(DISTINCT actor_did) FROM post_engagement_events;
