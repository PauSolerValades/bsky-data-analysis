"""HDBSCAN session clustering — density-based on 1D timestamps.

Noise points become singletons (is_singleton=True), clusters become sessions.
Output table: (did, session_start, session_end, duration_s, is_singleton).

Usage:
    uv run sessions/table_creation/hdbscan_cluster.py --table-name sessions_hdbscan_e60 --epsilon 60 --summary
"""

import argparse
import sys
from functools import partial

import numpy as np

from _core import get_connection, load_dids, run_batch_loop


# ── Clustering ────────────────────────────────────────────────────────────

def _hdbscan_cluster(
    timestamps_us: list[int],
    min_cluster_size: int = 2,
    min_samples: int = 1,
    epsilon: float = 60.0,
):
    """HDBSCAN on 1D timestamps.

    Input: timestamps in microseconds. Converted to seconds internally.
    Returns: (start_us, end_us, is_singleton) in microseconds.
    Noise points → is_singleton=True.
    """
    import hdbscan as _hdbscan

    unique_s = sorted(set(t / 1_000_000 for t in timestamps_us))
    if len(unique_s) < 2:
        return None

    t0 = unique_s[0]
    X = np.array([[t - t0] for t in unique_s], dtype=np.float64)

    clusterer = _hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_epsilon=epsilon,
    )
    labels = clusterer.fit_predict(X)

    sessions = []
    cur_label = None
    cur_start = None
    cur_end = None

    for i, label in enumerate(labels):
        t = unique_s[i]
        if label == -1:
            if cur_start is not None:
                sessions.append((int(cur_start * 1_000_000), int(cur_end * 1_000_000), False))
                cur_start = None
            sessions.append((int(t * 1_000_000), int(t * 1_000_000), True))
            continue
        int_label = int(label)
        if int_label != cur_label:
            if cur_start is not None:
                sessions.append((int(cur_start * 1_000_000), int(cur_end * 1_000_000), False))
            cur_start = t
            cur_label = int_label
        cur_end = t

    if cur_start is not None:
        sessions.append((int(cur_start * 1_000_000), int(cur_end * 1_000_000), False))

    return sessions if sessions else None


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="HDBSCAN session clustering.")
    parser.add_argument("--table-name", type=str, required=True,
                        help="Output table name (e.g., sessions_hdbscan_e60)")
    parser.add_argument("--epsilon", type=float, default=60.0,
                        help="cluster_selection_epsilon in seconds")
    parser.add_argument("--min-samples", type=int, default=1)
    parser.add_argument("--min-cluster-size", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    print(f"HDBSCAN ε={args.epsilon} ms={args.min_samples} "
          f"mcs={args.min_cluster_size} → {args.table_name}", file=sys.stderr)
    conn = get_connection()

    try:
        dids = load_dids(conn)
        print(f"  → {len(dids):,} DIDs from DB", file=sys.stderr)
        if not dids:
            print("No DIDs found.", file=sys.stderr)
            return

        run_batch_loop(
            conn, args.table_name, dids,
            cluster_fn=partial(
                _hdbscan_cluster,
                min_cluster_size=args.min_cluster_size,
                min_samples=args.min_samples,
                epsilon=args.epsilon,
            ),
            batch_size=args.batch_size,
            summary=args.summary,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
        print("Connection closed.", file=sys.stderr)


if __name__ == "__main__":
    main()
