#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "numpy",
# ]
# ///
"""
Tukey-session clustering — Bluesky firehose → pau_db.sessions_all.

Source: pau_db.all_events (all 6 major event types, ≥8 events per user,
         time-windowed to 2026-04-11 → 2026-04-18).

Method (per-user adaptive IQR / Tukey's fences):
  • Read ALL events for each user from pau_db.all_events.
    Event types: like, repost, follow, block, post, reply.
  • Compute inter-arrival gaps.
  • Per-user threshold = max(Q3 + 1.5 × IQR, 120 s).
    Fallback = 60 min if < 4 gaps or IQR = 0.
  • Cluster events into sessions wherever gap > threshold.
  • Write to pau_db.sessions_all.

Parameters (from new EDA, docs/EDA.md):
  • The all_events table already applies the ≥8 events filter
    (power-law xmin from all-events distribution, α=1.56).
  • IQR multiplier: 1.5
  • Gap floor: 120 seconds
  • Fallback threshold: 60 minutes (3600 s)
  • Min gaps for IQR: 4

Usage:
    uv run sessions/creation-tukey/cluster_all.py --summary
    uv run sessions/creation-tukey/cluster_all.py --min-events 8 --max-events 500 --summary
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
# Config — reads .env from repo root
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

BATCH_SIZE = 2000       # DIDs per SQL query
INSERT_FLUSH = 50_000   # rows per INSERT batch


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
    replies: int = 0
    follows: int = 0
    blocks: int = 0
    other: int = 0

    @property
    def duration_s(self) -> float:
        return (self.end_us - self.start_us) / 1_000_000

    @property
    def interactions(self) -> int:
        return self.likes + self.reposts

    @property
    def posts_authored(self) -> int:
        return self.posts + self.replies

    @property
    def total_actions(self) -> int:
        return (self.likes + self.reposts + self.posts + self.replies
                + self.follows + self.blocks + self.other)


def _inc(s: Session, action_type: str):
    # Map from all_events event_type naming (feed_like, feed_repost, etc.)
    if action_type == "feed_like":
        s.likes += 1
    elif action_type == "feed_repost":
        s.reposts += 1
    elif action_type == "post_top":
        s.posts += 1
    elif action_type == "post_reply":
        s.replies += 1
    elif action_type == "graph_follow":
        s.follows += 1
    elif action_type == "graph_block":
        s.blocks += 1
    else:
        s.other += 1


# ---------------------------------------------------------------------------
# Adaptive threshold (Tukey's fences)
# ---------------------------------------------------------------------------

def compute_user_threshold(
    gaps_s: np.ndarray,
    iqr_multiplier: float = 1.5,
    fallback_s: float = 3600.0,
    min_gaps: int = 4,
) -> tuple[float, bool]:
    """Return (threshold_seconds, used_fallback) for a user's gap distribution."""
    if len(gaps_s) < min_gaps:
        return fallback_s, True
    q1, q3 = np.percentile(gaps_s, [25, 75])
    iqr = q3 - q1
    if iqr <= 0:
        # All gaps are identical — can't compute a meaningful threshold.
        # Use fallback instead of an arbitrary tiny value.
        return fallback_s, True
    return max(q3 + iqr_multiplier * iqr, 120.0), False


def cluster_sessions_adaptive(
    timestamps_us: list[tuple[int, str]],
    iqr_multiplier: float = 1.5,
    fallback_s: float = 3600.0,
    min_gaps: int = 4,
) -> tuple[list[Session], float, bool]:
    """Cluster a user's events into sessions using their adaptive threshold."""
    if not timestamps_us:
        return [], 0.0, False

    times = np.array([t[0] for t in timestamps_us], dtype=np.int64)
    gaps_s = np.diff(times) / 1_000_000
    threshold_s, used_fallback = compute_user_threshold(
        gaps_s, iqr_multiplier, fallback_s, min_gaps,
    )
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
CREATE TABLE IF NOT EXISTS pau_db.sessions_all (
    `did`                     varchar(128) NOT NULL,
    `session_start`           bigint NOT NULL,
    `session_end`             bigint NOT NULL,
    `next_session_start`      bigint NULL,
    `duration_s`              double NOT NULL,
    `likes`                   int NOT NULL,
    `reposts`                 int NOT NULL,
    `posts_authored`          int NOT NULL,
    `replies`                 int NOT NULL,
    `follows`                 int NOT NULL,
    `blocks`                  int NOT NULL,
    `other_actions`           int NOT NULL,
    `interactions`            int NOT NULL,
    `total_actions`           int NOT NULL,
    `user_threshold_s`        double NOT NULL,
    `user_threshold_fallback` tinyint NOT NULL,
    `user_gap_count`          int NOT NULL
) ENGINE=OLAP
DUPLICATE KEY(`did`, `session_start`)
DISTRIBUTED BY HASH(`did`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1"
);
"""

INSERT_SQL = """
INSERT INTO pau_db.sessions_all
    (did, session_start, session_end, next_session_start,
     duration_s, likes, reposts, posts_authored, replies, follows, blocks,
     other_actions, interactions, total_actions,
     user_threshold_s, user_threshold_fallback, user_gap_count)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def fetch_events_for_dids(
    conn: pymysql.Connection,
    dids: list[str],
) -> dict[str, list[tuple[int, str]]]:
    """Return {did: [(time_us, event_type), ...]} sorted by time.

    Reads from pau_db.all_events (already time-windowed and filtered to
    ≥8 events per user).  Event types use the all_events naming convention:
    feed_like, feed_repost, graph_follow, graph_block, post_top, post_reply,
    plus minor types (feed_threadgate, graph_listitem, etc.).
    """
    if not dids:
        return {}

    placeholders = ",".join(["%s"] * len(dids))
    query = f"""
        SELECT did, time_us, event_type
        FROM pau_db.all_events
        WHERE did IN ({placeholders})
        ORDER BY did, time_us
    """

    result: dict[str, list[tuple[int, str]]] = defaultdict(list)
    with conn.cursor() as cursor:
        cursor.execute(query, dids)
        for did, time_us, event_type in cursor:
            result[did].append((int(time_us), event_type))
    return dict(result)


