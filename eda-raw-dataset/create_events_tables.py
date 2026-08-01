"""Build raw_events and events tables.

Two tables from the same source merge (bsky.records + bsky.posts):

  raw_events — unfiltered dump:
    • All record operations (create, update, delete)
    • All collections (including fossils)
    • All users (no tourist filter)
    • Excludes only feed.post from records (uses bsky.posts instead)

  events — filtered:
    • Record creates only (no updates/deletes)
    • Fossil collections excluded
    • Users with <2 events/day excluded (tourists)

Output: raw_events + events tables (local: DuckDB, server: pau_db)
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from running_locally.local_db import Where, get_connection as local_connect

# ── Config ────────────────────────────────────────────────────────────────

load_dotenv(REPO / ".env")

WHERE = Where.from_env()
TBL = "pau_db." if WHERE == Where.SERVER else ""

# ponytail: use created_at (user-side) not time_us (server-receive).
# Server-side timestamps cluster during relay bursts → fake micro-sessions.
# Deletes/updates have NULL created_at → fall back to time_us.
TS_EXPR_RECORDS = "COALESCE(UNIX_TIMESTAMP(r.created_at) * 1000000, r.time_us)"
TS_EXPR_POSTS = "UNIX_TIMESTAMP(p.created_at) * 1000000"

FOSSILS = (
    "'app.bsky.graph.repost'",
    "'app.bsky.graph.verification'",
    "'app.bsky.lexicon.collection'",
    "'app.bsky.graph.cancellation'",
    "'app.bsky.draft.createDraft'",
)
FOSSIL_SQL = " AND r.collection NOT IN (" + ", ".join(FOSSILS) + ")"

DDL = f"""
    CREATE TABLE {{tbl}} (
        did         VARCHAR(128) NOT NULL,
        time_us     BIGINT       NOT NULL,
        event_type  VARCHAR(32)  NOT NULL
    )
    ENGINE = OLAP
    DUPLICATE KEY(did, time_us)
    DISTRIBUTED BY HASH(did) BUCKETS 32
    PROPERTIES ("replication_num" = "1")
"""


# ── Helpers ───────────────────────────────────────────────────────────────

def validate(conn, tbl):
    print(f"\n── Validation: {tbl} ──")
    rows = conn.query(f"""
        SELECT 'total rows' AS metric, COUNT(*) AS value FROM {tbl}
        UNION ALL
        SELECT 'distinct users', COUNT(DISTINCT did) FROM {tbl}
        UNION ALL
        SELECT 'distinct event types', COUNT(DISTINCT event_type) FROM {tbl}
    """)
    for metric, value in rows:
        print(f"  {metric}: {value:,}")

    rows = conn.query(f"""
        SELECT event_type, COUNT(*) AS cnt
        FROM {tbl}
        GROUP BY event_type
        ORDER BY cnt DESC
    """)
    total = sum(r[1] for r in rows)
    print(f"\n  {'event_type':<30s} {'count':>12s}  {'%':>6s}")
    print(f"  {'-'*52}")
    for et, cnt in rows:
        print(f"  {et:<30s} {cnt:>12,}  {100*cnt/total:>5.1f}%")
    print(f"  {'TOTAL':<30s} {total:>12,}")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = local_connect(WHERE, repo_root=str(REPO))
    print(f"Connected ({WHERE.value})\n")

    # ═══════════════════════════════════════════════════════════════════
    # 1. raw_events — unfiltered dump
    # ═══════════════════════════════════════════════════════════════════

    raw = TBL + "raw_events"
    print("── Creating raw_events (unfiltered dump) ──")
    conn.execute(f"DROP TABLE IF EXISTS {raw}", "drop old raw_events")
    conn.execute(DDL.format(tbl=raw), "create raw_events")

    conn.execute(f"""
        INSERT INTO {raw} (did, time_us, event_type)
        SELECT e.did, e.time_us, e.event_type
        FROM (
            -- Records: all operations, all collections (except feed.post)
            SELECT r.did, {TS_EXPR_RECORDS} AS time_us,
                   REPLACE(REPLACE(r.collection, 'app.bsky.', ''), '.', '_') AS event_type
            FROM bsky.records r
            WHERE r.collection != 'app.bsky.feed.post'

            UNION ALL

            -- Top-level posts
            SELECT p.did, {TS_EXPR_POSTS} AS time_us, 'post_top' AS event_type
            FROM bsky.posts p
            WHERE p.reply_root_uri IS NULL

            UNION ALL

            -- Replies
            SELECT p.did, {TS_EXPR_POSTS} AS time_us, 'post_reply' AS event_type
            FROM bsky.posts p
            WHERE p.reply_root_uri IS NOT NULL
        ) e
    """, "insert raw_events")

    validate(conn, raw)

    # ═══════════════════════════════════════════════════════════════════
    # 2. events — filtered (create-only, no fossils, no tourists)
    # ═══════════════════════════════════════════════════════════════════

    evt = TBL + "events"
    print("\n\n── Creating events (filtered) ──")
    print("  (create-only, no fossils, ≥2 events/day)")
    conn.execute(f"DROP TABLE IF EXISTS {evt}", "drop old events")
    conn.execute(DDL.format(tbl=evt), "create events")

    conn.execute(f"""
        INSERT INTO {evt} (did, time_us, event_type)

        WITH user_rates AS (
            SELECT did,
                   COUNT(*) / GREATEST(
                       COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))), 1
                   ) AS events_per_day
            FROM (
                SELECT did, {TS_EXPR_RECORDS} AS time_us
                FROM bsky.records r
                WHERE r.operation = 'create'{FOSSIL_SQL}
                UNION ALL
                SELECT did, {TS_EXPR_POSTS} AS time_us
                FROM bsky.posts p
            ) e
            GROUP BY did
            HAVING COUNT(*) / GREATEST(
                       COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))), 1
                   ) >= 2
        )
        SELECT e.did, e.time_us, e.event_type
        FROM (
            -- Records: create only, no fossils, no feed.post
            SELECT r.did, {TS_EXPR_RECORDS} AS time_us,
                   REPLACE(REPLACE(r.collection, 'app.bsky.', ''), '.', '_') AS event_type
            FROM bsky.records r
            WHERE r.operation = 'create'{FOSSIL_SQL}

            UNION ALL

            -- Top-level posts
            SELECT p.did, {TS_EXPR_POSTS} AS time_us, 'post_top' AS event_type
            FROM bsky.posts p
            WHERE p.reply_root_uri IS NULL

            UNION ALL

            -- Replies
            SELECT p.did, {TS_EXPR_POSTS} AS time_us, 'post_reply' AS event_type
            FROM bsky.posts p
            WHERE p.reply_root_uri IS NOT NULL
        ) e
        JOIN user_rates ur ON e.did = ur.did
    """, "insert events")

    validate(conn, evt)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
