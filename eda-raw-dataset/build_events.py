"""Build the unified events table.

Merges bsky.records (minus fossils, minus feed.post) + bsky.posts
into a single (did, time_us, event_type) table. Filters out users
with <2 events per active day (tourists).

Output: pau_db.all_events_v2
"""

import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / ".env")

DB = {
    "host": os.environ["DATABASE_HOST"],
    "port": int(os.environ["DATABASE_PORT"]),
    "user": os.environ["DATABASE_USER"],
    "password": os.environ["PAU_PASSWORD"],
    "database": "pau_db",
    "charset": "utf8mb4",
}

EXCLUDE_COLLECTIONS = (
    "'app.bsky.feed.post'",
    "'app.bsky.graph.repost'",
    "'app.bsky.graph.verification'",
    "'app.bsky.lexicon.collection'",
    "'app.bsky.graph.cancellation'",
    "'app.bsky.draft.createDraft'",
)
EXCLUDE_SQL = " AND r.collection NOT IN (" + ", ".join(EXCLUDE_COLLECTIONS) + ")"


# ── Helpers ───────────────────────────────────────────────────────────────

def query(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def execute(conn, sql, label):
    print(f"  {label}...", end=" ", flush=True)
    with conn.cursor() as cur:
        cur.execute(sql)
    print("done.")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}  (database: pau_db)\n")

    # ── Drop & create table ──────────────────────────────────────────

    print("── Creating table ──")
    execute(conn, "DROP TABLE IF EXISTS all_events_v2", "drop old table")
    execute(conn, """
        CREATE TABLE all_events_v2 (
            did         VARCHAR(128) NOT NULL,
            time_us     BIGINT       NOT NULL,
            event_type  VARCHAR(32)  NOT NULL
        )
        ENGINE = OLAP
        DUPLICATE KEY(did, time_us)
        DISTRIBUTED BY HASH(did) BUCKETS 32
        PROPERTIES ("replication_num" = "1")
    """, "create table")

    # ── Populate ─────────────────────────────────────────────────────

    print("\n── Populating all_events_v2 ──")
    print("  (merging records + posts, filtering users with <2 events/day)")

    execute(conn, f"""
        INSERT INTO all_events_v2 (did, time_us, event_type)

        WITH user_rates AS (
            -- Pre-compute events/day for every user
            SELECT did,
                   COUNT(*) / GREATEST(
                       COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))), 1
                   ) AS events_per_day
            FROM (
                SELECT did, time_us
                FROM bsky.records r
                WHERE 1=1{EXCLUDE_SQL}
                UNION ALL
                SELECT did, time_us
                FROM bsky.posts
            ) e
            GROUP BY did
            HAVING COUNT(*) / GREATEST(
                       COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))), 1
                   ) >= 2
        )
        SELECT e.did, e.time_us, e.event_type
        FROM (
            -- Records
            SELECT r.did, r.time_us,
                   REPLACE(REPLACE(r.collection, 'app.bsky.', ''), '.', '_') AS event_type
            FROM bsky.records r
            WHERE 1=1{EXCLUDE_SQL}

            UNION ALL

            -- Top-level posts
            SELECT did, time_us, 'post_top' AS event_type
            FROM bsky.posts
            WHERE reply_root_uri IS NULL

            UNION ALL

            -- Replies
            SELECT did, time_us, 'post_reply' AS event_type
            FROM bsky.posts
            WHERE reply_root_uri IS NOT NULL
        ) e
        JOIN user_rates ur ON e.did = ur.did
    """, "insert data")

    # ── Validation ───────────────────────────────────────────────────

    print("\n── Validation ──")
    rows = query(conn, """
        SELECT 'total rows' AS metric, COUNT(*) AS value FROM all_events_v2
        UNION ALL
        SELECT 'distinct users', COUNT(DISTINCT did) FROM all_events_v2
        UNION ALL
        SELECT 'distinct event types', COUNT(DISTINCT event_type) FROM all_events_v2
    """)
    for metric, value in rows:
        print(f"  {metric}: {value:,}")

    # Event type breakdown
    rows = query(conn, """
        SELECT event_type, COUNT(*) AS cnt
        FROM all_events_v2
        GROUP BY event_type
        ORDER BY cnt DESC
    """)
    total = sum(r[1] for r in rows)
    print(f"\n  {'event_type':<30s} {'count':>12s}  {'%':>6s}")
    print(f"  {'-'*52}")
    for et, cnt in rows:
        print(f"  {et:<30s} {cnt:>12,}  {100*cnt/total:>5.1f}%")
    print(f"  {'TOTAL':<30s} {total:>12,}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
