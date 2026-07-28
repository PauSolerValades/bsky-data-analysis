#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pymysql", "python-dotenv", "numpy", "hdbscan"]
# ///
"""
HDBSCAN session clustering — one run, one table.

Usage:
    uv run session-creation/run_hdbscan.py --epsilon 120 --min-samples 1 --did-from-file sample_dids.txt --summary
"""

import argparse
import sys

import numpy as np
import pymysql

from _core import get_connection, load_dids, run_batch_loop


# ---------------------------------------------------------------------------
# HDBSCAN clustering
# ---------------------------------------------------------------------------

def _hdbscan_cluster(
    timestamps_us: list[int],
    min_cluster_size: int = 2,
    min_samples: int = 1,
    epsilon: float = 60.0,
):
    """HDBSCAN on 1D timestamps. Noise → tagged singletons."""
    import hdbscan

    unique = sorted(set(timestamps_us))
    if len(unique) < 2:
        return None

    t0 = unique[0]
    X = np.array([[t - t0] for t in unique], dtype=np.float64) / 1_000_000

    clusterer = hdbscan.HDBSCAN(
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
        t = unique[i]
        if label == -1:
            if cur_start is not None:
                sessions.append((cur_start, cur_end, False))
                cur_start = None
            sessions.append((t, t, True))
            continue
        int_label = int(label)
        if int_label != cur_label:
            if cur_start is not None:
                sessions.append((cur_start, cur_end, False))
            cur_start = t
            cur_label = int_label
        cur_end = t

    if cur_start is not None:
        sessions.append((cur_start, cur_end, False))

    return sessions if sessions else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HDBSCAN session clustering.")
    parser.add_argument("--epsilon", type=float, default=60.0, help="cluster_selection_epsilon (seconds)")
    parser.add_argument("--min-samples", type=int, default=1, help="min_samples")
    parser.add_argument("--min-cluster-size", type=int, default=2, help="min_cluster_size")
    parser.add_argument("--did-from-file", type=str, default=None)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    eps = int(args.epsilon)
    ms = args.min_samples
    mcs = args.min_cluster_size

    target = f"pau_db.sessions_raw_hdbscan_e{eps}"
    if ms != 1:
        target += f"_ms{ms}"
    if mcs != 2:
        target += f"_mcs{mcs}"

    print(f"HDBSCAN ε={eps} ms={ms} mcs={mcs} → {target}", file=sys.stderr)
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
            cluster_fn=lambda ts: _hdbscan_cluster(
                ts, min_cluster_size=mcs, min_samples=ms, epsilon=eps,
            ),
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
