#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["atproto>=0.0.50", "pymysql", "python-dotenv", "requests"]
# ///
"""
Proportional batch crawler — every batch mirrors the follower-count distribution
of the full population.  Proportions are computed once at startup and held fixed
for all batches.  The empirical distribution comes from followers_from_data (the
firehose edges), which already follows a power law.

Usage:
  uv run topology-crawl/crawl_followers.py                    # start / resume
  uv run topology-crawl/crawl_followers.py --batch-size 5000  # smaller batches
  uv run topology-crawl/crawl_followers.py --dry-run          # print batch plan
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
from collections import OrderedDict
from pathlib import Path

import pymysql
import requests
from atproto import Client
from dotenv import load_dotenv

# ── Configuration ────────────────────────────────────────────────────────────

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)


def _require(key: str) -> str:
    val = os.environ.get(key)
    if val is None:
        print(f"Missing env var: {key} (check .env)", file=sys.stderr)
        sys.exit(1)
    return val


SR_CFG = {
    "host": _require("DATABASE_HOST"),
    "port": int(_require("DATABASE_PORT")),
    "user": _require("DATABASE_USER"),
    "password": _require("PAU_PASSWORD"),
    "database": "pau_db",
    "charset": "utf8mb4",
}

BSKY_HANDLE = _require("BSKY_HANDLE")
BSKY_APP_PASSWORD = _require("BSKY_APP_PASSWORD")

API_BASE = "https://bsky.social/xrpc"
FOLLOWERS_URL = f"{API_BASE}/app.bsky.graph.getFollowers"
PAGE_SIZE = 100
EDGE_BATCH_SIZE = 500
DEFAULT_BATCH_SIZE = 10_000

SKIP_SUBSTRINGS = (
    "profile not found", "account not found", "actor not found",
    "deactivated", "takendown", "block list violation",
)

# Log-scale follower-count buckets — boundaries are [lo, hi] inclusive.
# Order matters: keep biggest-first so allocation distributes remainder sensibly.
BUCKETS: list[tuple[int, int]] = [
    (8192, 10_000_000),
    (4096, 8191),
    (2048, 4095),
    (1024, 2047),
    (512, 1023),
    (256, 511),
    (128, 255),
    (64, 127),
    (32, 63),
    (16, 31),
    (8, 15),
    (4, 7),
    (2, 3),
    (1, 1),
    (0, 0),
]


# ── Database helpers ─────────────────────────────────────────────────────────

def db_connect() -> pymysql.Connection:
    return pymysql.connect(**SR_CFG)


def compute_full_population(conn: pymysql.Connection) -> dict[tuple[int, int], int]:
    """Return {bucket: count} for the FULL active-user population (fixed, never changes)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                CASE
                    WHEN fc >= 8192                   THEN 8192
                    WHEN fc BETWEEN 4096 AND 8191     THEN 4096
                    WHEN fc BETWEEN 2048 AND 4095     THEN 2048
                    WHEN fc BETWEEN 1024 AND 2047     THEN 1024
                    WHEN fc BETWEEN  512 AND 1023     THEN 512
                    WHEN fc BETWEEN  256 AND  511     THEN 256
                    WHEN fc BETWEEN  128 AND  255     THEN 128
                    WHEN fc BETWEEN   64 AND  127     THEN 64
                    WHEN fc BETWEEN   32 AND   63     THEN 32
                    WHEN fc BETWEEN   16 AND   31     THEN 16
                    WHEN fc BETWEEN    8 AND   15     THEN 8
                    WHEN fc BETWEEN    4 AND    7     THEN 4
                    WHEN fc BETWEEN    2 AND    3     THEN 2
                    WHEN fc = 1                       THEN 1
                    ELSE 0
                END AS bucket_lo,
                COUNT(*) AS users
            FROM (
                SELECT u.did, COALESCE(f.fc, 0) AS fc
                FROM pau_db.users u
                LEFT JOIN (
                    SELECT followee_did, COUNT(*) AS fc
                    FROM pau_db.followers_from_data
                    GROUP BY followee_did
                ) f ON u.did = f.followee_did
                WHERE NOT (u.num_posts   = 0 AND u.num_likes   = 0
                       AND u.num_reposts = 0 AND u.num_follows = 0)
            ) t
            GROUP BY bucket_lo
        """)
        rows = cur.fetchall()

    # Build result, preserving bucket order
    result: dict[tuple[int, int], int] = OrderedDict()
    lo_to_hi = {b[0]: b[1] for b in BUCKETS if b[0] == b[1] or b[0] > 1}
    # Also handle special buckets
    for bucket_lo, count in rows:
        lo = int(bucket_lo)
        if lo == 8192:
            result[(8192, 10_000_000)] = count
        elif lo == 4096:
            result[(4096, 8191)] = count
        elif lo == 2048:
            result[(2048, 4095)] = count
        elif lo == 1024:
            result[(1024, 2047)] = count
        elif lo == 512:
            result[(512, 1023)] = count
        elif lo == 256:
            result[(256, 511)] = count
        elif lo == 128:
            result[(128, 255)] = count
        elif lo == 64:
            result[(64, 127)] = count
        elif lo == 32:
            result[(32, 63)] = count
        elif lo == 16:
            result[(16, 31)] = count
        elif lo == 8:
            result[(8, 15)] = count
        elif lo == 4:
            result[(4, 7)] = count
        elif lo == 2:
            result[(2, 3)] = count
        elif lo == 1:
            result[(1, 1)] = count
        else:
            result[(0, 0)] = count
    return result


def compute_fixed_allocation(
    population: dict[tuple[int, int], int], batch_size: int
) -> list[tuple[tuple[int, int], int]]:
    """
    Return [(bucket, slots_per_batch), ...] with slots proportional to full
    population.  Every bucket that has ≥1 user gets at least 1 slot.
    Fixed once — same for every batch.
    """
    total = sum(population.values())
    allocation: list[tuple[tuple[int, int], int]] = []
    allocated = 0

    for bucket in BUCKETS:
        count = population.get(bucket, 0)
        if count == 0:
            continue
        # Floor proportional, but at least 1
        slots = max(1, int(batch_size * count / total))
        slots = min(slots, count)
        allocation.append((bucket, slots))
        allocated += slots

    # Distribute remainder to largest buckets (they're already at the front)
    remainder = batch_size - allocated
    for i, (bucket, slots) in enumerate(allocation):
        if remainder <= 0:
            break
        count = population[bucket]
        available = count - slots
        give = min(remainder, available)
        if give > 0:
            allocation[i] = (bucket, slots + give)
            remainder -= give

    return allocation


def pick_users_from_bucket(
    conn: pymysql.Connection, cur, lo: int, hi: int, n: int
) -> list[str]:
    """Return up to `n` random uncrawled DIDs from [lo, hi] follower-count bucket."""
    if n <= 0:
        return []
    cur.execute("""
        SELECT t.did
        FROM (
            SELECT u.did, COALESCE(f.fc, 0) AS fc
            FROM pau_db.users u
            LEFT JOIN (
                SELECT followee_did, COUNT(*) AS fc
                FROM pau_db.followers_from_data
                GROUP BY followee_did
            ) f ON u.did = f.followee_did
            LEFT JOIN pau_db.crawl_state cs ON u.did = cs.did
            WHERE cs.did IS NULL
              AND NOT (u.num_posts   = 0 AND u.num_likes   = 0
                   AND u.num_reposts = 0 AND u.num_follows = 0)
        ) t
        WHERE t.fc BETWEEN %s AND %s
        ORDER BY RAND()
        LIMIT %s
    """, (lo, hi, n))
    return [row[0] for row in cur.fetchall()]


def count_remaining(conn: pymysql.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*)
            FROM pau_db.users u
            LEFT JOIN pau_db.crawl_state cs ON u.did = cs.did
            WHERE cs.did IS NULL
              AND NOT (u.num_posts   = 0 AND u.num_likes   = 0
                   AND u.num_reposts = 0 AND u.num_follows = 0)
        """)
        return cur.fetchone()[0]


