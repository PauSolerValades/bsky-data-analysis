#!/bin/bash
# Phase 0: Export active edges from Parquet → integer-ID binary format for Go.
#
# Strategy:
#   1. Extract all unique DIDs → dids.txt (sorted)
#   2. Build a DuckDB table from dids.txt with rowid
#   3. Join active edges against the DID table to get integer IDs
#   4. Convert to binary int64 pairs
#
# Output files in topology/sampling-go/data/:
#   dids.txt   — sorted DIDs, one per line (line 0 = int_id 0)
#   edges.bin  — all edges as int64 pairs (actor_id, subject_id)
#   meta.json  — {num_nodes, num_edges}

set -euo pipefail
cd "$(dirname "$0")"

DATA_DIR="./data"
PARQUET="../firehose/process/data/topology/follow_edges.parquet"

rm -rf "${DATA_DIR}"
mkdir -p "${DATA_DIR}"

echo "=== Phase 0: Export active edges for Go ==="

# ── Step 1: Build DID → int mapping ───────────────────────────────────
echo "[1/4] Building DID mapping ..."
duckdb -c "
COPY (
    SELECT did FROM (
        SELECT DISTINCT CAST(actor_did AS VARCHAR) AS did
        FROM read_parquet('${PARQUET}') WHERE valid_to IS NULL
        UNION
        SELECT DISTINCT CAST(subject_did AS VARCHAR) AS did
        FROM read_parquet('${PARQUET}') WHERE valid_to IS NULL
    ) t
    ORDER BY did
) TO '${DATA_DIR}/dids.txt' (FORMAT CSV, HEADER false);
"

NUM_DIDS=$(wc -l < "${DATA_DIR}/dids.txt")
echo "  ${NUM_DIDS} unique DIDs"

# ── Step 2: Import DID mapping into DuckDB ─────────────────────────────
echo "[2/4] Importing DID mapping into DuckDB ..."
duckdb "${DATA_DIR}/temp.db" -c "
CREATE TABLE dids AS
SELECT did, row_number() OVER () - 1 AS id
FROM read_csv('${DATA_DIR}/dids.txt', columns={did:'VARCHAR'});
"

# ── Step 3: Join edges → write CSV ────────────────────────────────────
echo "[3/4] Joining edges with DID IDs ..."
duckdb "${DATA_DIR}/temp.db" -c "
COPY (
    SELECT da.id AS actor_id, ds.id AS subject_id
    FROM read_parquet('${PARQUET}') e
    JOIN dids da ON CAST(e.actor_did AS VARCHAR) = da.did
    JOIN dids ds ON CAST(e.subject_did AS VARCHAR) = ds.did
    WHERE e.valid_to IS NULL
) TO '${DATA_DIR}/edges_raw.csv' (FORMAT CSV, HEADER false, DELIMITER '|');
"

NUM_EDGES=$(wc -l < "${DATA_DIR}/edges_raw.csv")
echo "  ${NUM_EDGES} edges exported (CSV)"

# ── Step 4: Convert CSV → binary int64 pairs ──────────────────────────
echo "[4/4] Converting to binary int64 pairs ..."
python3 -c "
import struct

with open('${DATA_DIR}/edges_raw.csv', 'r') as f_in, \
     open('${DATA_DIR}/edges.bin', 'wb') as f_out:
    for line in f_in:
        a, s = line.strip().split('|')
        f_out.write(struct.pack('<qq', int(a), int(s)))

import json
with open('${DATA_DIR}/meta.json', 'w') as f:
    json.dump({'num_nodes': ${NUM_DIDS}, 'num_edges': ${NUM_EDGES}}, f)
"

rm -f "${DATA_DIR}/edges_raw.csv" "${DATA_DIR}/temp.db" "${DATA_DIR}/temp.db.wal"

SIZE=$(du -h "${DATA_DIR}/edges.bin" | cut -f1)
echo ""
echo "=== Phase 0 complete ==="
echo "  data/dids.txt  — ${NUM_DIDS} DIDs"
echo "  data/edges.bin — ${NUM_EDGES} edges (${SIZE})"
echo "  data/meta.json"
