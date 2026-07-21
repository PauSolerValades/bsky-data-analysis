#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "python-dotenv",
#     "numpy",
#     "hdbscan",
# ]
# ///
"""
Session creation — Bluesky firehose → pau_db.sessions_raw_*.

Sources:
  • core_tukey  → bsky.records + bsky.posts, Tukey clustering
  • all_tukey   → pau_db.all_events,          Tukey clustering

Usage:
    uv run session-creation/create-sessions.py all_tukey --did-from-file sample_dids.txt --summary
"""

import argparse
import os
import sys
import time as time_mod
from collections import defaultdict
from enum import Enum
from pathlib import Path

import numpy as np
import pymysql
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(ENV_PATH)

DB_CONFIG = {
    "host": os.getenv("DATABASE_HOST", "10.18.74.14"),
    "port": int(os.getenv("DATABASE_PORT", "9030")),
    "user": os.getenv("DATABASE_USER", "pau"),
    "password": os.getenv("PAU_PASSWORD", ""),
    "database": os.getenv("DATABASE_NAME", "bsky"),
    "charset": "utf8mb4",
}

BATCH_SIZE = 2000
INSERT_FLUSH = 2_500


# ---------------------------------------------------------------------------
# Source enum
# ---------------------------------------------------------------------------

class Source(Enum):
    CORE_TUKEY    = "core_tukey"
    ALL_TUKEY     = "all_tukey"
    ALL_HDBSCAN   = "all_hdbscan"


