"""Tukey session clustering — IQR-based outlier threshold.

Output table: (did, session_start, session_end, duration_s, is_singleton).

Usage:
    uv run sessions/table_creation/tukey.py --table-name sessions_tukey_k1_5 --k 1.5 --summary
"""

import argparse
import sys
from functools import partial

import numpy as np

from _core import get_connection, load_dids, run_batch_loop


# ── Clustering ────────────────────────────────────────────────────────────

def _tukey_cluster(timestamps_us: list[int], k: float = 1.5):
    """Tukey: Q3 + k × IQR on inter-event gaps.

    Input: timestamps in microseconds. Converted to seconds internally.
    Returns: (start_us, end_us, is_singleton) in microseconds.
    """
    if len(timestamps_us) < 5:
        return None
    unique_s = sorted(set(t / 1_000_000 for t in timestamps_us))
    if len(unique_s) < 5:
        return None

    gaps = np.diff(np.array(unique_s, dtype=np.float64))
    q1, q3 = np.percentile(gaps, [25, 75])
    threshold = q3 + k * (q3 - q1)

    sessions = []
    cur_start = unique_s[0]
    cur_end = unique_s[0]
    for i in range(1, len(unique_s)):
        t = unique_s[i]
        if t - unique_s[i - 1] > threshold:
            sessions.append((int(cur_start * 1_000_000), int(cur_end * 1_000_000), False))
            cur_start = t
        cur_end = t
    sessions.append((int(cur_start * 1_000_000), int(cur_end * 1_000_000), False))
    return sessions


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tukey session clustering.")
    parser.add_argument("--table-name", type=str, required=True,
                        help="Output table name (e.g., sessions_tukey_k1_5)")
    parser.add_argument("--k", type=float, default=1.5, help="IQR multiplier")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel workers for clustering")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    print(f"Tukey k={args.k} → {args.table_name}", file=sys.stderr)
    conn = get_connection()

    try:
        dids = load_dids(conn)
        print(f"  → {len(dids):,} DIDs from DB", file=sys.stderr)
        if not dids:
            print("No DIDs found.", file=sys.stderr)
            return

        run_batch_loop(
            conn, args.table_name, dids,
            cluster_fn=partial(_tukey_cluster, k=args.k),
            batch_size=args.batch_size,
            summary=args.summary,
            workers=args.workers,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
        print("Connection closed.", file=sys.stderr)


if __name__ == "__main__":
    main()
