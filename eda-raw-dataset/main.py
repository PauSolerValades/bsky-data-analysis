"""EDA — Bluesky firehose raw dataset.

Basic statistics on the two source tables: bsky.records and bsky.posts.
"""

import os
from pathlib import Path

import matplotlib.pyplot as plt
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
    "database": "bsky",
    "charset": "utf8mb4",
}

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────

def query(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def barh(labels, counts, title, out_name):
    """Horizontal bar chart: labels vs counts, with % of total."""
    total = sum(counts)
    pcts = [100 * c / total for c in counts]

    fig, ax = plt.subplots(figsize=(10, max(4, 0.35 * len(labels))))
    colors = plt.cm.Blues([0.4 + 0.55 * (i / max(len(labels) - 1, 1)) for i in range(len(labels))])
    bars = ax.barh(range(len(labels)), counts, color=colors, edgecolor="white")

    for i, (c, p) in enumerate(zip(counts, pcts)):
        ax.text(c, i, f"  {c:,}  ({p:.1f}%)", va="center", fontsize=9)

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Count")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    path = OUT / out_name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    # ── §1: distinct users ──────────────────────────────────────────────

    n_records = query(conn, "SELECT COUNT(DISTINCT did) FROM bsky.records")[0][0]
    n_posts   = query(conn, "SELECT COUNT(DISTINCT did) FROM bsky.posts")[0][0]
    n_union   = query(conn, """
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

    rows = query(conn, """
        SELECT collection, operation, COUNT(*) AS cnt
        FROM bsky.records
        GROUP BY collection, operation
        ORDER BY cnt DESC
    """)

    # Build short labels
    rec_labels = []
    rec_counts = []
    for coll, op, cnt in rows:
        short = coll.replace("app.bsky.", "")
        rec_labels.append(f"{short}  [{op}]")
        rec_counts.append(cnt)

    print("── bsky.records — event types ──")
    total_r = sum(rec_counts)
    for label, cnt in zip(rec_labels, rec_counts):
        print(f"  {label:<45s} {cnt:>12,}  ({100*cnt/total_r:5.1f}%)")
    print(f"  {'TOTAL':<45s} {total_r:>12,}")
    print()

    # ── §3: event types — bsky.posts ────────────────────────────────────

    total_p = query(conn, "SELECT COUNT(*) FROM bsky.posts")[0][0]
    n_top    = query(conn, "SELECT COUNT(*) FROM bsky.posts WHERE reply_root_uri IS NULL")[0][0]
    n_reply  = query(conn, "SELECT COUNT(*) FROM bsky.posts WHERE reply_root_uri IS NOT NULL")[0][0]

    post_labels = ["top-level post", "reply"]
    post_counts = [n_top, n_reply]

    print("── bsky.posts — event types ──")
    for label, cnt in zip(post_labels, post_counts):
        print(f"  {label:<20s} {cnt:>12,}  ({100*cnt/total_p:5.1f}%)")
    print(f"  {'TOTAL':<20s} {total_p:>12,}")
    print()

    # ── §4: merged event types (records + posts) ───────────────────────

    merged = query(conn, """
        SELECT event_type, COUNT(*) AS cnt
        FROM (
            SELECT REPLACE(REPLACE(collection, 'app.bsky.', ''), '.', '_') AS event_type
            FROM bsky.records
            WHERE collection LIKE 'app.bsky.%'
              AND collection NOT IN (
                  'app.bsky.feed.post',
                  'app.bsky.graph.repost',
                  'app.bsky.graph.verification',
                  'app.bsky.lexicon.collection',
                  'app.bsky.graph.cancellation',
                  'app.bsky.draft.createDraft'
              )
            UNION ALL
            SELECT 'post_top'   FROM bsky.posts WHERE reply_root_uri IS NULL
            UNION ALL
            SELECT 'post_reply' FROM bsky.posts WHERE reply_root_uri IS NOT NULL
        ) e
        GROUP BY event_type
        ORDER BY cnt DESC
    """)

    merged_labels = [r[0] for r in merged]
    merged_counts = [r[1] for r in merged]
    total_m = sum(merged_counts)

    print("── Merged events (records + posts, excluding fossils) ──")
    for label, cnt in zip(merged_labels, merged_counts):
        print(f"  {label:<30s} {cnt:>12,}  ({100*cnt/total_m:5.1f}%)")
    print(f"  {'TOTAL':<30s} {total_m:>12,}")
    print()

    # ── Plots ───────────────────────────────────────────────────────────

    barh(rec_labels, rec_counts,
         f"bsky.records — event types ({total_r:,} total)",
         "01_records_event_types.png")

    barh(post_labels, post_counts,
         f"bsky.posts — event types ({total_p:,} total)",
         "02_posts_event_types.png")

    barh(merged_labels, merged_counts,
         f"Merged — all events ({total_m:,} total)",
         "03_merged_event_types.png")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
