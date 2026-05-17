#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "numpy",
# ]
# ///
"""
Session-based engagement analysis using a fixed Δₜ threshold (elbow method).

Queries only pau_db.user_core_events (posts, replies, reposts),
filters users by event-count bounds, and clusters events into sessions with
a fixed inter-arrival gap threshold.

Writes results to pau_db.sessions_threshold in StarRocks.

Usage:
    uv run session-analysis/session_core_events.py --summary
    uv run session-analysis/session_core_events.py --min-events 6 --max-events 800 --threshold 285 --summary
"""

import argparse
import os
import sys
import time as time_mod
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymysql


def _load_env_file():
    """Load .env file from project root or working directory, if present."""
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for f in candidates:
        if f.exists():
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
            return


_load_env_file()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


DB_CONFIG = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": _env("DATABASE_NAME", "bsky"),
    "charset": "utf8mb4",
}

FETCH_BATCH = 2000    # DIDs per event-fetch query
INSERT_FLUSH = 50_000  # rows per INSERT batch


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Session:
    start_us: int
    end_us: int
    reposts: int = 0
    posts_authored: int = 0

    @property
    def duration_s(self) -> float:
        return (self.end_us - self.start_us) / 1_000_000


def _inc(s: Session, event_type: str):
    if event_type == "repost":
        s.reposts += 1
    elif event_type in ("post", "reply"):
        s.posts_authored += 1


# ---------------------------------------------------------------------------
# Session clustering (fixed threshold)
# ---------------------------------------------------------------------------

def cluster_sessions_fixed(
    timestamps: list[tuple[int, str]],
    threshold_s: float,
) -> list[Session]:
    """Cluster events into sessions using a fixed gap threshold.

    Two events ≤ threshold_s apart belong to the same session;
    a gap > threshold_s starts a new session.
    """
    if not timestamps:
        return []

    threshold_us = threshold_s * 1_000_000

    sessions = []
    cur = Session(start_us=timestamps[0][0], end_us=timestamps[0][0])
    _inc(cur, timestamps[0][1])

    for i in range(1, len(timestamps)):
        t_us, event_type = timestamps[i]
        gap = t_us - timestamps[i - 1][0]
        if gap > threshold_us:
            sessions.append(cur)
            cur = Session(start_us=t_us, end_us=t_us)
        else:
            cur.end_us = t_us
        _inc(cur, event_type)

    sessions.append(cur)
    return sessions


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pau_db.{table} (
    `did`               varchar(128) NOT NULL,
    `session_start`     bigint NOT NULL,
    `session_end`       bigint NOT NULL,
    `next_session_start` bigint NULL,
    `duration_s`        double NOT NULL,
    `reposts`           int NOT NULL,
    `posts_authored`    int NOT NULL,
    `threshold_s`       double NOT NULL
) ENGINE=OLAP
DUPLICATE KEY(`did`, `session_start`)
DISTRIBUTED BY HASH(`did`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1"
);
"""

INSERT_SQL = """
INSERT INTO pau_db.{table}
    (did, session_start, session_end, next_session_start,
     duration_s, reposts, posts_authored, threshold_s)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s)
