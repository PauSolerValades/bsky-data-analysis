#!/usr/bin/env bash
# Phase 1: Export graph_events from StarRocks → chunked Parquet files
#
# Splits the export into weekly chunks to stay under StarRocks query_timeout.
# Supports parallelism and resume (skips already-exported chunks).
#
# Usage:
#   chmod +x phase1_export.sh
#   ./phase1_export.sh                    # default: 4 workers
#   ./phase1_export.sh 8                  # 8 parallel workers
#
# Output: graph_events_YYYY-MM-DD.parquet (~70 files, ~64 GB total)
# Log:    phase1.log

set -euo pipefail

WORKERS="${1:-4}"
START_DATE="2025-02-01"
END_DATE="2026-05-31"

# Absolute paths so subshells find everything
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/logs/phase1.log"
DUCKDB_BIN="${HOME}/.local/bin/duckdb"

# StarRocks (exported for subshells)
export SR_HOST="10.18.74.14"
export SR_PORT="9030"
export SR_USER="pau"
export SR_PASS="regulate-evil-decode"
export SR_DB="bsky_topology"
export DUCKDB_BIN
export LOG_FILE
export SCRIPT_DIR

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# ── Export one chunk ─────────────────────────────────────────────────
export_chunk() {
    local d_start="$1"
    local d_end="$2"
    local fname="${SCRIPT_DIR}/data/raw/graph_events_${d_start}.parquet"

    # Skip if already done and non-empty
    if [[ -f "$fname" ]] && [[ -s "$fname" ]]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] SKIP $d_start → $d_end (already exists)" | tee -a "$LOG_FILE"
        return 0
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] START $d_start → $d_end" | tee -a "$LOG_FILE"

    "$DUCKDB_BIN" -c "
    INSTALL mysql; LOAD mysql;
    ATTACH 'host=${SR_HOST} port=${SR_PORT} user=${SR_USER} password=${SR_PASS} database=${SR_DB}' AS sr (TYPE mysql);
    CALL mysql_execute('sr', 'SET query_timeout = 7200');
    COPY (SELECT * FROM sr.graph_events
          WHERE event_timestamp >= '${d_start}' AND event_timestamp < '${d_end}')
    TO '${fname}'
      (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 1000000, OVERWRITE_OR_IGNORE true);
    " 2>&1 | tee -a "$LOG_FILE"

    if [[ -f "$fname" ]] && [[ -s "$fname" ]]; then
        local sz; sz=$(du -h "$fname" | cut -f1)
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] DONE  $d_start → $d_end ($sz)" | tee -a "$LOG_FILE"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAIL  $d_start → $d_end (no output file)" | tee -a "$LOG_FILE"
    fi
}

export -f export_chunk

# ── Generate weekly date ranges ──────────────────────────────────────
gen_ranges() {
    local cur="$START_DATE"
    while [[ "$cur" < "$END_DATE" ]]; do
        local next
        next=$(date -d "$cur + 7 days" +%Y-%m-%d)
        echo "$cur $next"
        cur="$next"
    done
}

# ── Main ─────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"

echo "" > "$LOG_FILE"
log "Phase 1 started — $WORKERS workers, $START_DATE → $END_DATE"
log "DuckDB: $DUCKDB_BIN"

total_chunks=$(gen_ranges | wc -l)
log "Total chunks: $total_chunks"

# Export via xargs with parallelism.
# xargs -P N spawns N parallel bash processes, each sourcing a chunk.
gen_ranges | xargs -P "$WORKERS" -n 2 bash -c '
    d_start="$1"
    d_end="$2"
    export_chunk "$d_start" "$d_end"
' _

log "Phase 1 complete."

# ── Summary ──────────────────────────────────────────────────────────
log "Files written:"
ls -lh "${SCRIPT_DIR}/data/raw/graph_events_"*.parquet 2>/dev/null | awk '{print $5, $NF}' | tee -a "$LOG_FILE" || log "(none yet)"
