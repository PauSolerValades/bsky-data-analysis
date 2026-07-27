-- Rename all_events_v2 → events, drop old event tables

USE pau_db;

-- Drop old tables
DROP TABLE IF EXISTS all_events;
DROP TABLE IF EXISTS engaged_events;

-- Rename
ALTER TABLE all_events_v2 RENAME events;
