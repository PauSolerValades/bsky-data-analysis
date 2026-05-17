-- post-lifetime/sql-scripts/populate_post_lifetime.sql
-- =============================================================================
-- Populates pau_db.post_lifetime.
-- Run create_post_lifetime_table.sql first (or migrate_add_first_columns.sql
-- if the table already exists without first_* columns).
--
-- For every TOP-LEVEL post in bsky.posts (reply_root_uri IS NULL, ~15.3M rows),
-- computes for each engagement type:
--   - first time (MIN), last time (MAX), total count
--   - Combined: last_engagement_us = MAX(last_repost, last_like, last_reply)
--               total_engagement   = SUM(counts)
--
-- Run:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < post-lifetime/sql-scripts/populate_post_lifetime.sql
-- =============================================================================

USE pau_db;

INSERT INTO post_lifetime (
    post_did, post_rkey, created_at,
    first_reposted_us, last_reposted_us,
    first_liked_us,    last_liked_us,
    first_replied_us,  last_replied_us,
    last_engagement_us,
    total_reposts, total_likes, total_replies, total_engagement
)

WITH
-- ── Reposts ─────────────────────────────────────────────────────────────────
reposts_agg AS (
    SELECT
        SUBSTRING_INDEX(SUBSTRING_INDEX(subject_uri, '/', 3), '/', -1) AS post_did,
        SUBSTRING_INDEX(subject_uri, '/', -1)                          AS post_rkey,
        COUNT(*)                                                        AS total_reposts,
        MIN(time_us)                                                    AS first_reposted_us,
        MAX(time_us)                                                    AS last_reposted_us
    FROM bsky.records
    WHERE collection = 'app.bsky.feed.repost'
      AND operation   = 'create'
    GROUP BY
        SUBSTRING_INDEX(SUBSTRING_INDEX(subject_uri, '/', 3), '/', -1),
        SUBSTRING_INDEX(subject_uri, '/', -1)
),

-- ── Likes ───────────────────────────────────────────────────────────────────
likes_agg AS (
    SELECT
        SUBSTRING_INDEX(SUBSTRING_INDEX(subject_uri, '/', 3), '/', -1) AS post_did,
        SUBSTRING_INDEX(subject_uri, '/', -1)                          AS post_rkey,
        COUNT(*)                                                        AS total_likes,
        MIN(time_us)                                                    AS first_liked_us,
        MAX(time_us)                                                    AS last_liked_us
    FROM bsky.records
    WHERE collection = 'app.bsky.feed.like'
      AND operation   = 'create'
    GROUP BY
        SUBSTRING_INDEX(SUBSTRING_INDEX(subject_uri, '/', 3), '/', -1),
        SUBSTRING_INDEX(subject_uri, '/', -1)
),

-- ── Direct replies ──────────────────────────────────────────────────────────
replies_agg AS (
    SELECT
        SUBSTRING_INDEX(SUBSTRING_INDEX(reply_parent_uri, '/', 3), '/', -1) AS post_did,
        SUBSTRING_INDEX(reply_parent_uri, '/', -1)                          AS post_rkey,
        COUNT(*)                                                             AS total_replies,
        MIN(time_us)                                                         AS first_replied_us,
        MAX(time_us)                                                         AS last_replied_us
    FROM bsky.posts
    WHERE reply_parent_uri IS NOT NULL
    GROUP BY
        SUBSTRING_INDEX(SUBSTRING_INDEX(reply_parent_uri, '/', 3), '/', -1),
        SUBSTRING_INDEX(reply_parent_uri, '/', -1)
)

-- ── Final assembly ──────────────────────────────────────────────────────────
SELECT
    p.did,
    p.rkey,
    p.created_at,

    -- Repost bounds
    r.first_reposted_us,
    r.last_reposted_us,

    -- Like bounds
    l.first_liked_us,
    l.last_liked_us,

    -- Reply bounds
    rp.first_replied_us,
    rp.last_replied_us,

    -- Combined: latest of any engagement type (NULL if none)
    CASE
        WHEN r.last_reposted_us IS NULL
         AND l.last_liked_us IS NULL
         AND rp.last_replied_us IS NULL
        THEN NULL
        ELSE GREATEST(
            COALESCE(r.last_reposted_us,  0),
            COALESCE(l.last_liked_us,     0),
            COALESCE(rp.last_replied_us,  0)
        )
    END AS last_engagement_us,

    -- Counts
    COALESCE(r.total_reposts,   0) AS total_reposts,
    COALESCE(l.total_likes,     0) AS total_likes,
    COALESCE(rp.total_replies,  0) AS total_replies,

    COALESCE(r.total_reposts,  0)
    + COALESCE(l.total_likes,  0)
    + COALESCE(rp.total_replies, 0) AS total_engagement

FROM bsky.posts p
LEFT JOIN reposts_agg  r  ON p.did = r.post_did  AND p.rkey = r.post_rkey
LEFT JOIN likes_agg    l  ON p.did = l.post_did  AND p.rkey = l.post_rkey
LEFT JOIN replies_agg  rp ON p.did = rp.post_did AND p.rkey = rp.post_rkey
WHERE p.reply_root_uri IS NULL;     -- top-level posts only


-- ── Validation ──────────────────────────────────────────────────────────────

SELECT 'total top-level posts' AS metric, COUNT(*) AS value FROM post_lifetime
UNION ALL
SELECT 'reposted ≥1',   COUNT(*) FROM post_lifetime WHERE total_reposts > 0
UNION ALL
SELECT 'liked ≥1',      COUNT(*) FROM post_lifetime WHERE total_likes > 0
UNION ALL
SELECT 'replied ≥1',    COUNT(*) FROM post_lifetime WHERE total_replies > 0
UNION ALL
SELECT 'any engagement', COUNT(*) FROM post_lifetime WHERE total_engagement > 0
UNION ALL
SELECT 'no engagement',  COUNT(*) FROM post_lifetime WHERE total_engagement = 0;

-- Quick check: first_* should be <= last_* for every post
SELECT 'first > last (repost)' AS issue,
       COUNT(*) FROM post_lifetime
WHERE first_reposted_us IS NOT NULL
  AND first_reposted_us > last_reposted_us
UNION ALL
SELECT 'first > last (like)',
       COUNT(*) FROM post_lifetime
WHERE first_liked_us IS NOT NULL
  AND first_liked_us > last_liked_us
UNION ALL
SELECT 'first > last (reply)',
       COUNT(*) FROM post_lifetime
WHERE first_replied_us IS NOT NULL
  AND first_replied_us > last_replied_us;
