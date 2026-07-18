USE pau_db;

DROP TABLE IF EXISTS broadcast_groups;
DROP TABLE IF EXISTS root_to_leaf_paths;

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
