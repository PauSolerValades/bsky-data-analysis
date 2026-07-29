"""Describe the raw source tables and events table.

Basic statistics — row counts, distinct users, event type breakdowns.
No plots, just printed tables. Run as a data-integrity smoke test.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env")
from running_locally.local_db import Where, get_connection as local_connect

WHERE = Where.from_env()


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = local_connect(WHERE, repo_root=str(REPO))
    print(f"Connected ({WHERE.value})\n")

    # ── §1: distinct users ──────────────────────────────────────────────

    n_records = conn.query("SELECT COUNT(DISTINCT did) FROM bsky.records")[0][0]
    n_posts = conn.query("SELECT COUNT(DISTINCT did) FROM bsky.posts")[0][0]
    n_union = conn.query("""
        SELECT COUNT(*) FROM (
            SELECT did FROM bsky.records UNION SELECT did FROM bsky.posts
        ) u
    """)[0][0]

    print("── Distinct users ──")
    print(f"  bsky.records:  {n_records:>12,}")
    print(f"  bsky.posts:    {n_posts:>12,}")
    print(f"  union:         {n_union:>12,}")
    print()

    # ── §2: event types — bsky.records ──────────────────────────────────

    rows = conn.query("""
        SELECT collection, operation, COUNT(*) AS cnt
        FROM bsky.records
        GROUP BY collection, operation
        ORDER BY cnt DESC
    """)

    rec_labels = []
    rec_counts = []
    for coll, op, cnt in rows:
        short = coll.replace("app.bsky.", "")
        rec_labels.append(f"{short}  [{op}]")
        rec_counts.append(cnt)

    print("── bsky.records — event types ──")
    total_r = sum(rec_counts)
    for label, cnt in zip(rec_labels, rec_counts):
        print(f"  {label:<45s} {cnt:>12,}  ({100 * cnt / total_r:5.1f}%)")
    print(f"  {'TOTAL':<45s} {total_r:>12,}")
    print()

    # ── §3: event types — bsky.posts ────────────────────────────────────

    total_p = conn.query("SELECT COUNT(*) FROM bsky.posts")[0][0]
    n_top = conn.query(
        "SELECT COUNT(*) FROM bsky.posts WHERE reply_root_uri IS NULL"
    )[0][0]
    n_reply = conn.query(
        "SELECT COUNT(*) FROM bsky.posts WHERE reply_root_uri IS NOT NULL"
    )[0][0]

    post_labels = ["top-level post", "reply"]
    post_counts = [n_top, n_reply]

    print("── bsky.posts — event types ──")
    for label, cnt in zip(post_labels, post_counts):
        print(f"  {label:<20s} {cnt:>12,}  ({100 * cnt / total_p:5.1f}%)")
    print(f"  {'TOTAL':<20s} {total_p:>12,}")
    print()

    # ── §4: merged event types (from events table) ─────────────────────

    merged = conn.query("""
        SELECT event_type, COUNT(*) AS cnt
        FROM events
        GROUP BY event_type
        ORDER BY cnt DESC
    """)

    merged_labels = [r[0] for r in merged]
    merged_counts = [r[1] for r in merged]
    total_m = sum(merged_counts)

    print("── Merged events (events table) ──")
    for label, cnt in zip(merged_labels, merged_counts):
        print(f"  {label:<30s} {cnt:>12,}  ({100 * cnt / total_m:5.1f}%)")
    print(f"  {'TOTAL':<30s} {total_m:>12,}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