def _source_queries(source: Source, hdbscan_epsilon: int | None = None, hdbscan_mcs: int = 2, hdbscan_ms: int = 1) -> dict:
    """Return all source-dependent queries and metadata."""
    is_core = source.value.startswith("core")

    if is_core:
        dids_sql = """
            SELECT DISTINCT did FROM (
                SELECT did FROM bsky.records
                UNION
                SELECT did FROM bsky.posts
            ) t
            ORDER BY did
        """
        events_sql = """
            SELECT did, time_us
            FROM bsky.records
            WHERE did IN ({placeholders})
            UNION ALL
            SELECT did, time_us
            FROM bsky.posts
            WHERE did IN ({placeholders})
            ORDER BY did, time_us
        """
        params_factor = 2
    else:
        dids_sql = """
            SELECT DISTINCT did
            FROM pau_db.all_events_v2
            ORDER BY did
        """
        events_sql = """
            SELECT did, time_us
            FROM pau_db.all_events_v2
            WHERE did IN ({placeholders})
            ORDER BY did, time_us
        """
        params_factor = 1

    target = f"pau_db.sessions_raw_{source.value}"
    if hdbscan_epsilon is not None:
        target += f"_e{hdbscan_epsilon}"
    if hdbscan_mcs != 2:
        target += f"_mcs{hdbscan_mcs}"
    if hdbscan_ms not in (1, None):
        target += f"_ms{hdbscan_ms}"

    return {
        "dids": dids_sql,
        "events": events_sql,
        "events_params_factor": params_factor,
        "target": target,
        "create": f"""
            CREATE TABLE IF NOT EXISTS {target} (
                `did`           varchar(128) NOT NULL,
                `session_start` bigint NOT NULL,
                `session_end`   bigint NOT NULL,
                `duration_s`    double NOT NULL,
                `is_singleton`  tinyint NOT NULL
            ) ENGINE=OLAP
            DUPLICATE KEY(`did`, `session_start`)
            DISTRIBUTED BY HASH(`did`) BUCKETS 32
            PROPERTIES ("replication_num" = "1")
        """,
        "insert": f"""
            INSERT INTO {target}
                (did, session_start, session_end, duration_s, is_singleton)
            VALUES (%s, %s, %s, %s, %s)
        """,
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _execute(conn, query, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _flush_inserts(conn, insert_sql, buffer):
    if not buffer:
        return
    with conn.cursor() as cur:
        cur.executemany(insert_sql, buffer)
    conn.commit()
    buffer.clear()


def load_dids(conn, did_query):
    return [row[0] for row in _execute(conn, did_query)]


def fetch_user_timestamps(conn, dids, events_query_tpl, params_factor):
    if not dids:
        return {}
    placeholders = ",".join(["%s"] * len(dids))
    query = events_query_tpl.format(placeholders=placeholders)
    params = dids * params_factor
    result = defaultdict(list)
    for did, time_us in _execute(conn, query, params):
        result[did].append(int(time_us))
    return dict(result)


# ---------------------------------------------------------------------------
# Clustering methods
# ---------------------------------------------------------------------------

def _cluster_events(timestamps, threshold_us):
    """Common: cluster sorted timestamps using a per-user threshold."""
    sessions: list[tuple[int, int, bool]] = []
    cur_start = timestamps[0]
    cur_end = timestamps[0]
    for i in range(1, len(timestamps)):
        t = timestamps[i]
        if t - timestamps[i - 1] > threshold_us:
            sessions.append((cur_start, cur_end, False))
            cur_start = t
        cur_end = t
    sessions.append((cur_start, cur_end, False))
    return sessions


def tukey_cluster(timestamps_us: list[int]) -> list[tuple[int, int, bool]] | None:
    """Tukey: Q3 + 1.5 × IQR on microsecond gaps."""
    if len(timestamps_us) < 5:
        return None
    gaps = np.diff(np.array(timestamps_us, dtype=np.int64))
    q1, q3 = np.percentile(gaps, [25, 75])
    threshold = int(q3 + 1.5 * (q3 - q1))
    return _cluster_events(timestamps_us, threshold)


def cluster(timestamps_us: list[int]) -> list[tuple[int, int, bool]] | None:
    """Tukey clustering with timestamp deduplication."""
    if len(timestamps_us) < 5:
        return None
    unique = sorted(set(timestamps_us))
    if len(unique) < 5:
        return None
    return tukey_cluster(unique)


def hdbscan_cluster(
    timestamps_us: list[int],
    min_cluster_size: int = 2,
    min_samples: int = 1,
    cluster_selection_epsilon: float = 60.0,
) -> list[tuple[int, int, bool]] | None:
    """HDBSCAN clustering on 1D timestamps.

    Returns list of (start_us, end_us, is_singleton).
    Clustered events have is_singleton=False.
    Unclustered (noise) events become singletons with is_singleton=True.
    """
    import hdbscan

    unique = sorted(set(timestamps_us))
    if len(unique) < 2:
        return None

    t0 = unique[0]
    X = np.array([[t - t0] for t in unique], dtype=np.float64) / 1_000_000

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_epsilon=cluster_selection_epsilon,
    )
    labels = clusterer.fit_predict(X)

    sessions: list[tuple[int, int, bool]] = []
    cur_label = None
    cur_start: int | None = None
    cur_end: int | None = None

    for i, label in enumerate(labels):
        t = unique[i]
        if label == -1:
            # Noise → singleton
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
    parser = argparse.ArgumentParser(description="Create sessions from Bluesky firehose data.")
    parser.add_argument(
        "table_source", type=str,
        choices=[s.value for s in Source],
        help="Source×method (e.g., all_tukey).",
    )
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--did-from-file", type=str, default=None)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument(
        "--hdbscan-epsilon", type=int, default=None,
        help="cluster_selection_epsilon for HDBSCAN in seconds. "
             "Appended to table name (e.g., _e30).",
    )
    parser.add_argument(
        "--hdbscan-min-cluster-size", type=int, default=2,
        help="min_cluster_size for HDBSCAN (default: 2).",
    )
    parser.add_argument(
        "--hdbscan-min-samples", type=int, default=1,
        help="min_samples for HDBSCAN (default: 1).",
    )
    args = parser.parse_args()

    source = Source(args.table_source)
    hdbscan_eps = args.hdbscan_epsilon
    hdbscan_mcs = args.hdbscan_min_cluster_size
    hdbscan_ms = args.hdbscan_min_samples
    q = _source_queries(source, hdbscan_epsilon=hdbscan_eps, hdbscan_mcs=hdbscan_mcs, hdbscan_ms=hdbscan_ms)

    print(f"Connecting to DB ({DB_CONFIG['host']}:{DB_CONFIG['port']}) ...", file=sys.stderr)
    conn = pymysql.connect(**DB_CONFIG)

    try:
        # ── 1. Load DIDs ─────────────────────────────────────────────
        if args.did_from_file:
            print(f"Loading DIDs from {args.did_from_file} ...", file=sys.stderr)
            t0 = time_mod.time()
            dids = Path(args.did_from_file).read_text().strip().splitlines()
            print(f"  → {len(dids):,} DIDs in {time_mod.time() - t0:.0f}s", file=sys.stderr)
        else:
            print(f"Loading DIDs for source='{source.value}' ...", file=sys.stderr)
            t0 = time_mod.time()
            dids = load_dids(conn, q["dids"])
            print(f"  → {len(dids):,} DIDs in {time_mod.time() - t0:.0f}s", file=sys.stderr)

        if not dids:
            print("No DIDs found.", file=sys.stderr)
            return

        # ── 2. Create output table ──────────────────────────────────
        _execute(conn, q["create"])
        conn.commit()
        print(f"Table {q['target']} ready.", file=sys.stderr)

        # ── 3. Batch loop ────────────────────────────────────────────
        batches = [dids[i:i + args.batch_size] for i in range(0, len(dids), args.batch_size)]
        total_batches = len(batches)

        insert_buffer = []
        total_sessions = 0
        seen_users = 0
        all_durations = []

        t0 = time_mod.time()

        for batch_idx, batch_dids in enumerate(batches):
            user_ts = fetch_user_timestamps(
                conn, batch_dids, q["events"], q["events_params_factor"],
            )

            for did in batch_dids:
                timestamps = user_ts.get(did, [])

                if source.value.endswith("hdbscan"):
                    sessions = hdbscan_cluster(
                        timestamps,
                        cluster_selection_epsilon=hdbscan_eps or 60,
                        min_cluster_size=hdbscan_mcs,
                        min_samples=hdbscan_ms or 1,
                    )
                else:
                    sessions = cluster(timestamps)

                if sessions is None:
                    continue

                seen_users += 1
                total_sessions += len(sessions)

                for start_us, end_us, is_singleton in sessions:
                    duration_s = (end_us - start_us) / 1_000_000
                    insert_buffer.append((
                        did, start_us, end_us,
                        round(duration_s, 3),
                        1 if is_singleton else 0,
                    ))
                    all_durations.append(duration_s)

                    if len(insert_buffer) >= INSERT_FLUSH:
                        _flush_inserts(conn, q["insert"], insert_buffer)

            _flush_inserts(conn, q["insert"], insert_buffer)

            if (batch_idx + 1) % 10 == 0 or batch_idx == total_batches - 1:
                elapsed = time_mod.time() - t0
                pct = 100 * (batch_idx + 1) / total_batches
                rate = (batch_idx + 1) * args.batch_size / elapsed if elapsed > 0 else 0
                print(
                    f"  Batch {batch_idx + 1}/{total_batches} ({pct:.0f}%) | "
                    f"{seen_users:,} users | {total_sessions:,} sessions | "
                    f"{elapsed:.0f}s | ~{rate:.0f} users/s",
                    file=sys.stderr,
                )

        _flush_inserts(conn, q["insert"], insert_buffer)
        elapsed = time_mod.time() - t0
        print(f"\nDone in {elapsed:.0f}s", file=sys.stderr)

        if args.summary and all_durations:
            _print_summary(all_durations, total_sessions, seen_users)

    except pymysql.Error as e:
        print(f"\nDatabase error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
        print("Connection closed.", file=sys.stderr)


def _print_summary(durations, total_sessions, total_users):
    d = np.array(durations)
    print("\n" + "=" * 55, file=sys.stderr)
    print(f"  SESSION SUMMARY  (n={total_sessions:,} sessions, {total_users:,} users)", file=sys.stderr)
    print("=" * 55, file=sys.stderr)
    print("-" * 55, file=sys.stderr)
    print("  Session duration (s):", file=sys.stderr)
    print(f"    Mean:   {np.mean(d):.0f}", file=sys.stderr)
    print(f"    Median: {np.median(d):.0f}", file=sys.stderr)
    print(f"    P25:    {np.percentile(d, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {np.percentile(d, 75):.0f}", file=sys.stderr)
    print(f"    P90:    {np.percentile(d, 90):.0f}", file=sys.stderr)
    print("=" * 55 + "\n", file=sys.stderr)


if __name__ == "__main__":
    main()
