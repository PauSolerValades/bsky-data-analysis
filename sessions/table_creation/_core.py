"""
Shared core for session clustering — DB, fetch, table creation, batch loop.
"""

import sys
import time as time_mod
from pathlib import Path

import numpy as np
from running_locally.local_db import Where, get_connection as _local_connect

WHERE = Where.from_env()
TBL_PREFIX = "pau_db." if WHERE == Where.SERVER else ""

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

BATCH_SIZE = 1000
EVENTS_TABLE = "events"  # ponytail: "events_sample" for sweep runs, "events" for production

DIDS_SQL = f"""
    SELECT DISTINCT did
    FROM {TBL_PREFIX}{EVENTS_TABLE}
    ORDER BY did
"""

EVENTS_SQL = f"""
    SELECT did, time_us
    FROM {TBL_PREFIX}{EVENTS_TABLE}
    WHERE did IN ({{placeholders}})
    ORDER BY did, time_us
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_connection():
    """Return a DB connection via local_db (StarRocks or DuckDB)."""
    return _local_connect(WHERE, repo_root=str(REPO_ROOT))

def _execute(conn, query, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


_INSERT_CHUNK = 10_000  # ponytail: StarRocks expr_children_limit

def _insert_batch(conn, table_full: str, rows: list):
    """Insert rows in chunks of 10K (StarRocks limit per INSERT)."""
    while rows:
        chunk = rows[:_INSERT_CHUNK]
        del rows[:_INSERT_CHUNK]
        placeholders = ",".join(["(%s,%s,%s,%s,%s)"] * len(chunk))
        sql = f"INSERT INTO {table_full} (did, session_start, session_end, duration_s, is_singleton) VALUES {placeholders}"
        flat = [v for row in chunk for v in row]
        _execute(conn, sql, flat)
        conn.commit()


def load_dids(conn):
    return [row[0] for row in _execute(conn, DIDS_SQL)]


def fetch_user_timestamps(conn, dids):
    """Return {did: [timestamp_us, ...]} from rows already sorted by did, time_us.

    StarRocks streams sorted rows efficiently; we group by detecting did changes
    instead of building a defaultdict or using expensive GROUP_CONCAT.
    """
    if not dids:
        return {}
    placeholders = ",".join(["%s"] * len(dids))
    query = EVENTS_SQL.format(placeholders=placeholders)
    result = {}
    cur_did = None
    cur_ts = []
    for did, time_us in _execute(conn, query, dids):
        if did != cur_did:
            if cur_did is not None:
                result[cur_did] = cur_ts
            cur_did = did
            cur_ts = [int(time_us)]
        else:
            cur_ts.append(int(time_us))
    if cur_did is not None:
        result[cur_did] = cur_ts
    return result


def table_sql(target: str) -> str:
    """Return CREATE TABLE SQL for a given table name."""
    full = f"{TBL_PREFIX}{target}"
    return f"""
        CREATE TABLE IF NOT EXISTS {full} (
            `did`           varchar(128) NOT NULL,
            `session_start` bigint NOT NULL,
            `session_end`   bigint NOT NULL,
            `duration_s`    double NOT NULL,
            `is_singleton`  tinyint NOT NULL
        ) ENGINE=OLAP
        DUPLICATE KEY(`did`, `session_start`)
        DISTRIBUTED BY HASH(`did`) BUCKETS 32
        PROPERTIES ("replication_num" = "1")
    """



# ---------------------------------------------------------------------------
# Batch loop
# ---------------------------------------------------------------------------

def run_batch_loop(
    conn,
    target: str,
    dids: list[str],
    cluster_fn,
    batch_size: int = BATCH_SIZE,
    summary: bool = False,
):
    """Fetch events in batches, cluster each user, write sessions to *target*."""
    create_sql = table_sql(target)
    _execute(conn, create_sql)
    conn.commit()
    print(f"Table {target} ready.", file=sys.stderr)

    batches = [dids[i:i + batch_size] for i in range(0, len(dids), batch_size)]
    total_batches = len(batches)

    insert_buffer = []
    total_sessions = 0
    seen_users = 0
    all_durations = []
    table_full = f"{TBL_PREFIX}{target}"

    t0 = time_mod.time()

    for batch_idx, batch_dids in enumerate(batches):
        user_ts = fetch_user_timestamps(conn, batch_dids)

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

        _insert_batch(conn, table_full, insert_buffer)

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