def mark_crawled(cur, did: str, follower_count: int):
    cur.execute(
        "INSERT INTO crawl_state (did, crawled_at, follower_count) VALUES (%s, NOW(), %s)",
        (did, follower_count),
    )


def flush_edges(cur, batch: list[tuple[str, str]], conn):
    if not batch:
        return
    cur.executemany(
        "INSERT INTO crawled_followers (follower_did, followee_did, crawled_at) "
        "VALUES (%s, %s, NOW())",
        batch,
    )
    conn.commit()


# ── Bluesky auth ─────────────────────────────────────────────────────────────

def get_access_token() -> str:
    client = Client()
    client.login(BSKY_HANDLE, BSKY_APP_PASSWORD)
    return client._session.access_jwt  # type: ignore[attr-defined]


# ── Rate-limit helpers ───────────────────────────────────────────────────────

def _header_int(resp: requests.Response, name: str) -> int | None:
    try:
        return int(resp.headers[name])
    except (KeyError, ValueError, TypeError):
        return None


def handle_rate_limit(resp: requests.Response):
    remaining = _header_int(resp, "RateLimit-Remaining")
    if remaining is not None and remaining <= 1:
        reset_at = _header_int(resp, "RateLimit-Reset")
        if reset_at is not None:
            wait = reset_at - time.time() + 1.0
            if wait > 0:
                print(f"  [rate-limit] {remaining} reqs left, sleeping {wait:.0f}s…",
                      file=sys.stderr, flush=True)
                time.sleep(wait)


