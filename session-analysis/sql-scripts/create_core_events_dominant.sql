-- =============================================================================
-- Create pau_db.user_core_events_dominant
-- =============================================================================
-- Filtered core-event table for the FIXED-THRESHOLD ELBOW method.
--
-- Contains ONLY the 101–500 event bucket, which supplies 37.4% of all
-- inter-arrival gaps and dominates the elbow computation.
--
-- Rationale (from EDA §5):
--   • 1–5 events   → 52.7% of users,  2.1% of gaps → invisible noise
--   • 6–100 events  → 41.1% of users, 32.9% of gaps → meaningful but not dominant
--   • 101–500       →  5.5% of users, 37.4% of gaps → DOMINANT — THIS is the elbow
--   • 501+          →  0.8% of users, 27.7% of gaps → bots compress elbow downward
--
-- Running the elbow on this stratum alone gives the threshold that
-- actually matters — the one the dominant user cohort exhibits.
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
