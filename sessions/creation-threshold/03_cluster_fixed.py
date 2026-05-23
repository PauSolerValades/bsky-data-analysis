#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "numpy",
# ]
# ///
"""
Fixed-threshold session clustering → pau_db.sessions_threshold.

Pipeline order:
  1. 01_core_events_dominant.sql  → pau_db.user_core_events_dominant
  2. 02_detect_threshold.py       → Δt = 265 s (Kneedle elbow)
  3. 03_cluster_fixed.py          → pau_db.sessions_threshold

Method:
  • Reads events from user_core_events_dominant (posts, replies, reposts).
  • Two events ≤ 265 s apart → same session.
  • Gap > 265 s → new session.
  • Writes to pau_db.sessions_threshold.

Parameters (from 02_detect_threshold.py):
  • Threshold: 265 s (4.4 min)
  • Source: user_core_events_dominant (101–500 events, 96K users)

Usage:
    uv run session-creation-threshold/03_cluster_fixed.py --summary
"""

import argparse
import sys
import time as time_mod
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymysql


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_ENV = {}
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            _ENV[key] = val.strip().strip('"').strip("'")

DB_CONFIG = {
    "host": _ENV.get("DATABASE_HOST", "10.18.74.14"),
    "port": int(_ENV.get("DATABASE_PORT", "9030")),
    "user": _ENV.get("DATABASE_USER", "pau"),
    "password": _ENV.get("PAU_PASSWORD", ""),
    "database": _ENV.get("DATABASE_NAME", "bsky"),
    "charset": "utf8mb4",
}

FETCH_BATCH = 2000
INSERT_FLUSH = 50_000


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
# Clustering
# ---------------------------------------------------------------------------

def cluster_sessions_fixed(
    timestamps: list[tuple[int, str]],
    threshold_s: float,
) -> list[Session]:
    if not timestamps:
        return []

    threshold_us = threshold_s * 1_000_000
    sessions = []
    cur = Session(start_us=timestamps[0][0], end_us=timestamps[0][0])
    _inc(cur, timestamps[0][1])

    for i in range(1, len(timestamps)):
        t_us, event_type = timestamps[i]
        if t_us - timestamps[i - 1][0] > threshold_us:
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
CREATE TABLE IF NOT EXISTS pau_db.sessions_threshold (
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
INSERT INTO pau_db.sessions_threshold
    (did, session_start, session_end, next_session_start,
     duration_s, reposts, posts_authored, threshold_s)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s)
