-- =============================================================================
-- Populate pau_db.user_core_events_dominant
-- =============================================================================
-- Run create_core_events_dominant.sql first.
--
-- Selects only the 101–500 event range — the dominant stratum.
-- 5.5% of users, 37.4% of all gaps.
-- =============================================================================

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
