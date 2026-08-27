"""Build pau_db.actions — the firehose "action trace" (des-ctic/dataset equivalent).

The des-ctic simulation feeds every dataset from its action trace:
(user_id, post_id, parent_id, type, time). The firehose side keeps
`events` minimal (did, time_us, event_type) for sessionization; this
table is the full trace that feeds cascade-creation and post analysis:

  did, time_us, event_type, rkey, subject_uri, via_uri

Rows:
  feed_repost  ← bsky.records (subject_uri = post reposted, via_uri = attribution)
  feed_like    ← bsky.records (subject_uri = post liked,   via_uri = attribution)
  post_top     ← bsky.posts   (subject_uri = own post uri — TOP-LEVEL posts only,
                                reply_root_uri IS NULL: one cascade per unique
                                post, replies are engagement not cascade roots)
  post_reply   ← bsky.posts   (subject_uri = reply_parent_uri — the reply as
                                engagement on the parent post)

Cascade roots are top-level posts only: replies would otherwise spawn
subcascades (their own repost trees) inside the parent's story.

time_us mirrors the events/sessions convention (create_events_tables.py):
user-side created_at, not server-receive time_us — so action↔session joins
share one clock. No tourist filter here (cascades need all actors).

Output: pau_db.actions (server) / actions (local DuckDB).
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

# ponytail: user-side clock, same as create_events_tables.py — keeps
# action↔session joins consistent. Fall back to server time_us if a
# created_at is ever NULL (doesn't happen in the current data).
TS_EXPR = "COALESCE(UNIX_TIMESTAMP({t}.created_at) * 1000000, {t}.time_us)"

DDL = f"""
    CREATE TABLE {{tbl}} (
        did          VARCHAR(128) NOT NULL,
        time_us      BIGINT       NOT NULL,
        event_type   VARCHAR(32)  NOT NULL,
        rkey         VARCHAR(64),
        subject_uri  VARCHAR(256),
        via_uri      VARCHAR(256)
    )
    ENGINE = OLAP
    DUPLICATE KEY(did, time_us, event_type)
    DISTRIBUTED BY HASH(did) BUCKETS 32
    PROPERTIES ("replication_num" = "1")
"""


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = local_connect(WHERE, repo_root=str(REPO))
    tbl = TBL + "actions"
    print(f"Connected ({WHERE.value})\n")

    print("── Creating actions (full firehose trace) ──")
    conn.execute(f"DROP TABLE IF EXISTS {tbl}", "drop old actions")
    conn.execute(DDL.format(tbl=tbl), "create actions")

    conn.execute(f"""
        INSERT INTO {tbl} (did, time_us, event_type, rkey, subject_uri, via_uri)
        SELECT e.did, e.time_us, e.event_type, e.rkey, e.subject_uri, e.via_uri
        FROM (
            -- Reposts + likes
            SELECT r.did,
                   {TS_EXPR.format(t='r')} AS time_us,
                   REPLACE(REPLACE(r.collection, 'app.bsky.', ''), '.', '_') AS event_type,
                   r.rkey,
                   r.subject_uri,
                   r.via_uri
            FROM bsky.records r
            WHERE r.collection IN ('app.bsky.feed.repost', 'app.bsky.feed.like')
              AND r.operation = 'create'
              AND r.subject_uri IS NOT NULL

            UNION ALL

            -- Top-level posts only: one cascade per unique post, no subcascades
            SELECT p.did,
                   {TS_EXPR.format(t='p')} AS time_us,
                   'post_top' AS event_type,
                   p.rkey,
                   CONCAT('at://', p.did, '/app.bsky.feed.post/', p.rkey) AS subject_uri,
                   NULL AS via_uri
            FROM bsky.posts p
            WHERE p.reply_root_uri IS NULL

            UNION ALL

            -- Replies → subject is the parent they replied to
            SELECT p.did,
                   {TS_EXPR.format(t='p')} AS time_us,
                   'post_reply' AS event_type,
                   p.rkey,
                   p.reply_parent_uri AS subject_uri,
                   NULL AS via_uri
            FROM bsky.posts p
            WHERE p.reply_root_uri IS NOT NULL
        ) e
    """, "insert actions")

    # ── Validation ─────────────────────────────────────────────────────
    print("\n── Validation ──")
    rows = conn.query(f"""
        SELECT 'total rows' AS metric, COUNT(*) AS value FROM {tbl}
        UNION ALL
        SELECT 'distinct users', COUNT(DISTINCT did) FROM {tbl}
        UNION ALL
        SELECT 'distinct subjects', COUNT(DISTINCT subject_uri) FROM {tbl}
    """)
    for metric, value in rows:
        print(f"  {metric}: {value:,}")

    rows = conn.query(f"""
        SELECT event_type, COUNT(*) AS cnt FROM {tbl}
        GROUP BY event_type ORDER BY cnt DESC
    """)
    total = sum(r[1] for r in rows)
    print(f"\n  {'event_type':<20s} {'count':>14s}  {'%':>6s}")
    print(f"  {'-'*44}")
    for et, cnt in rows:
        print(f"  {et:<20s} {cnt:>14,}  {100*cnt/total:>5.1f}%")
    print(f"  {'TOTAL':<20s} {total:>14,}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