"""

SELECT_DIDS_SQL = """
SELECT did FROM {source_table} GROUP BY did ORDER BY did
"""

FETCH_EVENTS_SQL = """
SELECT did, time_us, event_type
FROM {source_table}
WHERE did IN ({placeholders})
ORDER BY did, time_us
"""


def load_dids(conn: pymysql.Connection, source_table: str) -> list[str]:
    print(f"Querying DIDs from {source_table} ...", file=sys.stderr)
    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute(SELECT_DIDS_SQL.format(source_table=source_table))
        dids = [row[0] for row in cur]
    print(f"  → {len(dids):,} DIDs in {time_mod.time() - t0:.1f}s", file=sys.stderr)
    return dids


def fetch_events(
    conn: pymysql.Connection,
    dids: list[str],
    source_table: str,
) -> dict[str, list[tuple[int, str]]]:
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
        description="Fixed-threshold session clustering → pau_db.sessions_threshold"
    )
    parser.add_argument(
        "--source-table", default="pau_db.user_core_events_dominant",
        help="Source table (default: user_core_events_dominant, 101–500 events)",
    )
    parser.add_argument(
        "-t", "--threshold", type=float, default=265.0,
        help="Gap threshold in seconds (default: 265, from elbow method)",
    )
    parser.add_argument("--batch-size", type=int, default=FETCH_BATCH)
    parser.add_argument("--summary", action="store_true",
                        help="Print aggregate statistics after completion")
    args = parser.parse_args()

    print(f"Threshold: {args.threshold:.0f}s ({args.threshold / 60:.1f} min)", file=sys.stderr)

    conn = pymysql.connect(**DB_CONFIG)
    print(f"Connected to {DB_CONFIG['host']}:{DB_CONFIG['port']}", file=sys.stderr)

    # Create output table
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    print("Table pau_db.sessions_threshold ready.", file=sys.stderr)

    # Get DIDs (dominant table is already pre-filtered to 101–500)
    all_dids = load_dids(conn, args.source_table)
    if not all_dids:
        print("No DIDs found. Did you run 01_core_events_dominant.sql?", file=sys.stderr)
        conn.close()
        return

    batches = [all_dids[i : i + args.batch_size] for i in range(0, len(all_dids), args.batch_size)]
    total_batches = len(batches)

    insert_buffer: list[tuple] = []
    all_durations: list[float] = []
    all_reposts: list[int] = []
    all_posts: list[int] = []
    seen_users: set[str] = set()
    total_sessions = 0

    def flush():
        nonlocal insert_buffer
        if not insert_buffer:
            return
        with conn.cursor() as cur:
            cur.executemany(INSERT_SQL, insert_buffer)
        conn.commit()
        insert_buffer.clear()

    t0 = time_mod.time()

    for batch_idx, batch_dids in enumerate(batches):
        actions_by_did = fetch_events(conn, batch_dids, args.source_table)

        for did in batch_dids:
            timestamps = actions_by_did.get(did, [])
            if len(timestamps) < 2:
                continue

            sessions = cluster_sessions_fixed(timestamps, args.threshold)
            seen_users.add(did)
            total_sessions += len(sessions)

            for i, s in enumerate(sessions):
                next_start = sessions[i + 1].start_us if i + 1 < len(sessions) else None
                insert_buffer.append((
                    did, s.start_us, s.end_us, next_start,
                    round(s.duration_s, 3),
                    s.reposts, s.posts_authored, args.threshold,
                ))
                all_durations.append(s.duration_s)
                all_reposts.append(s.reposts)
                all_posts.append(s.posts_authored)

            if len(insert_buffer) >= INSERT_FLUSH:
                flush()

        flush()

        if (batch_idx + 1) % 10 == 0 or batch_idx == total_batches - 1:
            elapsed = time_mod.time() - t0
            pct = 100 * (batch_idx + 1) / total_batches
            rate = (batch_idx + 1) * args.batch_size / elapsed if elapsed > 0 else 0
            print(f"  Batch {batch_idx + 1}/{total_batches} ({pct:.0f}%) | "
                  f"{len(seen_users):,} users | {total_sessions:,} sessions | "
                  f"{elapsed:.0f}s | ~{rate:.0f} users/s", file=sys.stderr)

    flush()
    conn.close()

    elapsed = time_mod.time() - t0
    print(f"\nDone in {elapsed:.0f}s", file=sys.stderr)

    if args.summary and all_durations:
        durations = np.array(all_durations)
        reposts_a = np.array(all_reposts)
        posts_a = np.array(all_posts)
        n = len(durations)

        def p(arr, pct):
            return np.percentile(arr, pct)

        print("\n" + "=" * 60, file=sys.stderr)
        print(f"  SESSION ANALYSIS — {n:,} sessions, {len(seen_users):,} users", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"  Threshold: {args.threshold:.0f}s ({args.threshold / 60:.1f} min)", file=sys.stderr)
        print("-" * 60, file=sys.stderr)
        print(f"  Duration (s):  mean={np.mean(durations):.0f}  median={np.median(durations):.0f}  "
              f"P25={p(durations, 25):.0f}  P75={p(durations, 75):.0f}  P90={p(durations, 90):.0f}", file=sys.stderr)
        print(f"  Reposts:       mean={np.mean(reposts_a):.2f}  median={np.median(reposts_a):.0f}", file=sys.stderr)
        print(f"  Posts authored: mean={np.mean(posts_a):.2f}  median={np.median(posts_a):.0f}", file=sys.stderr)
        print("=" * 60 + "\n", file=sys.stderr)


if __name__ == "__main__":
    main()
