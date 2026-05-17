#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "numpy",
# ]
# ///
"""
Session-based engagement analysis for Bluesky firehose data.
Uses per-user adaptive session clustering (Tukey's fences / IQR).

Writes results directly to pau_db.sessions_tukey in StarRocks.

Usage:
    # Filter by event-count range (recommended: 6–500 from EDA)
    uv run session-analysis/session_engagement_analysis.py \
      --min-events 6 --max-events 500 --summary

    # Or use a pre-filtered DID file
    uv run session-analysis/session_engagement_analysis.py \
      --did-file session-analysis/results/users.txt \
      --summary
"""

import argparse
import os
import sys
import time as time_mod
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import pymysql


def _load_env_file():
    """Load .env file from project root or working directory, if present."""
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",   # repo root
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

BATCH_SIZE = 2000  # DIDs per SQL query
INSERT_FLUSH = 50_000  # rows per INSERT batch


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Session:
    start_us: int
    end_us: int
    likes: int = 0
    reposts: int = 0
    posts: int = 0
    follows: int = 0
    other: int = 0

    @property
    def duration_s(self) -> float:
        return (self.end_us - self.start_us) / 1_000_000

    @property
    def interactions(self) -> int:
        return self.likes + self.reposts

    @property
    def total_actions(self) -> int:
        return self.likes + self.reposts + self.posts + self.follows + self.other


def _inc(s: Session, a: str):
    if a == "like": s.likes += 1
    elif a == "repost": s.reposts += 1
    elif a == "post": s.posts += 1
    elif a == "follow": s.follows += 1
    else: s.other += 1


# ---------------------------------------------------------------------------
# Adaptive threshold (Tukey's fences)
# ---------------------------------------------------------------------------

def compute_user_threshold(
    gaps_s: np.ndarray,
    iqr_multiplier: float = 1.5,
    fallback_s: float = 3600.0,
    min_gaps: int = 4,
) -> tuple[float, bool]:
    if len(gaps_s) < min_gaps:
        return fallback_s, True
    q1, q3 = np.percentile(gaps_s, [25, 75])
    iqr = q3 - q1
    if iqr <= 0:
        return gaps_s[0] + 1, False
    return max(q3 + iqr_multiplier * iqr, 120.0), False


def cluster_sessions_adaptive(
    timestamps_us: list[tuple[int, str]],
    iqr_multiplier: float = 1.5,
    fallback_s: float = 3600.0,
    min_gaps: int = 4,
) -> tuple[list[Session], float, bool]:
    if not timestamps_us:
        return [], 0.0, False
    times = np.array([t[0] for t in timestamps_us], dtype=np.int64)
    gaps_s = np.diff(times) / 1_000_000
    threshold_s, used_fallback = compute_user_threshold(gaps_s, iqr_multiplier, fallback_s, min_gaps)
    threshold_us = threshold_s * 1_000_000

    sessions = []
    cur = Session(start_us=timestamps_us[0][0], end_us=timestamps_us[0][0])
    _inc(cur, timestamps_us[0][1])
    for i in range(1, len(timestamps_us)):
        t_us, act = timestamps_us[i]
        if t_us - timestamps_us[i - 1][0] > threshold_us:
            sessions.append(cur)
            cur = Session(start_us=t_us, end_us=t_us)
        else:
            cur.end_us = t_us
        _inc(cur, act)
    sessions.append(cur)
    return sessions, threshold_s, used_fallback


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pau_db.sessions_tukey (
    `did`              varchar(128) NOT NULL,
    `session_start`    bigint NOT NULL,
    `session_end`      bigint NOT NULL,
    `next_session_start` bigint NULL,
    `duration_s`       double NOT NULL,
    `likes`            int NOT NULL,
    `reposts`          int NOT NULL,
    `posts_authored`   int NOT NULL,
    `follows`          int NOT NULL,
    `other_actions`    int NOT NULL,
    `interactions`     int NOT NULL,
    `total_actions`    int NOT NULL,
    `user_threshold_s` double NOT NULL,
    `user_threshold_fallback` tinyint NOT NULL,
    `user_gap_count`   int NOT NULL
) ENGINE=OLAP
DUPLICATE KEY(`did`, `session_start`)
DISTRIBUTED BY HASH(`did`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1"
);
"""

INSERT_SQL = """
INSERT INTO pau_db.sessions_tukey
    (did, session_start, session_end, next_session_start,
     duration_s, likes, reposts, posts_authored, follows, other_actions,
     interactions, total_actions,
     user_threshold_s, user_threshold_fallback, user_gap_count)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def fetch_actions_for_dids(
    conn: pymysql.Connection,
    dids: list[str],
) -> dict[str, list[tuple[int, str]]]:
    """Return {did: [(time_us, action_type), ...]} sorted by time for each DID.

    Fetches ALL collections from bsky.records (likes, reposts, follows, blocks,
    profile updates, etc.) plus ALL posts from bsky.posts.  Every timestamp the
    user produces contributes to the IQR gap estimate.
    """
    if not dids:
        return {}

    placeholders = ",".join(["%s"] * len(dids))

    query = f"""
        SELECT did, time_us,
               CASE collection
                   WHEN 'app.bsky.feed.like'   THEN 'like'
                   WHEN 'app.bsky.feed.repost' THEN 'repost'
                   WHEN 'app.bsky.graph.follow' THEN 'follow'
                   ELSE 'other'
               END AS action_type
        FROM bsky.records
        WHERE did IN ({placeholders})
        UNION ALL
        SELECT did, time_us, 'post' AS action_type
        FROM bsky.posts
        WHERE did IN ({placeholders})
        ORDER BY did, time_us
    """
    params = dids + dids

    result: dict[str, list[tuple[int, str]]] = defaultdict(list)
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        for did, time_us, action_type in cursor:
            result[did].append((int(time_us), action_type))
    return dict(result)