"""

# Query to find DIDs with event counts in [min_events, max_events]
SELECT_DIDS_SQL = """
SELECT did, COUNT(*) AS n
FROM {source_table}
GROUP BY did
HAVING n >= %s AND n <= %s
ORDER BY did
"""

# Query to fetch all events for a batch of DIDs
FETCH_EVENTS_SQL = """
SELECT did, time_us, event_type
FROM {source_table}
WHERE did IN ({placeholders})
ORDER BY did, time_us
"""


def load_eligible_dids(
    conn: pymysql.Connection,
    min_events: int,
    max_events: int,
    source_table: str,
) -> list[str]:
    """Return DIDs whose event count falls in [min_events, max_events]."""
    print(f"Querying users with {min_events}–{max_events} events from {source_table} ...", file=sys.stderr)
    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute(SELECT_DIDS_SQL.format(source_table=source_table), (min_events, max_events))
        dids = [row[0] for row in cur]
    elapsed = time_mod.time() - t0
    print(f"  → {len(dids):,} DIDs found in {elapsed:.1f}s", file=sys.stderr)
    return dids


def fetch_events_for_dids(
    conn: pymysql.Connection,
    dids: list[str],
    source_table: str,
) -> dict[str, list[tuple[int, str]]]:
    """Return {did: [(time_us, event_type), ...]} sorted by time for each DID."""
    if not dids:
        return {}

    placeholders = ",".join(["%s"] * len(dids))
    sql = FETCH_EVENTS_SQL.format(placeholders=placeholders, source_table=source_table)

    result: dict[str, list[tuple[int, str]]] = defaultdict(list)
    with conn.cursor() as cur:
        cur.execute(sql, dids)
        for did, time_us, event_type in cur:
            result[did].append((int(time_us), event_type))
    return dict(result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Session-based Bluesky engagement analysis (fixed Δₜ threshold)"
    )
    parser.add_argument(
        "--min-events", type=int, default=6,
        help="Minimum total events per user (default: 6, excludes tourists)",
    )
    parser.add_argument(
        "--max-events", type=int, default=800,
        help="Maximum total events per user (default: 800, excludes bots >100/day)",
    )
    parser.add_argument(
        "-t", "--threshold", type=float, default=285.0,
        help="Session gap threshold in seconds (default: 285, the 4.8-min elbow)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=FETCH_BATCH,
        help=f"DIDs per event-fetch query (default: {FETCH_BATCH})",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Print per-user summary statistics after loading",
    )
    parser.add_argument(
        "--source-table", type=str, default="pau_db.user_core_events_human",
        help="Source table: user_core_events (all), user_core_events_human (6-500, default), "
             "or user_core_events_dominant (101-500)",
    )
    parser.add_argument(
        "--did-file", type=str, default=None,
        help="File with one DID per line (overrides --source-table and event filters)",
    )
    parser.add_argument(
        "--output-table", type=str, default="sessions_threshold",
        help="Output table name in pau_db (default: sessions_threshold)",
    )
    args = parser.parse_args()

    output_table = args.output_table
    threshold_s = args.threshold
    print(f"Session threshold: {threshold_s:.0f}s ({threshold_s / 60:.1f} min)", file=sys.stderr)
    print(f"Event filter: {args.min_events}–{args.max_events} events per user", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Connect
    # -----------------------------------------------------------------------
    conn = pymysql.connect(**DB_CONFIG)
    print(f"Connected to {DB_CONFIG['host']}:{DB_CONFIG['port']}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Create output table
    # -----------------------------------------------------------------------
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL.format(table=output_table))
    conn.commit()
    print(f"Table pau_db.{output_table} ready.", file=sys.stderr)

    # -----------------------------------------------------------------------
    # 1. Get eligible DIDs
    # -----------------------------------------------------------------------
    if args.did_file:
        with open(args.did_file) as f:
            all_dids = [line.strip() for line in f if line.strip()]
        print(f"Loaded {len(all_dids):,} DIDs from {args.did_file}", file=sys.stderr)
    else:
        all_dids = load_eligible_dids(conn, args.min_events, args.max_events, args.source_table)
    if not all_dids:
        print("No users match the event-count filter. Exiting.", file=sys.stderr)
        conn.close()
        return

    # -----------------------------------------------------------------------
    # 2. Process in batches
    # -----------------------------------------------------------------------
    batches = [
        all_dids[i:i + args.batch_size]
        for i in range(0, len(all_dids), args.batch_size)
    ]
    total_batches = len(batches)

    insert_buffer: list[tuple] = []

    insert_sql = INSERT_SQL.format(table=output_table)

    def flush_inserts():
        nonlocal insert_buffer
        if not insert_buffer:
            return
        with conn.cursor() as cur:
            cur.executemany(insert_sql, insert_buffer)
        conn.commit()
        insert_buffer.clear()

    # Summary accumulators
    all_durations: list[float] = []
    all_reposts: list[int] = []
    all_posts: list[int] = []
    seen_users: set[str] = set()
    total_sessions = 0

    t0 = time_mod.time()

    for batch_idx, batch_dids in enumerate(batches):
        actions_by_did = fetch_events_for_dids(conn, batch_dids, args.source_table)

        for did in batch_dids:
            timestamps = actions_by_did.get(did, [])
            if len(timestamps) < 2:
                continue  # need at least 2 events for a session

            sessions = cluster_sessions_fixed(timestamps, threshold_s)

            seen_users.add(did)
            total_sessions += len(sessions)

            for i, s in enumerate(sessions):
                next_start = sessions[i + 1].start_us if i + 1 < len(sessions) else None
                insert_buffer.append((
                    did,
                    s.start_us,
                    s.end_us,
                    next_start,
                    round(s.duration_s, 3),
                    s.reposts,
                    s.posts_authored,
                    threshold_s,
                ))

                all_durations.append(s.duration_s)
                all_reposts.append(s.reposts)
                all_posts.append(s.posts_authored)

            if len(insert_buffer) >= INSERT_FLUSH:
                flush_inserts()

        flush_inserts()

        if (batch_idx + 1) % 10 == 0 or batch_idx == total_batches - 1:
            elapsed = time_mod.time() - t0
            pct = 100 * (batch_idx + 1) / total_batches
            rate = (batch_idx + 1) * args.batch_size / elapsed if elapsed > 0 else 0
            print(
                f"  Batch {batch_idx + 1}/{total_batches} ({pct:.0f}%) | "
                f"{len(seen_users):,} users | {total_sessions:,} sessions | "
                f"{elapsed:.0f}s | ~{rate:.0f} users/s",
                file=sys.stderr,
            )

    flush_inserts()
    conn.close()

    elapsed = time_mod.time() - t0
    print(f"\nDone in {elapsed:.0f}s", file=sys.stderr)

    if args.summary and all_durations:
        _print_summary(
            all_durations, all_reposts, all_posts,
            total_sessions, len(seen_users), args,
        )


def _print_summary(
    all_durations, all_reposts, all_posts,
    total_sessions, total_users, args,
):
    durations = np.array(all_durations)
    reposts_a = np.array(all_reposts)
    posts_a = np.array(all_posts)

    n = len(durations)

    def _p(arr, pct):
        return np.percentile(arr, pct)

    print("\n" + "=" * 60, file=sys.stderr)
    print(
        f"  SESSION ANALYSIS SUMMARY  (n={n:,} sessions, {total_users:,} users)",
        file=sys.stderr,
    )
    print("=" * 60, file=sys.stderr)
    print(
        f"  Fixed threshold: {args.threshold}s ({args.threshold / 60:.1f} min)  |  "
        f"Event filter: {args.min_events}–{args.max_events}",
        file=sys.stderr,
    )
    print(f"  Source: pau_db.user_core_events  (posts + replies + reposts)", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    print("  Session duration (seconds):", file=sys.stderr)
    print(f"    Mean:   {np.mean(durations):.0f}", file=sys.stderr)
    print(f"    Median: {np.median(durations):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(durations, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(durations, 75):.0f}", file=sys.stderr)
    print(f"    P90:    {_p(durations, 90):.0f}", file=sys.stderr)

    print("  Reposts per session:", file=sys.stderr)
    print(f"    Mean:   {np.mean(reposts_a):.2f}", file=sys.stderr)
    print(f"    Median: {np.median(reposts_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(reposts_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(reposts_a, 75):.0f}", file=sys.stderr)

    print("  Posts authored per session:", file=sys.stderr)
    print(f"    Mean:   {np.mean(posts_a):.2f}", file=sys.stderr)
    print(f"    Median: {np.median(posts_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(posts_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(posts_a, 75):.0f}", file=sys.stderr)

    print("=" * 60 + "\n", file=sys.stderr)


if __name__ == "__main__":
    main()
