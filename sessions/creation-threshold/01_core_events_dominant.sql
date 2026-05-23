-- =============================================================================
-- 01_core_events_dominant.sql
-- =============================================================================
-- Creates and populates pau_db.user_core_events_dominant — the 101–500 event
-- bucket (96K users, 37.4% of all inter-arrival gaps).  Used exclusively by
-- the fixed-threshold session pipeline.
--
-- This is a strict subset of user_core_events (1.75M) and user_core_events_human
-- (815K, 6–500).  The EDA identified this stratum as the one that dominates the
-- gap histogram — the elbow is effectively this cohort's threshold.
--
-- Prerequisites: pau_db.user_core_events must exist
--   (run ../session-creation-tukey/01_core_events.sql if you haven't).
--
-- Usage:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < 01_core_events_dominant.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS pau_db.user_core_events_dominant (
    `did`        varchar(128) NOT NULL,
    `time_us`    bigint       NOT NULL,
    `event_type` varchar(16)  NOT NULL   -- 'post', 'reply', 'repost'
) ENGINE = OLAP
DUPLICATE KEY(`did`, `time_us`)
DISTRIBUTED BY HASH(`did`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1"
);

INSERT INTO pau_db.user_core_events_dominant (did, time_us, event_type)
SELECT e.did, e.time_us, e.event_type
FROM pau_db.user_core_events e
JOIN (
    SELECT did
    FROM pau_db.user_core_events
    GROUP BY did
    HAVING COUNT(*) >= 101
       AND COUNT(*) <= 500
) eligible ON e.did = eligible.did;
