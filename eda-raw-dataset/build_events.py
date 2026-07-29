"""Build the unified events table.

Merges bsky.records (minus fossils, minus feed.post) + bsky.posts
into a single (did, time_us, event_type) table. Filters out users
with <2 events per active day (tourists).

Output: events table (local: data/local.duckdb, server: pau_db.events)
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from running_locally.local_db import Where, get_connection as local_connect

# ── Config ────────────────────────────────────────────────────────────────

load_dotenv(REPO / ".env")

WHERE = Where.from_env()

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

# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = local_connect(WHERE, repo_root=str(REPO))
    print(f"Connected ({WHERE.value})\n")

    # ── Drop & create table ──────────────────────────────────────────

    print("── Creating table ──")
    conn.execute("DROP TABLE IF EXISTS events", "drop old table")
    conn.execute("""
        CREATE TABLE events (
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

    print("\n── Populating events ──")
    print("  (merging records + posts, filtering users with <2 events/day)")

    conn.execute( f"""
        INSERT INTO events (did, time_us, event_type)

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
    rows = conn.query("""
        SELECT 'total rows' AS metric, COUNT(*) AS value FROM events
        UNION ALL
        SELECT 'distinct users', COUNT(DISTINCT did) FROM events
        UNION ALL
        SELECT 'distinct event types', COUNT(DISTINCT event_type) FROM events
    """)
    for metric, value in rows:
        print(f"  {metric}: {value:,}")

    # Event type breakdown
    rows = conn.query("""
        SELECT event_type, COUNT(*) AS cnt
        FROM events
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
