-- Phase 3: Materialize Parquet → indexed DuckDB database
--
-- Loads the SCD2 Parquet files from Phase 2 into a persistent DuckDB
-- database with indexes for fast point-lookup and graph-traversal queries.
--
-- Output: bsky_topology.db  (~130 GB with indexes)
--
-- Runtime: ~5 minutes (load + index build)
-- You can query the .db as soon as each CREATE TABLE finishes — indexes
-- are created after data load.
--
-- This is optional: you can query the Parquet files directly via
-- read_parquet() without ever running Phase 3. The .db gives you:
--   - Indexed lookups (instant actor/subject queries)
--   - Single-file portability for client delivery
--   - Full DuckDB SQL features (views, macros, transactions)

SET threads = 8;

-- ── Database bootstrap ─────────────────────────────────────────────

-- Run as:  duckdb bsky_topology.db < phase3_materialize.sql

-- ── users ──────────────────────────────────────────────────────────

CREATE OR REPLACE TABLE users AS
FROM read_parquet('data/topology/users.parquet');

SELECT 'Phase 3a: users loaded (' || COUNT(*) || ' rows)' AS status
FROM users;

-- ── follow_edges ───────────────────────────────────────────────────

CREATE OR REPLACE TABLE follow_edges AS
FROM read_parquet('data/topology/follow_edges.parquet');

SELECT 'Phase 3b: follow_edges loaded (' || COUNT(*) || ' rows)' AS status
FROM follow_edges;

CREATE INDEX idx_fe_actor   ON follow_edges(actor_did,   valid_from, valid_to);
CREATE INDEX idx_fe_subject ON follow_edges(subject_did, valid_from, valid_to);

SELECT 'Phase 3b: follow_edges indexes created' AS status;

-- ── block_edges ────────────────────────────────────────────────────

CREATE OR REPLACE TABLE block_edges AS
FROM read_parquet('data/topology/block_edges.parquet');

SELECT 'Phase 3c: block_edges loaded (' || COUNT(*) || ' rows)' AS status
FROM block_edges;

CREATE INDEX idx_be_actor   ON block_edges(actor_did,   valid_from, valid_to);
CREATE INDEX idx_be_subject ON block_edges(subject_did, valid_from, valid_to);

SELECT 'Phase 3c: block_edges indexes created' AS status;

-- ── Final stats ────────────────────────────────────────────────────

SELECT 'users'         AS tbl, COUNT(*) AS rows FROM users
UNION ALL
SELECT 'follow_edges',  COUNT(*) FROM follow_edges
UNION ALL
SELECT 'block_edges',   COUNT(*) FROM block_edges
UNION ALL
SELECT 'active_follows', COUNT(*) FROM follow_edges WHERE valid_to IS NULL
UNION ALL
SELECT 'active_blocks',  COUNT(*) FROM block_edges  WHERE valid_to IS NULL;

SELECT 'Phase 3 complete: bsky_topology.db ready.' AS status;