def handle_429(resp: requests.Response):
    retry_after = _header_int(resp, "Retry-After")
    if retry_after is not None:
        wait = retry_after + 0.5
    else:
        reset_at = _header_int(resp, "RateLimit-Reset")
        wait = max(reset_at - time.time() + 1.0, 5.0) if reset_at else 30.0
    print(f"  [429] rate-limited, sleeping {wait:.0f}s…",
          file=sys.stderr, flush=True)
    time.sleep(wait)


# ── Paginated follower fetch ─────────────────────────────────────────────────

def fetch_followers(token: str, actor: str) -> tuple[list[str], bool]:
    followers: list[str] = []
    cursor: str | None = None
    failures = 0
    MAX_FAILURES = 5

    while True:
        params = {"actor": actor, "limit": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(
            FOLLOWERS_URL, params=params,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )

        if resp.status_code == 401:
            raise RuntimeError("Access token expired (HTTP 401)")

        if resp.status_code == 429:
            handle_429(resp)
            continue

        if 400 <= resp.status_code < 500:
            msg = resp.text.lower()
            if any(k in msg for k in SKIP_SUBSTRINGS):
                return followers, False
            failures += 1
            if failures > MAX_FAILURES:
                return followers, False
            time.sleep(failures * 5)
            continue

        if resp.status_code >= 500:
            failures += 1
            if failures > MAX_FAILURES:
                return followers, False
            time.sleep(failures * 10)
            continue

        failures = 0
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            failures += 1
            if failures > MAX_FAILURES:
                return followers, False
            time.sleep(failures * 5)
            continue

        page_followers = [f["did"] for f in data.get("followers", [])]
        followers.extend(page_followers)
        handle_rate_limit(resp)
        cursor = data.get("cursor")
        if not cursor:
            break

    return followers, True


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Proportional batch crawler — fixed power-law sampling"
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Users per batch (default: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print allocation plan and exit")
    args = parser.parse_args()

    conn = db_connect()
    cur = conn.cursor()

    # ── Pre-compute fixed proportions from full population ───────────────
    print("Computing population distribution…", file=sys.stderr)
    full_pop = compute_full_population(conn)
    total_pop = sum(full_pop.values())
    allocation = compute_fixed_allocation(full_pop, args.batch_size)

    print(f"\nFull population: {total_pop:,} active users", file=sys.stderr)
    print(f"Batch size:      {args.batch_size:,} users", file=sys.stderr)
    print(f"\nFixed allocation per batch:", file=sys.stderr)
    for (lo, hi), slots in allocation:
        count = full_pop[(lo, hi)]
        pct = count / total_pop * 100
        label = str(lo) if lo == hi else f"{lo}–{hi}"
        print(f"  {label:>10} followers → {slots:>5} of {count:>9}  ({pct:5.1f}%)",
              file=sys.stderr)
    print(f"  {'':>10}            {'─'*5}   {'─'*9}", file=sys.stderr)
    print(f"  {'TOTAL':>10}            {sum(s for _, s in allocation):>5}",
          file=sys.stderr)

    if args.dry_run:
        cur.close()
        conn.close()
        return

    # ── State ────────────────────────────────────────────────────────────
    batch_num = 0
    total_crawled = 0
    total_edges = 0
    t_start = time.time()
    shutdown = False

    def _on_signal(signum, frame):
        nonlocal shutdown
        if shutdown:
            return
        shutdown = True
        print("\nShutting down after current user…", file=sys.stderr)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    print("\nAuthenticating with Bluesky…", file=sys.stderr)
    token = get_access_token()
    last_login = time.time()
    LOGIN_TTL = 3600

    while not shutdown:
        remaining = count_remaining(conn)
        if remaining == 0:
            print("\nAll users crawled!", file=sys.stderr)
            break

        batch_num += 1
        batch_size = min(args.batch_size, remaining)

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  BATCH {batch_num}: {batch_size:,} users  "
              f"({remaining:,} remaining, {total_crawled:,} done)",
              file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

        # ── Pick users from each bucket (proportional to fixed allocation) ─
        batch_dids: list[str] = []
        for (lo, hi), slots in allocation:
            # Scale slots to actual batch size if smaller than default
            n = max(1, int(slots * batch_size / args.batch_size)) if slots > 0 else 0
            n = min(n, batch_size - len(batch_dids))
            if n <= 0:
                continue
            dids = pick_users_from_bucket(conn, cur, lo, hi, n)
            batch_dids.extend(dids)

        # Pad with any users if we fell short (sparse buckets)
        if len(batch_dids) < batch_size:
            print(f"  (picked {len(batch_dids)}, shortfall {batch_size - len(batch_dids)} "
                  f"— topping up from remaining pool)", file=sys.stderr)
            # Pick any remaining users
            cur.execute("""
                SELECT u.did
                FROM pau_db.users u
                LEFT JOIN pau_db.crawl_state cs ON u.did = cs.did
                WHERE cs.did IS NULL
                  AND NOT (u.num_posts   = 0 AND u.num_likes   = 0
                       AND u.num_reposts = 0 AND u.num_follows = 0)
                ORDER BY RAND()
                LIMIT %s
            """, (batch_size - len(batch_dids),))
            extra = [row[0] for row in cur.fetchall()]
            batch_dids.extend(extra)

        random.shuffle(batch_dids)

        # ── Crawl ────────────────────────────────────────────────────────
        edge_buffer: list[tuple[str, str]] = []

        for idx, did in enumerate(batch_dids):
            if shutdown:
                flush_edges(cur, edge_buffer, conn)
                conn.commit()
                break

            if time.time() - last_login > LOGIN_TTL:
                print("  Re-authenticating…", file=sys.stderr, flush=True)
                token = get_access_token()
                last_login = time.time()

            did_short = did if len(did) <= 45 else did[:42] + "…"
            elapsed = time.time() - t_start
            rate = total_crawled / elapsed * 60 if elapsed > 0 else 0
            print(f"\n  [{batch_num}.{idx + 1}/{len(batch_dids)}] {did_short}  "
                  f"({total_crawled:,} total, {rate:.1f}/min, {total_edges:,} edges)",
                  file=sys.stderr, flush=True)

            try:
                follower_dids, ok = fetch_followers(token, did)
            except RuntimeError as exc:
                if "401" in str(exc):
                    print("  -> token expired, re-auth…", file=sys.stderr, flush=True)
                    token = get_access_token()
                    last_login = time.time()
                    try:
                        follower_dids, ok = fetch_followers(token, did)
                    except Exception as exc2:
                        print(f"  -> ERROR: {exc2!r}", file=sys.stderr, flush=True)
                        mark_crawled(cur, did, 0)
                        conn.commit()
                        total_crawled += 1
                        continue
                else:
                    raise
            except Exception as exc:
                print(f"  -> UNEXPECTED ERROR: {exc!r}", file=sys.stderr, flush=True)
                mark_crawled(cur, did, 0)
                conn.commit()
                total_crawled += 1
                continue

            if not ok:
                print(f"  -> unreachable", file=sys.stderr, flush=True)
                mark_crawled(cur, did, 0)
                conn.commit()
                total_crawled += 1
                continue

            for fid in follower_dids:
                edge_buffer.append((fid, did))

            if len(edge_buffer) >= EDGE_BATCH_SIZE:
                flush_edges(cur, edge_buffer, conn)
                edge_buffer.clear()

            mark_crawled(cur, did, len(follower_dids))
            conn.commit()
            total_crawled += 1
            total_edges += len(follower_dids)

            print(f"  -> {len(follower_dids):,} followers  "
                  f"({total_edges:,} edges)", file=sys.stderr, flush=True)

        if edge_buffer:
            flush_edges(cur, edge_buffer, conn)

        print(f"\n  Batch {batch_num} complete — {total_crawled:,} users, "
              f"{total_edges:,} edges  ({ (time.time() - t_start) / 60:.1f} min)",
              file=sys.stderr, flush=True)

    cur.close()
    conn.close()

    elapsed = time.time() - t_start
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Done in {elapsed/60:.1f} min", file=sys.stderr)
    print(f"  Users crawled: {total_crawled:,}", file=sys.stderr)
    print(f"  Edges:         {total_edges:,}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
