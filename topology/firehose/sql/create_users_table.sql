-- topology-crawl/sql-scripts/create_users_table.sql
-- =============================================================================
-- Creates and populates `pau_db.users` — a per-user summary table derived from
-- the `bsky` firehose database.
--
-- Columns:
--   did            User's decentralized identifier (PK)
--   num_posts      Total posts authored (from bsky.posts)
--   num_likes      Total likes given (from bsky.records)
--   num_reposts    Total reposts given (from bsky.records)
--   num_follows    Total follows given (from bsky.records) — outbound, NOT follower count
--   first_seen_us  Earliest activity timestamp (µs epoch)
--   last_seen_us   Latest activity timestamp (µs epoch)
--   primary_lang   Most frequent language of their posts (NULL if no posts / no lang tag)
--   created_at     When this row was inserted
--
-- ⚠️  WARNING: user `pau` cannot DROP tables in StarRocks.
--    CREATE TABLE IF NOT EXISTS will only succeed if the table does not exist yet.
--    If the table already exists (e.g., a failed previous attempt left an empty
--    or partial table), you MUST ask a DBA to drop it before re-running.
--
--    The INSERT is a single all-or-nothing query — if it fails mid-way the
--    table will be empty (no partial data).
--
-- Run:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < topology-crawl/sql-scripts/create_users_table.sql
--   (password: regulate-evil-decode)

USE pau_db;

-- ── Table definition ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    did           VARCHAR(128) NOT NULL COMMENT 'User decentralized identifier',
    num_posts     BIGINT       NOT NULL COMMENT 'Total posts authored',
    num_likes     BIGINT       NOT NULL COMMENT 'Total likes given (app.bsky.feed.like)',
    num_reposts   BIGINT       NOT NULL COMMENT 'Total reposts given (app.bsky.feed.repost)',
    num_follows   BIGINT       NOT NULL COMMENT 'Total follows given — outbound, NOT follower count',
    first_seen_us BIGINT       NOT NULL COMMENT 'Earliest activity timestamp (µs since epoch)',
    last_seen_us  BIGINT       NOT NULL COMMENT 'Latest activity timestamp (µs since epoch)',
    primary_lang  VARCHAR(16)  NULL COMMENT 'Most frequent language of their posts',
    created_at    DATETIME     NOT NULL COMMENT 'Row insertion time'
);

-- ── Populate with a single INSERT … SELECT ──────────────────────────────────

INSERT INTO users (did, num_posts, num_likes, num_reposts, num_follows,
                   first_seen_us, last_seen_us, primary_lang, created_at)
WITH
-- All distinct DIDs appearing in either table
all_dids AS (
    SELECT DISTINCT did FROM bsky.posts
    UNION
    SELECT DISTINCT did FROM bsky.records
),

-- Per-DID post aggregates (single pass over bsky.posts)
post_stats AS (
    SELECT
        did,
        COUNT(*)    AS num_posts,
        MIN(time_us) AS p_first_us,
        MAX(time_us) AS p_last_us
    FROM bsky.posts
    GROUP BY did
),

-- Per-DID record aggregates (single pass over bsky.records).
-- Counts only the three core collections, but time bounds span ALL collections
-- so users who only appear in e.g. blocks or profiles still get a first/last_seen_us.
record_stats AS (
    SELECT
        did,
        COUNT(CASE WHEN collection = 'app.bsky.feed.like'   THEN 1 END) AS num_likes,
        COUNT(CASE WHEN collection = 'app.bsky.feed.repost'  THEN 1 END) AS num_reposts,
        COUNT(CASE WHEN collection = 'app.bsky.graph.follow' THEN 1 END) AS num_follows,
        MIN(time_us) AS r_first_us,
        MAX(time_us) AS r_last_us
    FROM bsky.records
    GROUP BY did
),

-- Most frequent language per user (users with no lang-tagged posts get NULL)
primary_langs AS (
    SELECT did, lang AS primary_lang
    FROM (
        SELECT
            did,
            lang,
            ROW_NUMBER() OVER (PARTITION BY did ORDER BY cnt DESC) AS rn
        FROM (
            SELECT did, lang, COUNT(*) AS cnt
            FROM bsky.posts
            WHERE lang IS NOT NULL
            GROUP BY did, lang
        ) t
    ) ranked
    WHERE rn = 1
)

SELECT
    d.did,
    COALESCE(p.num_posts,  0) AS num_posts,
    COALESCE(r.num_likes,   0) AS num_likes,
    COALESCE(r.num_reposts, 0) AS num_reposts,
    COALESCE(r.num_follows, 0) AS num_follows,

    -- Earliest timestamp: take the min of whichever sources exist.
    -- LEAST(a, b) where a and b are the same non-null value when one side is
    -- missing → this correctly handles users present in only one of the tables.
    LEAST(
        COALESCE(p.p_first_us, r.r_first_us),
        COALESCE(r.r_first_us, p.p_first_us)
    ) AS first_seen_us,

    GREATEST(
        COALESCE(p.p_last_us, r.r_last_us),
        COALESCE(r.r_last_us, p.p_last_us)
    ) AS last_seen_us,

    pl.primary_lang,
    NOW() AS created_at

FROM all_dids d
LEFT JOIN post_stats   p  ON d.did = p.did
LEFT JOIN record_stats r  ON d.did = r.did
LEFT JOIN primary_langs pl ON d.did = pl.did;


-- ── Validation queries ──────────────────────────────────────────────────────
-- Run these manually after the INSERT completes to sanity-check the results.

-- Expected: ~2.8–3.1 million rows
SELECT COUNT(*) AS total_users FROM users;

-- Should be 0 (every user appears in at least one source table)
SELECT COUNT(*) AS users_with_no_activity
FROM users
WHERE num_posts = 0 AND num_likes = 0 AND num_reposts = 0 AND num_follows = 0;

-- Quick distribution check
SELECT
    MIN(num_posts)   AS min_posts,   MAX(num_posts)   AS max_posts,
    MIN(num_likes)   AS min_likes,   MAX(num_likes)   AS max_likes,
    MIN(num_reposts) AS min_reposts, MAX(num_reposts) AS max_reposts,
    MIN(num_follows) AS min_follows, MAX(num_follows) AS max_follows
FROM users;

-- Top 10 languages
SELECT primary_lang, COUNT(*) AS users
FROM users
GROUP BY primary_lang
ORDER BY users DESC
LIMIT 10;

-- Users with no language (no posts or no lang tags)
SELECT COUNT(*) AS users_without_lang FROM users WHERE primary_lang IS NULL;