def load_dids_from_db(
    conn: pymysql.Connection,
    min_events: int,
    max_events: int | None,
) -> list[str]:
    """Return DIDs with event count in [min_events, max_events] from all_events.

    If max_events is None or 0, no upper bound is applied.
    """
    if max_events and max_events > 0:
        query = """
            SELECT did
            FROM pau_db.all_events
            GROUP BY did
            HAVING COUNT(*) >= %s AND COUNT(*) <= %s
            ORDER BY did
        """
        params = (min_events, max_events)
    else:
        query = """
            SELECT did
            FROM pau_db.all_events
            GROUP BY did
            HAVING COUNT(*) >= %s
            ORDER BY did
        """
        params = (min_events,)

    with conn.cursor() as cur:
        cur.execute(query, params)
        return [row[0] for row in cur]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Tukey-session clustering → pau_db.sessions_all"
    )
    parser.add_argument(
        "--min-events", type=int, default=8,
        help="Minimum events per user (default: 8, all_events already filters ≥8)",
    )
    parser.add_argument(
        "--max-events", type=int, default=0,
        help="Maximum events per user (default: 0 = no limit)",
    )
    parser.add_argument(
        "-q", "--iqr-multiplier", type=float, default=1.5,
        help="Tukey IQR multiplier (default: 1.5)",
    )
    parser.add_argument(
        "-G", "--fallback-threshold", type=float, default=60.0,
        help="Fallback threshold in minutes when < 4 gaps (default: 60)",
    )
    parser.add_argument(
        "--min-gaps", type=int, default=4,
        help="Minimum gaps required for per-user IQR (default: 4)",
    )
    parser.add_argument(
        "--min-actions", type=int, default=2,
        help="Minimum total actions for a user to be processed (default: 2)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help=f"DIDs per event-fetch query (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Print aggregate statistics after completion",
    )
    args = parser.parse_args()

    max_events = args.max_events if args.max_events > 0 else None

    # -------------------------------------------------------------------
    # Get DIDs from all_events
    # -------------------------------------------------------------------
    conn_sr = pymysql.connect(**DB_CONFIG)

    print(
        f"Querying DIDs with ≥{args.min_events} events "
        f"from pau_db.all_events ...",
        file=sys.stderr,
    )
    t_dids = time_mod.time()
    all_dids = load_dids_from_db(conn_sr, args.min_events, max_events)
    print(
        f"  → {len(all_dids):,} DIDs in {time_mod.time() - t_dids:.0f}s",
        file=sys.stderr,
    )

    if not all_dids:
        print("No DIDs to process.  Run EDA/01_create_all_events.sql first.",
              file=sys.stderr)
        conn_sr.close()
        sys.exit(1)

    fallback_s = args.fallback_threshold * 60
    batches = [
        all_dids[i : i + args.batch_size]
        for i in range(0, len(all_dids), args.batch_size)
    ]
    total_batches = len(batches)

    # -------------------------------------------------------------------
    # Create output table
    # -------------------------------------------------------------------
    with conn_sr.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn_sr.commit()
    print("Table pau_db.sessions_all ready.", file=sys.stderr)

    # -------------------------------------------------------------------
    # Cluster
    # -------------------------------------------------------------------
    all_durations: list[float] = []
    all_likes: list[int] = []
    all_reposts: list[int] = []
    all_posts: list[int] = []
    all_replies: list[int] = []
    all_follows: list[int] = []
    all_blocks: list[int] = []
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
        insert_buffer.clear()

    t0 = time_mod.time()

    for batch_idx, batch_dids in enumerate(batches):
        actions_by_did = fetch_events_for_dids(conn_sr, batch_dids)

        for did in batch_dids:
            timestamps = actions_by_did.get(did, [])
            if len(timestamps) < args.min_actions:
                continue

            sessions, threshold_s, used_fallback = cluster_sessions_adaptive(
                timestamps,
                args.iqr_multiplier,
                fallback_s,
                args.min_gaps,
            )

            seen_users.add(did)
            if used_fallback:
                fallback_users.add(did)
            total_sessions += len(sessions)
            gap_count = max(len(timestamps) - 1, 0)

            for i, s in enumerate(sessions):
                next_start = sessions[i + 1].start_us if i + 1 < len(sessions) else None
                insert_buffer.append((
                    did,
                    s.start_us,
                    s.end_us,
                    next_start,
                    round(s.duration_s, 3),
                    s.likes,
                    s.reposts,
                    s.posts_authored,
                    s.replies,
                    s.follows,
                    s.blocks,
                    s.other,
                    s.interactions,
                    s.total_actions,
                    round(threshold_s, 1),
                    1 if used_fallback else 0,
                    gap_count,
                ))

                all_durations.append(s.duration_s)
                all_likes.append(s.likes)
                all_reposts.append(s.reposts)
                all_posts.append(s.posts)
                all_replies.append(s.replies)
                all_follows.append(s.follows)
                all_blocks.append(s.blocks)
                all_other.append(s.other)
                all_interactions.append(s.interactions)
                all_total.append(s.total_actions)

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
    conn_sr.close()

    elapsed = time_mod.time() - t0
    print(f"\nDone in {elapsed:.0f}s", file=sys.stderr)

    if args.summary and all_durations:
        _print_summary(
            all_durations, all_likes, all_reposts, all_posts, all_replies,
            all_follows, all_blocks, all_other, all_interactions, all_total,
            len(fallback_users), len(seen_users), args,
        )


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(
    all_durations, all_likes, all_reposts, all_posts, all_replies,
    all_follows, all_blocks, all_other, all_interactions, all_total,
    fallback_count, total_users, args,
):
    durations = np.array(all_durations)
    likes_a = np.array(all_likes)
    reposts_a = np.array(all_reposts)
    posts_a = np.array(all_posts)
    replies_a = np.array(all_replies)
    follows_a = np.array(all_follows)
    blocks_a = np.array(all_blocks)
    other_a = np.array(all_other)
    interactions_a = np.array(all_interactions)
    total_a = np.array(all_total)

    n = len(durations)

    def _p(arr, pct):
        return np.percentile(arr, pct)

    header = f"  SESSION ANALYSIS SUMMARY — sessions_all  (n={n:,} sessions, {total_users:,} users)"
    print("\n" + "=" * 60, file=sys.stderr)
    print(header, file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(
        f"  IQR multiplier: {args.iqr_multiplier}  |  "
        f"Fallback: {args.fallback_threshold} min  |  "
        f"Event filter: ≥{args.min_events}",
        file=sys.stderr,
    )
    if total_users > 0:
        print(
            f"  Users on fallback threshold: {fallback_count}/{total_users} "
            f"({100 * fallback_count / total_users:.0f}%)",
            file=sys.stderr,
        )
    print("-" * 60, file=sys.stderr)

    print("  Session duration (s):", file=sys.stderr)
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

    print("  Replies per session:", file=sys.stderr)
    print(f"    Mean:   {np.mean(replies_a):.2f}", file=sys.stderr)
    print(f"    Median: {np.median(replies_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(replies_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(replies_a, 75):.0f}", file=sys.stderr)

    print("  Follows per session:", file=sys.stderr)
    print(f"    Mean:   {np.mean(follows_a):.2f}", file=sys.stderr)
    print(f"    Median: {np.median(follows_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(follows_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(follows_a, 75):.0f}", file=sys.stderr)

    print("  Interactions per session (likes + reposts):", file=sys.stderr)
    print(f"    Mean:   {np.mean(interactions_a):.1f}", file=sys.stderr)
    print(f"    Median: {np.median(interactions_a):.0f}", file=sys.stderr)
    print(f"    P25:    {_p(interactions_a, 25):.0f}", file=sys.stderr)
    print(f"    P75:    {_p(interactions_a, 75):.0f}", file=sys.stderr)

    print("=" * 60 + "\n", file=sys.stderr)


if __name__ == "__main__":
    main()
