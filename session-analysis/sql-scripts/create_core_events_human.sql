-- =============================================================================
-- Create pau_db.user_core_events_human
-- =============================================================================
-- Filtered core-event table for PER-USER ADAPTIVE (IQR) session clustering.
--
-- Contains the 6–500 event range — all non-tourist, non-bot humans:
--   • ≥6 events  → removes 52.7% tourists (invisible noise, too few gaps for IQR)
--   • ≤500 events → removes ~0.8% heavy bots (>62/day, 27.7% of gaps)
--
-- Unlike the elbow method (which only needs the dominant 101–500 stratum),
-- the IQR method benefits from the full human range: the 6–25 casuals,
-- the 26–100 regulars, and the 101–500 power users. IQR adapts per-user
-- so different rhythms are handled naturally.
--
-- Same schema as user_core_events — just a filtered subset.
-- =============================================================================

CREATE TABLE IF NOT EXISTS pau_db.user_core_events_human (
    `did`        varchar(128) NOT NULL,
    `time_us`    bigint       NOT NULL,
    `event_type` varchar(16)  NOT NULL   -- 'post', 'reply', 'repost'
) ENGINE = OLAP
DUPLICATE KEY(`did`, `time_us`)
DISTRIBUTED BY HASH(`did`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1"
);
