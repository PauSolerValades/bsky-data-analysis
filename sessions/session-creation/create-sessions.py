#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "python-dotenv",
#     "numpy",
# ]
# ///
"""
Session creation — Bluesky firehose → pau_db.sessions_{core,all}.

Sources:
  • "core" → bsky.records + bsky.posts  (all event types)
  • "all"  → pau_db.all_events           (6 event types, pre-filtered)

Method (per-user adaptive IQR / Tukey's fences):
  • Fetch timestamps per user from the chosen source.
  • Compute inter-arrival gaps.
  • Per-user threshold = max(Q3 + 1.5 × IQR, 120 s).
    Fallback = 60 min if < 4 gaps.
  • Cluster events into sessions wherever gap > threshold.

Usage:
    uv run sessions/session-creation/create-sessions.py core
    uv run sessions/session-creation/create-sessions.py all
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
# Config — load .env from repo root
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

BATCH_SIZE = 2000       # DIDs per event-fetch query
INSERT_FLUSH = 5_000    # rows per INSERT batch (StarRocks limit: 10k)


# ---------------------------------------------------------------------------
# Source — single if/elif, single source of truth
# ---------------------------------------------------------------------------

class Source(Enum):
    CORE = "core"
    ALL = "all"


def _source_queries(source: Source) -> dict:
    """Return all source-dependent queries and metadata."""
    if source is Source.CORE:
        return {
            "dids": """
                SELECT DISTINCT did FROM (
                    SELECT did FROM bsky.records
                    UNION
                    SELECT did FROM bsky.posts
                ) t
                ORDER BY did
            """,
            # {placeholders} is filled per batch with ",".join(["%s"] * len(dids))
            "events": """
                SELECT did, time_us
                FROM bsky.records
                WHERE did IN ({placeholders})
                UNION ALL
                SELECT did, time_us
                FROM bsky.posts
                WHERE did IN ({placeholders})
                ORDER BY did, time_us
            """,
            "events_params_factor": 2,   # UNION ALL → dids repeated for each SELECT
            "target": "pau_db.sessions_raw_core",
            "create": """
                CREATE TABLE IF NOT EXISTS pau_db.sessions_raw_core (
                    `did`           varchar(128) NOT NULL,
                    `session_start` bigint NOT NULL,
                    `session_end`   bigint NOT NULL,
                    `duration_s`    double NOT NULL
                ) ENGINE=OLAP
                DUPLICATE KEY(`did`, `session_start`)
                DISTRIBUTED BY HASH(`did`) BUCKETS 32
                PROPERTIES ("replication_num" = "1")
            """,
            "insert": """
                INSERT INTO pau_db.sessions_raw_core
                    (did, session_start, session_end, duration_s)
                VALUES (%s, %s, %s, %s)
            """,
        }
    elif source is Source.ALL:
        return {
            "dids": """
                SELECT DISTINCT did
                FROM pau_db.all_events
                ORDER BY did
            """,
            "events": """
                SELECT did, time_us
                FROM pau_db.all_events
                WHERE did IN ({placeholders})
                ORDER BY did, time_us
            """,
            "events_params_factor": 1,
            "target": "pau_db.sessions_raw_all",
            "create": """
                CREATE TABLE IF NOT EXISTS pau_db.sessions_raw_all (
                    `did`           varchar(128) NOT NULL,
                    `session_start` bigint NOT NULL,
                    `session_end`   bigint NOT NULL,
                    `duration_s`    double NOT NULL
                ) ENGINE=OLAP
                DUPLICATE KEY(`did`, `session_start`)
                DISTRIBUTED BY HASH(`did`) BUCKETS 32
                PROPERTIES ("replication_num" = "1")
            """,
            "insert": """
                INSERT INTO pau_db.sessions_raw_all
                    (did, session_start, session_end, duration_s)
                VALUES (%s, %s, %s, %s)
            """,
        }


# ---------------------------------------------------------------------------
# Generic DB helpers
# ---------------------------------------------------------------------------

def _execute(conn: pymysql.Connection, query: str, params: list | None = None) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _flush_inserts(
    conn: pymysql.Connection,
    insert_sql: str,
    buffer: list[tuple],
) -> None:
    """Flush the session insert buffer to the database."""
    if not buffer:
        return
    with conn.cursor() as cur:
        cur.executemany(insert_sql, buffer)
    conn.commit()
    buffer.clear()


def load_dids(conn: pymysql.Connection, did_query: str) -> list[str]:
    return [row[0] for row in _execute(conn, did_query)]


def fetch_user_timestamps(
    conn: pymysql.Connection,
    dids: list[str],
    events_query_tpl: str,
    params_factor: int,
) -> dict[str, list[int]]:
    """Return {did: [t1_us, t2_us, ...]} sorted by time for a batch of DIDs."""
    if not dids:
        return {}

    placeholders = ",".join(["%s"] * len(dids))
    query = events_query_tpl.format(placeholders=placeholders)
    params = dids * params_factor

    result: dict[str, list[int]] = defaultdict(list)
    for did, time_us in _execute(conn, query, params):
        result[did].append(int(time_us))
    return dict(result)


# ---------------------------------------------------------------------------
# Tukey clustering (source-agnostic)
# ---------------------------------------------------------------------------

def tukey_cluster(
    timestamps_us: list[int],
    iqr_multiplier: float = 1.5,
) -> list[tuple[int, int]] | None:
    """Cluster sorted timestamps into sessions using Tukey's fences.

    Threshold = Q3 + k × IQR, computed directly on microsecond gaps.
    Returns a list of (start_us, end_us) boundaries, or None if the
    user has fewer than 5 events (4 gaps) — not enough to compute IQR.
    """
    if len(timestamps_us) < 5:
        return None

    gaps = np.diff(np.array(timestamps_us, dtype=np.int64))
    q1, q3 = np.percentile(gaps, [25, 75])
    threshold = int(q3 + iqr_multiplier * (q3 - q1))

    sessions: list[tuple[int, int]] = []
    cur_start = timestamps_us[0]
    cur_end = timestamps_us[0]

    for i in range(1, len(timestamps_us)):
        t = timestamps_us[i]
        if t - timestamps_us[i - 1] > threshold:
            sessions.append((cur_start, cur_end))
            cur_start = t
        cur_end = t

    sessions.append((cur_start, cur_end))
    return sessions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create sessions from Bluesky firehose data (Tukey IQR method)."
    )
    parser.add_argument(
        "table_source",
        type=str,
        choices=[s.value for s in Source],
        help="Data source: 'core' (bsky.records + bsky.posts) or 'all' (pau_db.all_events).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"DIDs per event-fetch query (default: {BATCH_SIZE}).",
    )
    parser.add_argument(
        "--did-from-file",
        type=str,
        default=None,
        help="Path to a file with one DID per line (skips the DID DB query).",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print aggregate statistics after completion.",
    )
    args = parser.parse_args()

    source = Source(args.table_source)
    q = _source_queries(source)

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
            print("No DIDs found. Check the source table.", file=sys.stderr)
            return

        # ── 2. Create output table ──────────────────────────────────
        _execute(conn, q["create"])
        conn.commit()
        print(f"Table {q['target']} ready.", file=sys.stderr)

        # ── 3. Batch-fetch timestamps, cluster, write ────────────────
        batches = [
            dids[i : i + args.batch_size]
            for i in range(0, len(dids), args.batch_size)
        ]
        total_batches = len(batches)

        insert_buffer: list[tuple] = []
        total_sessions = 0
        seen_users = 0
        all_durations: list[float] = []

        t0 = time_mod.time()

        for batch_idx, batch_dids in enumerate(batches):
            user_ts = fetch_user_timestamps(
                conn, batch_dids, q["events"], q["events_params_factor"],
            )

            for did in batch_dids:
                timestamps = user_ts.get(did, [])
                sessions = tukey_cluster(timestamps)
                if sessions is None:
                    continue

                seen_users += 1
                total_sessions += len(sessions)

                for start_us, end_us in sessions:
                    duration_s = (end_us - start_us) / 1_000_000
                    insert_buffer.append((
                        did,
                        start_us,
                        end_us,
                        round(duration_s, 3),
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

        # ── 4. Summary ───────────────────────────────────────────────
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


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(
    durations: list[float],
    total_sessions: int,
    total_users: int,
) -> None:
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
