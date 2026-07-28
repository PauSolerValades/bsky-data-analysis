#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pymysql", "python-dotenv", "numpy"]
# ///
"""
Tukey session clustering — one run, one table.

Usage:
    uv run session-creation/run_tukey.py --k 1.5 --did-from-file sample_dids.txt --summary
    uv run session-creation/run_tukey.py --k 1.2 --summary
"""

import argparse
import sys

import numpy as np
import pymysql

from _core import get_connection, load_dids, run_batch_loop


# ---------------------------------------------------------------------------
# Tukey clustering
# ---------------------------------------------------------------------------

def _tukey_cluster(timestamps_us: list[int], k: float = 1.5):
    """Tukey: Q3 + k × IQR, with dedup. Returns (start, end, is_singleton)."""
    if len(timestamps_us) < 5:
        return None
    unique = sorted(set(timestamps_us))
    if len(unique) < 5:
        return None

    gaps = np.diff(np.array(unique, dtype=np.int64))
    q1, q3 = np.percentile(gaps, [25, 75])
    threshold = int(q3 + k * (q3 - q1))

    sessions = []
    cur_start = unique[0]
    cur_end = unique[0]
    for i in range(1, len(unique)):
        t = unique[i]
        if t - unique[i - 1] > threshold:
            sessions.append((cur_start, cur_end, False))
            cur_start = t
        cur_end = t
    sessions.append((cur_start, cur_end, False))
    return sessions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Tukey session clustering.")
    parser.add_argument("--k", type=float, default=1.5, help="IQR multiplier")
    parser.add_argument("--did-from-file", type=str, default=None)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    k = args.k
    k_str = str(k).replace(".", "_")
    target = f"pau_db.sessions_raw_tukey_k{k_str}"

    print(f"Tukey k={k} → {target}", file=sys.stderr)
    conn = get_connection()

    try:
        if args.did_from_file:
            from pathlib import Path
            dids = Path(args.did_from_file).read_text().strip().splitlines()
            print(f"  → {len(dids):,} DIDs from file", file=sys.stderr)
        else:
            dids = load_dids(conn)
            print(f"  → {len(dids):,} DIDs from DB", file=sys.stderr)

        if not dids:
            print("No DIDs found.", file=sys.stderr)
            return

        run_batch_loop(
            conn, target, dids,
            cluster_fn=lambda ts: _tukey_cluster(ts, k=k),
            batch_size=args.batch_size,
            summary=args.summary,
        )
    except pymysql.Error as e:
        print(f"\nDatabase error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
        print("Connection closed.", file=sys.stderr)


if __name__ == "__main__":
    main()
