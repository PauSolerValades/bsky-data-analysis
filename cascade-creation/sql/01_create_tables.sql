USE pau_db;

DROP TABLE IF EXISTS cascades;
DROP TABLE IF EXISTS cascade_edges;

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

CREATE TABLE cascade_edges (
    post_uri    VARCHAR(256) NOT NULL,
    actor_did   VARCHAR(128) NOT NULL,
    time_us     BIGINT       NOT NULL,
    parent_did  VARCHAR(128) NOT NULL
)
PRIMARY KEY (post_uri, actor_did, time_us)
DISTRIBUTED BY HASH(post_uri) BUCKETS 32;
