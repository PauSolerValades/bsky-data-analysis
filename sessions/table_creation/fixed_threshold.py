"""Fixed-threshold session clustering — global gap threshold.

If the gap between two consecutive events exceeds `threshold_s` seconds,
a new session starts. Same threshold for every user.

Output table: (did, session_start, session_end, duration_s, is_singleton).

Usage:
    uv run sessions/table_creation/fixed_threshold.py --table-name sessions_fixed_300s --threshold 300 --summary
"""

import argparse
import sys
from functools import partial

from _core import get_connection, load_dids, run_batch_loop


# ── Clustering ────────────────────────────────────────────────────────────

def _fixed_cluster(timestamps_us: list[int], threshold_s: float):
    """Cluster sorted timestamps with a fixed gap threshold.

    Input: timestamps in microseconds, threshold in seconds.
    Returns: (start_us, end_us, is_singleton) in microseconds.
    """
    if len(timestamps_us) < 1:
        return None
    unique_s = sorted(set(t / 1_000_000 for t in timestamps_us))
    if len(unique_s) < 1:
        return None

    sessions = []
    cur_start = unique_s[0]
    cur_end = unique_s[0]
    for i in range(1, len(unique_s)):
        t = unique_s[i]
        if t - unique_s[i - 1] > threshold_s:
            sessions.append((int(cur_start * 1_000_000), int(cur_end * 1_000_000), False))
            cur_start = t
        cur_end = t
    sessions.append((int(cur_start * 1_000_000), int(cur_end * 1_000_000), False))
    return sessions


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fixed-threshold session clustering.")
    parser.add_argument("--table-name", type=str, required=True,
                        help="Output table name (e.g., sessions_fixed_300s)")
    parser.add_argument("--threshold", type=float, required=True,
                        help="Gap threshold in seconds")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel workers for clustering")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    print(f"Fixed threshold={args.threshold}s → {args.table_name}", file=sys.stderr)
    conn = get_connection()

    try:
        dids = load_dids(conn)
        print(f"  → {len(dids):,} DIDs from DB", file=sys.stderr)
        if not dids:
            print("No DIDs found.", file=sys.stderr)
            return

        run_batch_loop(
            conn, args.table_name, dids,
            cluster_fn=partial(_fixed_cluster, threshold_s=args.threshold),
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
