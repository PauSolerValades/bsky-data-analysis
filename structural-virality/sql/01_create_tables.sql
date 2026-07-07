-- Create all cascade + lifetime tables in StarRocks.
-- Run once:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < structural-virality/sql/01_create_tables.sql

USE pau_db;

DROP TABLE IF EXISTS cascades;
DROP TABLE IF EXISTS broadcast_groups;
DROP TABLE IF EXISTS root_to_leaf_paths;
DROP TABLE IF EXISTS post_lifetime;
DROP TABLE IF EXISTS repost_gaps;

-- Cascade-level metrics (1 row per original post)
CREATE TABLE cascades (
    post_uri            VARCHAR(256) NOT NULL,
    author_did          VARCHAR(128) NOT NULL,
    creation_time_us    BIGINT       NOT NULL,
    cascade_size        INT          NOT NULL,
    cascade_depth       INT          NOT NULL,
    max_out_degree      INT          NOT NULL,
    structural_virality DOUBLE       NOT NULL
)
PRIMARY KEY (post_uri)
DISTRIBUTED BY HASH(post_uri) BUCKETS 32;

-- Per-parent broadcast analysis (1 row per node with ≥1 child)
CREATE TABLE broadcast_groups (
    post_uri            VARCHAR(256) NOT NULL,
    parent_did          VARCHAR(128) NOT NULL,
    broadcast_size      INT          NOT NULL,
    mean_gap_us         DOUBLE       NOT NULL,
    median_gap_us       DOUBLE       NOT NULL,
    gap_trend           DOUBLE       NOT NULL,
    first_child_time_us BIGINT       NOT NULL,
    last_child_time_us  BIGINT       NOT NULL
)
PRIMARY KEY (post_uri, parent_did)
DISTRIBUTED BY HASH(post_uri) BUCKETS 32;

-- Root-to-leaf path analysis (1 row per leaf node)
CREATE TABLE root_to_leaf_paths (
    post_uri            VARCHAR(256) NOT NULL,
    leaf_did            VARCHAR(128) NOT NULL,
    path_depth           INT          NOT NULL,
    path_total_time_us  DOUBLE       NOT NULL,
    traversal_speed     DOUBLE       NOT NULL,
    gap_trend           DOUBLE       NOT NULL
)
PRIMARY KEY (post_uri, leaf_did)
DISTRIBUTED BY HASH(post_uri) BUCKETS 32;

-- Post lifetime percentiles (1 row per original post with ≥1 repost)
CREATE TABLE post_lifetime (
    post_uri            VARCHAR(256) NOT NULL,
    author_did          VARCHAR(128) NOT NULL,
    creation_time_us    BIGINT       NOT NULL,
    last_repost_time_us BIGINT       NOT NULL,
    total_reposts       INT          NOT NULL,
    T_50_us             DOUBLE       NOT NULL,
    T_95_us             DOUBLE       NOT NULL,
    T_99_us             DOUBLE       NOT NULL,
    time_to_peak_us     DOUBLE       NOT NULL
)
PRIMARY KEY (post_uri)
DISTRIBUTED BY HASH(post_uri) BUCKETS 32;

-- Per-repost gaps (1 row per repost event)
CREATE TABLE repost_gaps (
    post_uri            VARCHAR(256) NOT NULL,
    reposter_did        VARCHAR(128) NOT NULL,
    repost_time_us      BIGINT       NOT NULL,
    parent_did          VARCHAR(128) NOT NULL,
    global_gap_us       DOUBLE       NOT NULL,
    topology_gap_us     DOUBLE       NOT NULL
)
PRIMARY KEY (post_uri, reposter_did, repost_time_us)
DISTRIBUTED BY HASH(post_uri) BUCKETS 32;
