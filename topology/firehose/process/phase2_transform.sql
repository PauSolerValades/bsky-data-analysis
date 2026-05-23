-- Phase 2: Transform data/raw/graph_events_*.parquet → SCD2 Parquet files
--
-- Reads the chunked raw event log (from Phase 1) and builds Slowly Changing
-- Dimension Type 2 tables via a simple GROUP BY on `uri`. Since each AT
-- Protocol record has a unique URI and at most one create + one delete event,
-- no window functions or state machines are needed.
--
-- Input:  data/raw/graph_events_*.parquet  (weekly chunks from Phase 1)
-- Output: data/topology/follow_edges.parquet    (~1.47B rows, ~67 GB)
--         data/topology/block_edges.parquet     (~117M rows, ~5.6 GB)
--         data/topology/users.parquet           (~29M DIDs, ~1.3 GB)
--
-- Runtime: ~25 minutes (hash aggregation, parallel reads, single-threaded write)
--
-- Memory: the GROUP BY hash table for ~830M follow URIs needs ~33 GB.
-- Hash-table spilling to disk is handled automatically by DuckDB.

SET threads = 16;

------------------------------------------------------------------------
-- follow_edges
------------------------------------------------------------------------

COPY (
    SELECT
        uri,
        MAX(actor_did)                                              AS actor_did,
        MAX(subject_did)                                            AS subject_did,
        MIN(event_timestamp)                                        AS valid_from,
        NULLIF(MAX(event_timestamp), MIN(event_timestamp))          AS valid_to
    FROM read_parquet('data/raw/graph_events_*.parquet')
    WHERE action_type IN ('follow', 'unfollow')
    GROUP BY uri
    HAVING COUNT_IF(action_type = 'follow') > 0    -- require a create event
) TO 'data/topology/follow_edges.parquet'
  (FORMAT PARQUET,
   COMPRESSION ZSTD,
   ROW_GROUP_SIZE 1000000,
   OVERWRITE_OR_IGNORE true);

SELECT 'Phase 2a complete: follow_edges.parquet' AS status;

------------------------------------------------------------------------
-- block_edges
------------------------------------------------------------------------

COPY (
    SELECT
        uri,
        MAX(actor_did)                                              AS actor_did,
        MAX(subject_did)                                            AS subject_did,
        MIN(event_timestamp)                                        AS valid_from,
        NULLIF(MAX(event_timestamp), MIN(event_timestamp))          AS valid_to
    FROM read_parquet('data/raw/graph_events_*.parquet')
    WHERE action_type IN ('block', 'unblock')
    GROUP BY uri
    HAVING COUNT_IF(action_type = 'block') > 0
) TO 'data/topology/block_edges.parquet'
  (FORMAT PARQUET,
   COMPRESSION ZSTD,
   ROW_GROUP_SIZE 1000000,
   OVERWRITE_OR_IGNORE true);

SELECT 'Phase 2b complete: block_edges.parquet' AS status;

------------------------------------------------------------------------
-- users
------------------------------------------------------------------------

COPY (
    SELECT
        did,
        MIN(ts)           AS first_seen_at,
        MIN_BY(uri, ts)   AS first_seen_uri
    FROM (
        SELECT actor_did AS did, event_timestamp AS ts, uri
        FROM read_parquet('data/raw/graph_events_*.parquet')
        WHERE action_type IN ('follow', 'unfollow', 'block', 'unblock')
          AND actor_did IS NOT NULL
          AND actor_did != ''

        UNION ALL

        SELECT subject_did AS did, event_timestamp AS ts, uri
        FROM read_parquet('data/raw/graph_events_*.parquet')
        WHERE action_type IN ('follow', 'unfollow', 'block', 'unblock')
          AND subject_did IS NOT NULL
          AND subject_did != ''
    )
    GROUP BY did
) TO 'data/topology/users.parquet'
  (FORMAT PARQUET,
   COMPRESSION ZSTD,
   ROW_GROUP_SIZE 1000000,
   OVERWRITE_OR_IGNORE true);

SELECT 'Phase 2c complete: users.parquet' AS status;

------------------------------------------------------------------------
-- Quick sanity checks
------------------------------------------------------------------------

SELECT 'follow_edges  rows' AS metric, COUNT(*) AS value FROM read_parquet('data/topology/follow_edges.parquet')
UNION ALL
SELECT 'block_edges   rows', COUNT(*) FROM read_parquet('data/topology/block_edges.parquet')
UNION ALL
SELECT 'users         rows', COUNT(*) FROM read_parquet('data/topology/users.parquet')
UNION ALL
SELECT 'active follows', COUNT(*) FROM read_parquet('data/topology/follow_edges.parquet') WHERE valid_to IS NULL
UNION ALL
SELECT 'active blocks',  COUNT(*) FROM read_parquet('data/topology/block_edges.parquet')  WHERE valid_to IS NULL;

SELECT 'Phase 2 complete: SCD2 Parquet files ready.' AS status;
