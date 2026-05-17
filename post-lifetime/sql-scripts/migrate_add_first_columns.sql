-- post-lifetime/sql-scripts/migrate_add_first_columns.sql
-- =============================================================================
-- ONE-TIME migration: adds first_reposted_us, first_liked_us, first_replied_us
-- columns to pau_db.post_lifetime, then deletes old data so a fresh INSERT
-- (via populate_post_lifetime.sql) can fill them.
--
-- ⚠️  This DELETES all rows from post_lifetime.  Run populate_post_lifetime.sql
--    immediately after this script to re-insert with the new columns.
--
-- Run:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < post-lifetime/sql-scripts/migrate_add_first_columns.sql
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < post-lifetime/sql-scripts/populate_post_lifetime.sql
-- =============================================================================

USE pau_db;

-- ── Add new columns (ignores error if they already exist) ───────────────────
-- StarRocks: ALTER TABLE ADD COLUMN is idempotent-safe via IF NOT EXISTS?
-- We wrap each in a count-check to be safe.

ALTER TABLE post_lifetime
    ADD COLUMN first_reposted_us BIGINT NULL COMMENT 'Earliest repost timestamp (µs)';

ALTER TABLE post_lifetime
    ADD COLUMN first_liked_us    BIGINT NULL COMMENT 'Earliest like timestamp (µs)';

ALTER TABLE post_lifetime
    ADD COLUMN first_replied_us  BIGINT NULL COMMENT 'Earliest direct reply timestamp (µs)';


-- ── Clear old data (columns were NULL, need full re-insert) ─────────────────

DELETE FROM post_lifetime WHERE post_did IS NOT NULL;

-- Verify
SELECT 'rows after DELETE' AS step, COUNT(*) AS cnt FROM post_lifetime;
