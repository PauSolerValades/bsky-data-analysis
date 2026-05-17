-- =============================================================================
-- Populate pau_db.user_core_events_human
-- =============================================================================
-- Run create_core_events_human.sql first.
--
-- Selects the 6–500 event range — all real humans:
--   ≥6   → removes tourists   (52.7% of users,  2.1% of gaps)
--   ≤500 → removes heavy bots  (~0.8% of users, 27.7% of gaps)
--
-- Leaves the three meaningful strata: 6–25, 26–100, 101–500.
-- Together they represent ~46% of users and ~70% of gaps.
-- =============================================================================

INSERT INTO pau_db.user_core_events_human (did, time_us, event_type)
SELECT e.did, e.time_us, e.event_type
FROM pau_db.user_core_events e
JOIN (
    SELECT did
    FROM pau_db.user_core_events
    GROUP BY did
    HAVING COUNT(*) >= 6
       AND COUNT(*) <= 500
) eligible ON e.did = eligible.did;
