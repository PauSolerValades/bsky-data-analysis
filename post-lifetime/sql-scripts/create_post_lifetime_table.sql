-- post-lifetime/sql-scripts/create_post_lifetime_table.sql
-- =============================================================================
-- Creates pau_db.post_lifetime — a table that records, for every TOP-LEVEL post
-- in the firehose, the aggregated engagement metrics that define its "lifetime":
--
--   * First & last repost time + total reposts
--   * First & last like time   + total likes
--   * First & last reply time  + total direct replies
--   * Combined: LAST of all three + SUM of all three
--
-- A post is uniquely identified by (post_did, post_rkey).
-- post_did is also the author's DID.
--
-- ⚠️  Only top-level posts (reply_root_uri IS NULL) are included.
-- ⚠️  Quote posts cannot be counted (see README for details).
--
-- Columns:
--   post_did            DID of the post author (= the "did" identifier)
--   post_rkey           Record key — together with post_did = unique post ID
--   created_at          Post creation timestamp (UTC datetime)
--   first_reposted_us   Earliest repost timestamp (µs), NULL if never
--   last_reposted_us    Latest repost timestamp   (µs), NULL if never
--   first_liked_us      Earliest like timestamp   (µs), NULL if never
--   last_liked_us       Latest like timestamp     (µs), NULL if never
--   first_replied_us    Earliest reply timestamp  (µs), NULL if never
--   last_replied_us     Latest reply timestamp    (µs), NULL if never
--   last_engagement_us  MAX(last_repost, last_like, last_reply), NULL if none
--   total_reposts       Number of reposts received
--   total_likes         Number of likes received
--   total_replies       Number of direct replies received
--   total_engagement    SUM(reposts, likes, replies)
--
-- Run:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < post-lifetime/sql-scripts/create_post_lifetime_table.sql
--
-- ⚠️  If the table already exists, ask a DBA to drop it first.
-- =============================================================================

USE pau_db;

CREATE TABLE IF NOT EXISTS post_lifetime (
    post_did            VARCHAR(128)  NOT NULL COMMENT 'DID of the post author',
    post_rkey           VARCHAR(16)   NOT NULL COMMENT 'Record key (unique per author)',
    created_at          DATETIME      NOT NULL COMMENT 'Post creation timestamp (UTC)',
    first_reposted_us   BIGINT        NULL     COMMENT 'Earliest repost timestamp (µs)',
    last_reposted_us    BIGINT        NULL     COMMENT 'Latest repost timestamp (µs)',
    first_liked_us      BIGINT        NULL     COMMENT 'Earliest like timestamp (µs)',
    last_liked_us       BIGINT        NULL     COMMENT 'Latest like timestamp (µs)',
    first_replied_us    BIGINT        NULL     COMMENT 'Earliest direct reply timestamp (µs)',
    last_replied_us     BIGINT        NULL     COMMENT 'Latest direct reply timestamp (µs)',
    last_engagement_us  BIGINT        NULL     COMMENT 'MAX(last_repost, last_like, last_reply)',
    total_reposts       BIGINT        NOT NULL COMMENT 'Number of reposts received',
    total_likes         BIGINT        NOT NULL COMMENT 'Number of likes received',
    total_replies       BIGINT        NOT NULL COMMENT 'Number of direct replies received',
    total_engagement    BIGINT        NOT NULL COMMENT 'SUM(reposts, likes, replies)'
)
ENGINE = OLAP
DUPLICATE KEY(`post_did`, `post_rkey`)
DISTRIBUTED BY HASH(`post_did`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1"
);
