"""Dump per-user durations & gaps from pau_db.sessions to parquet chunks.

Durations: non-singleton sessions only (duration_s > 0) — micro sessions discarded.
Gaps: raw seconds between consecutive sessions (>0); the R fitter shifts by eps.
Chunking: strided, dids[K::10] → data/chunk{K}.parquet (did, col, value).

Usage:
    uv run distribution-fit/dump_data.py --chunk 0
"""

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "table_creation"))
from _core import get_connection, _execute, TBL_PREFIX

BATCH_DIDS = 2000


def main():
    parser = argparse.ArgumentParser(description="Dump sessions to parquet chunks.")
    parser.add_argument("--chunk", type=int, required=True)
    parser.add_argument("--chunks", type=int, default=10)
    args = parser.parse_args()

    out = HERE / "data" / f"chunk{args.chunk}.parquet"
    out.parent.mkdir(exist_ok=True)

    conn = get_connection()
    dids = [r[0] for r in _execute(conn, f"SELECT did FROM {TBL_PREFIX}sessions_users ORDER BY did")]
    dids = dids[args.chunk::args.chunks]
    print(f"chunk {args.chunk}: {len(dids):,} users", file=sys.stderr, flush=True)

    buf_did, buf_col, buf_val = [], [], []
    t0 = time.time()
    for i in range(0, len(dids), BATCH_DIDS):
        batch = dids[i:i + BATCH_DIDS]
        ph = ",".join(["%s"] * len(batch))
        rows = _execute(conn, f"""
            SELECT did, session_start, session_end, duration_s
            FROM {TBL_PREFIX}sessions
            WHERE did IN ({ph}) ORDER BY did, session_start
        """, batch)

        prev_end = {}
        for did, s, e, d in rows:
            if d > 0:  # discard micro sessions (singletons, duration 0)
                buf_did.append(did); buf_col.append("duration"); buf_val.append(float(d))
            if did in prev_end and s > prev_end[did]:
                buf_did.append(did); buf_col.append("gap"); buf_val.append((s - prev_end[did]) / 1e6)
            prev_end[did] = e

        if (i // BATCH_DIDS) % 10 == 0:
            print(f"\r  {i + len(batch):,}/{len(dids):,}", end="", file=sys.stderr, flush=True)

    conn.close()
    pl.DataFrame({"did": buf_did, "col": buf_col, "value": buf_val}).write_parquet(out)
    print(f"\n  → {out} ({len(buf_val):,} rows) in {(time.time()-t0)/60:.0f} min",
          file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
