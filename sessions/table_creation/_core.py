"""
Shared core for session clustering — DB, fetch, table creation, batch loop.
"""

import os
import sys
import time as time_mod
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pymysql
from dotenv import load_dotenv
from running_locally.local_db import Where, get_connection as _local_connect

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

# Hardcoded: single data source
DIDS_SQL = """
    SELECT DISTINCT did
    FROM events
    ORDER BY did
"""

EVENTS_SQL = """
    SELECT did, time_us
    FROM events
    WHERE did IN ({placeholders})
    ORDER BY did, time_us
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_connection():
    """Return a DB connection (DuckDB if WHERE=local, otherwise pymysql)."""
    where = Where.from_env()
    if where == Where.LOCAL:
        return _local_connect(where, repo_root=str(ENV_PATH.parent))
    return pymysql.connect(**DB_CONFIG)

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


def load_dids(conn):
    return [row[0] for row in _execute(conn, DIDS_SQL)]


def fetch_user_timestamps(conn, dids):
    if not dids:
        return {}
    placeholders = ",".join(["%s"] * len(dids))
    query = EVENTS_SQL.format(placeholders=placeholders)
    result = defaultdict(list)
    for did, time_us in _execute(conn, query, dids):
        result[did].append(int(time_us))
    return dict(result)


def table_sql(target: str) -> dict:
    """Return CREATE and INSERT SQL for a given table name."""
    return {
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



# ---- Multiprocessing helper ----\ndef _cluster_one(args):\n    """Unpack args and call cluster_fn. For Pool.map."""\n    timestamps, cluster_fn = args\n    return cluster_fn(timestamps)\n
# ---------------------------------------------------------------------------
# Batch loop (shared by both methods)
# ---------------------------------------------------------------------------

def run_batch_loop(
    conn,
    target: str,
    dids: list[str],
    cluster_fn,
    batch_size: int = BATCH_SIZE,
    summary: bool = False,
    workers: int = 1,
):
    """Fetch events in batches, cluster each user, write sessions to *target*.

    When workers > 1, clustering runs in parallel via multiprocessing.Pool.
    """
    sql = table_sql(target)
    _execute(conn, sql["create"])
    conn.commit()
    print(f"Table {target} ready.", file=sys.stderr)

    batches = [dids[i:i + batch_size] for i in range(0, len(dids), batch_size)]
    total_batches = len(batches)

    insert_buffer = []
    total_sessions = 0
    seen_users = 0
    all_durations = []

    t0 = time_mod.time()

    for batch_idx, batch_dids in enumerate(batches):
        user_ts = fetch_user_timestamps(conn, batch_dids)

        # ── Cluster (serial or parallel) ──
        if workers > 1:
            items = list(user_ts.items())  # [(did, timestamps), ...]
            with Pool(workers) as pool:
                results = pool.map(_cluster_one,
                                   [(ts, cluster_fn) for _, ts in items])
            for (did, _), sessions in zip(items, results):
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
                        _flush_inserts(conn, sql["insert"], insert_buffer)
        else:
            for did in batch_dids:
                timestamps = user_ts.get(did, [])
                sessions = cluster_fn(timestamps)
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
                        _flush_inserts(conn, sql["insert"], insert_buffer)

        _flush_inserts(conn, sql["insert"], insert_buffer)

        if (batch_idx + 1) % 10 == 0 or batch_idx == total_batches - 1:
            elapsed = time_mod.time() - t0
            pct = 100 * (batch_idx + 1) / total_batches
            rate = (batch_idx + 1) * batch_size / elapsed if elapsed > 0 else 0
            print(
                f"  Batch {batch_idx + 1}/{total_batches} ({pct:.0f}%) | "
                f"{seen_users:,} users | {total_sessions:,} sessions | "
                f"{elapsed:.0f}s | ~{rate:.0f} users/s",
                file=sys.stderr,
            )

    _flush_inserts(conn, sql["insert"], insert_buffer)
    elapsed = time_mod.time() - t0
    print(f"\nDone in {elapsed:.0f}s", file=sys.stderr)

    if summary and all_durations:
        d = np.array(all_durations)
        print("\n" + "=" * 55, file=sys.stderr)
        print(f"  SESSION SUMMARY  (n={total_sessions:,} sessions, {seen_users:,} users)", file=sys.stderr)
        print("=" * 55, file=sys.stderr)
        print("-" * 55, file=sys.stderr)
        print("  Session duration (s):", file=sys.stderr)
        print(f"    Mean:   {np.mean(d):.0f}", file=sys.stderr)
        print(f"    Median: {np.median(d):.0f}", file=sys.stderr)
        print(f"    P25:    {np.percentile(d, 25):.0f}", file=sys.stderr)
        print(f"    P75:    {np.percentile(d, 75):.0f}", file=sys.stderr)
        print(f"    P90:    {np.percentile(d, 90):.0f}", file=sys.stderr)
        print("=" * 55 + "\n", file=sys.stderr)