def load_dids(path: str) -> list[str]:
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def load_dids_from_db(
    conn: pymysql.Connection,
    min_events: int,
    max_events: int,
) -> list[str]:
    """Return DIDs with event count in [min_events, max_events] from user_core_events."""
    query = """
        SELECT did
        FROM pau_db.user_core_events
        GROUP BY did
        HAVING COUNT(*) >= %s AND COUNT(*) <= %s
        ORDER BY did
    """
    with conn.cursor() as cur:
        cur.execute(query, (min_events, max_events))
        return [row[0] for row in cur]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Session-based Bluesky engagement analysis (Tukey)")
    parser.add_argument("--did-file", default=None, help="File with one DID per line (optional if using --min-events)")
    parser.add_argument("--min-events", type=int, default=0, help="Minimum events per user (from user_core_events). Default: 0 = no filter")
    parser.add_argument("--max-events", type=int, default=0, help="Maximum events per user. Default: 0 = no cap")
    parser.add_argument("-q", "--iqr-multiplier", type=float, default=1.5)
    parser.add_argument("-G", "--fallback-threshold", type=float, default=60.0)
    parser.add_argument("--min-gaps", type=int, default=4)
    parser.add_argument("--min-actions", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    # -------------------------------------------------------------------
    # Determine DIDs: from file, from DB event-count filter, or error
    # -------------------------------------------------------------------
    conn_sr = pymysql.connect(**DB_CONFIG)

    if args.did_file:
        all_dids = load_dids(args.did_file)
        print(f"Loaded {len(all_dids):,} DIDs from {args.did_file}", file=sys.stderr)
    elif args.min_events > 0 or args.max_events > 0:
        mn = args.min_events if args.min_events > 0 else 1
        mx = args.max_events if args.max_events > 0 else 999_999_999
        print(f"Querying DIDs with {mn}–{mx} events from pau_db.user_core_events ...", file=sys.stderr)
        t_dids = time_mod.time()
        all_dids = load_dids_from_db(conn_sr, mn, mx)
        print(f"  → {len(all_dids):,} DIDs in {time_mod.time() - t_dids:.0f}s", file=sys.stderr)
    else:
        print("ERROR: specify --did-file or --min-events/--max-events", file=sys.stderr)
        conn_sr.close()
        sys.exit(1)

    if not all_dids:
        print("No DIDs to process. Exiting.", file=sys.stderr)
        conn_sr.close()
        sys.exit(0)

    fallback_s = args.fallback_threshold * 60
    batches = [all_dids[i:i + args.batch_size] for i in range(0, len(all_dids), args.batch_size)]
    total_batches = len(batches)

    # -----------------------------------------------------------------------
    # Connect, create table
    # -----------------------------------------------------------------------
    with conn_sr.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn_sr.commit()
    print("Table pau_db.sessions_tukey ready.", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Summary accumulators
    # -----------------------------------------------------------------------
    all_durations: list[float] = []
    all_likes: list[int] = []
    all_reposts: list[int] = []
    all_posts: list[int] = []
    all_follows: list[int] = []
    all_other: list[int] = []
    all_interactions: list[int] = []
    all_total: list[int] = []
    fallback_users: set[str] = set()
    seen_users: set[str] = set()
    total_sessions = 0

    insert_buffer: list[tuple] = []

    def flush_inserts():
        nonlocal insert_buffer
        if not insert_buffer:
            return
        with conn_sr.cursor() as cur:
            cur.executemany(INSERT_SQL, insert_buffer)
        conn_sr.commit()
        insert_buffer = []

    t0 = time_mod.time()

    for batch_idx, batch_dids in enumerate(batches):
        actions_by_did = fetch_actions_for_dids(conn_sr, batch_dids)

        for did in batch_dids:
            timestamps = actions_by_did.get(did, [])
            if len(timestamps) < args.min_actions:
                continue

            sessions, threshold_s, used_fallback = cluster_sessions_adaptive(
                timestamps, args.iqr_multiplier, fallback_s, args.min_gaps,
            )

            seen_users.add(did)
            if used_fallback:
                fallback_users.add(did)
            total_sessions += len(sessions)
            gap_count = max(len(timestamps) - 1, 0)

            # Compute next_session_start for each session
            for i, s in enumerate(sessions):
                next_start = sessions[i + 1].start_us if i + 1 < len(sessions) else None
                insert_buffer.append((
                    did,
                    s.start_us, s.end_us, next_start,
                    round(s.duration_s, 3),
                    s.likes, s.reposts, s.posts, s.follows, s.other,
                    s.interactions, s.total_actions,
                    round(threshold_s, 1),
                    1 if used_fallback else 0,
                    gap_count,
                ))

                # Accumulate for summary
                all_durations.append(s.duration_s)
                all_likes.append(s.likes)
                all_reposts.append(s.reposts)
                all_posts.append(s.posts)
                all_follows.append(s.follows)
                all_other.append(s.other)
                all_interactions.append(s.interactions)
                all_total.append(s.total_actions)

            if len(insert_buffer) >= INSERT_FLUSH:
                flush_inserts()

        # Flush after each batch
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
    conn_sr.close()

    elapsed = time_mod.time() - t0
    print(f"\nDone in {elapsed:.0f}s", file=sys.stderr)

    if args.summary and all_durations:
        _print_summary(
            all_durations, all_likes, all_reposts, all_posts,
            all_follows, all_other, all_interactions, all_total,
            len(fallback_users), len(seen_users), args,
        )


def _print_summary(all_durations, all_likes, all_reposts, all_posts,
                   all_follows, all_other, all_interactions, all_total,
                   fallback_count, total_users, args):
    durations = np.array(all_durations)
    likes_a = np.array(all_likes)
    reposts_a = np.array(all_reposts)
    posts_a = np.array(all_posts)
    follows_a = np.array(all_follows)
    other_a = np.array(all_other)
    interactions_a = np.array(all_interactions)
    total_a = np.array(all_total)

    n = len(durations)

    def _p(arr, pct): return np.percentile(arr, pct)

    print("\n" + "=" * 60, file=sys.stderr)
    print(f"  SESSION ANALYSIS SUMMARY  (n={n:,} sessions, {total_users:,} users)", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  IQR multiplier: {args.iqr_multiplier}  |  Fallback: {args.fallback_threshold} min", file=sys.stderr)
    if total_users > 0:
        print(f"  Users using fallback threshold: {fallback_count}/{total_users} ({100*fallback_count/total_users:.0f}%)", file=sys.stderr)
    print(f"  Source: all bsky.records + bsky.posts (every collection)", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    print("  Session duration (seconds):", file=sys.stderr)
    print(f"    Mean:   {np.mean(durations):.0f}", file=sys.stderr)
    print(f"    Median: {np.median(durations):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(durations, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(durations, 75):.0f}", file=sys.stderr)

    print("  Total actions per session:", file=sys.stderr)
    print(f"    Mean:   {np.mean(total_a):.1f}", file=sys.stderr)
    print(f"    Median: {np.median(total_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(total_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(total_a, 75):.0f}", file=sys.stderr)

    print("  Likes per session:", file=sys.stderr)
    print(f"    Mean:   {np.mean(likes_a):.1f}", file=sys.stderr)
    print(f"    Median: {np.median(likes_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(likes_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(likes_a, 75):.0f}", file=sys.stderr)

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

    print("  Follows per session:", file=sys.stderr)
    print(f"    Mean:   {np.mean(follows_a):.2f}", file=sys.stderr)
    print(f"    Median: {np.median(follows_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(follows_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(follows_a, 75):.0f}", file=sys.stderr)

    print("  Other actions per session:", file=sys.stderr)
    print(f"    Mean:   {np.mean(other_a):.2f}", file=sys.stderr)
    print(f"    Median: {np.median(other_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(other_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(other_a, 75):.0f}", file=sys.stderr)

    print("  Interactions per session (likes + reposts):", file=sys.stderr)
    print(f"    Mean:   {np.mean(interactions_a):.1f}", file=sys.stderr)
    print(f"    Median: {np.median(interactions_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(interactions_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(interactions_a, 75):.0f}", file=sys.stderr)

    print("=" * 60 + "\n", file=sys.stderr)


if __name__ == "__main__":
    main()
