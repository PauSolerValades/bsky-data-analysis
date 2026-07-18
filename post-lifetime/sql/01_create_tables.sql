USE pau_db;

DROP TABLE IF EXISTS repost_gaps;
DROP TABLE IF EXISTS post_lifetime;

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
