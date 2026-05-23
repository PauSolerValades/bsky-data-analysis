#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "numpy",
#     "matplotlib",
#     "polars",
# ]
# ///
"""
Proper EDA — Bluesky firehose.

Reads ONLY from the two base tables: bsky.records and bsky.posts.
No pre-filtered tables. No pre-computed archetypes. No IQR. No composite score.

This EDA answers, in order:
  1. What event types exist? (bar chart: counts + %)
  2. When do events happen? (per day, per hour)
  3. How many users?
  4. Events per user — histogram (the fundamental distribution)
  5. Average events per day per user — histogram
  6. Average events per hour per user — histogram
  7. Ratio-based categorization (what users actually do)

Usage:
    uv run sessions/eda_proper.py
    uv run sessions/eda_proper.py --sample-collections 500000  # sample for speed
"""

import argparse
import os
import sys
import time as time_mod
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pymysql

# ---------------------------------------------------------------------------
# Env / DB
# ---------------------------------------------------------------------------


def _load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        print(f"WARNING: .env not found at {env_path}", file=sys.stderr)
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


_load_env()


def _env(k, d=""):
    return os.environ.get(k, d)


DB = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": "bsky",
    "charset": "utf8mb4",
}

OUT = Path(__file__).resolve().parent / "eda_proper_results"
OUT.mkdir(parents=True, exist_ok=True)

# ggplot2-ish style
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "figure.facecolor": "white",
    "axes.facecolor": "#f5f5f5",
    "axes.grid": True,
    "grid.alpha": 0.4,
    "grid.color": "#cccccc",
})


# ---------------------------------------------------------------------------
# §1 — Event types: what collections exist in bsky.records?
# ---------------------------------------------------------------------------

def section1_event_types(conn, sample=None):
    print("\n" + "=" * 60)
    print("  §1 — Event types (collections in bsky.records)")
    print("=" * 60)

    # All distinct collections + counts
    sql = """
        SELECT collection, COUNT(*) AS cnt
        FROM bsky.records
    """
    if sample:
        sql += f" WHERE time_us > 0 ORDER BY RAND() LIMIT {sample}"
    else:
        sql += " WHERE time_us > 0"
    sql += " GROUP BY collection ORDER BY cnt DESC"

    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    collections = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    total = sum(counts)

    print(f"  {len(collections)} distinct collections, {total:,} total records "
          f"({time_mod.time() - t0:.0f}s)")
    for c, n in zip(collections, counts):
        print(f"    {c:<45s} {n:>14,}  ({100*n/total:.1f}%)")

    # Bar chart
    fig, ax = plt.subplots(figsize=(14, 7))
    # Shorten names for display
    names = [c.replace("app.bsky.", "") for c in collections]
    pcts = [100 * n / total for n in counts]

    bars = ax.bar(range(len(names)), counts, color="#4A90D9", edgecolor="white", linewidth=0.5)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Count")
    ax.set_title(f"§1 — Record types in bsky.records  ({total:,} total)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x/1e6:.0f}M"))

    # Add % labels
    for bar, pct in zip(bars, pcts):
        if pct > 0.5:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + total * 0.005,
                    f"{pct:.1f}%", ha="center", fontsize=8)

    fig.tight_layout()
    fig.savefig(OUT / "01_event_types.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {OUT / '01_event_types.png'}")

    # Also: posts table. bsky.posts has its own row count (all posts, not a collection)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bsky.posts WHERE time_us > 0")
        post_rows = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM bsky.posts WHERE time_us > 0 AND reply_root_uri IS NULL
        """)
        top_level = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM bsky.posts WHERE time_us > 0 AND reply_root_uri IS NOT NULL
        """)
        replies = cur.fetchone()[0]

    print(f"\n  bsky.posts (all):        {post_rows:>14,}")
    print(f"    top-level (no reply):   {top_level:>14,}")
    print(f"    replies (has parent):   {replies:>14,}")

    return collections, counts, total


# ---------------------------------------------------------------------------
# §2 — Events per day & per hour
# ---------------------------------------------------------------------------

def section2_temporal(conn, sample_pct=10):
    print("\n" + "=" * 60)
    print("  §2 — Temporal distribution (per day, per hour)")
    print("=" * 60)

    # Use a random sample if the table is huge
    sample_clause = ""
    if sample_pct < 100:
        sample_clause = f" TABLESAMPLE({sample_pct} PERCENT) "

    # Per day — from records
    t0 = time_mod.time()
    sql = f"""
        SELECT DATE(FROM_UNIXTIME(time_us / 1000000)) AS day, COUNT(*) AS cnt
        FROM bsky.records {sample_clause}
        WHERE time_us > 0
        GROUP BY day ORDER BY day
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        days_records = cur.fetchall()
    print(f"  records per day: {len(days_records)} days, {time_mod.time() - t0:.0f}s")

    # Per day — from posts
    t0 = time_mod.time()
    sql = f"""
        SELECT DATE(FROM_UNIXTIME(time_us / 1000000)) AS day, COUNT(*) AS cnt
        FROM bsky.posts {sample_clause}
        WHERE time_us > 0
        GROUP BY day ORDER BY day
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        days_posts = cur.fetchall()
    print(f"  posts per day:   {len(days_posts)} days, {time_mod.time() - t0:.0f}s")

    # Per hour — from records
    t0 = time_mod.time()
    sql = f"""
        SELECT HOUR(FROM_UNIXTIME(time_us / 1000000)) AS hr, COUNT(*) AS cnt
        FROM bsky.records {sample_clause}
        WHERE time_us > 0
        GROUP BY hr ORDER BY hr
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        hours_records = cur.fetchall()
    print(f"  records per hour: {len(hours_records)} hours, {time_mod.time() - t0:.0f}s")

    # Per hour — from posts
    t0 = time_mod.time()
    sql = f"""
        SELECT HOUR(FROM_UNIXTIME(time_us / 1000000)) AS hr, COUNT(*) AS cnt
        FROM bsky.posts {sample_clause}
        WHERE time_us > 0
        GROUP BY hr ORDER BY hr
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        hours_posts = cur.fetchall()
    print(f"  posts per hour:   {len(hours_posts)} hours, {time_mod.time() - t0:.0f}s")

    # Plot — per day
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9))

    if days_records:
        d_rec, c_rec = zip(*days_records)
        ax1.bar(range(len(d_rec)), c_rec, color="#4A90D9", alpha=0.8, label="Records (all types)")
    if days_posts:
        d_post, c_post = zip(*days_posts)
        # Align posts days to records days
        day_map = {str(d): c for d, c in zip(d_rec, c_rec)}
        ax1.bar(range(len(d_post)), c_post, color="#E6842A", alpha=0.8, label="Posts only")
    ax1.set_title("§2a — Events per day")
    ax1.set_ylabel("Count")
    ax1.legend()
    ax1.set_xticks(range(0, len(d_rec), max(1, len(d_rec)//10)))
    ax1.set_xticklabels([str(d_rec[i]) for i in range(0, len(d_rec), max(1, len(d_rec)//10))],
                        rotation=45, ha="right")

    # Plot — per hour
    if hours_records:
        h_rec, c_rec = zip(*hours_records)
        ax2.bar(np.array(h_rec) - 0.2, c_rec, width=0.4, color="#4A90D9", alpha=0.8, label="Records")
    if hours_posts:
        h_post, c_post = zip(*hours_posts)
        ax2.bar(np.array(h_post) + 0.2, c_post, width=0.4, color="#E6842A", alpha=0.8, label="Posts")
    ax2.set_title("§2b — Events per hour of day (UTC)")
    ax2.set_xlabel("Hour (UTC)")
    ax2.set_ylabel("Count")
    ax2.set_xticks(range(24))
    ax2.legend()

    fig.tight_layout()
    fig.savefig(OUT / "02_temporal.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {OUT / '02_temporal.png'}")

    return days_records, hours_records


# ---------------------------------------------------------------------------
# §3 — How many users?
# ---------------------------------------------------------------------------

def section3_user_counts(conn):
    print("\n" + "=" * 60)
    print("  §3 — User counts")
    print("=" * 60)

    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT did) FROM bsky.records WHERE time_us > 0")
        users_records = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT did) FROM bsky.posts WHERE time_us > 0")
        users_posts = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(DISTINCT did) FROM (
                SELECT did FROM bsky.records WHERE time_us > 0
                UNION
                SELECT did FROM bsky.posts WHERE time_us > 0
            ) u
        """)
        users_total = cur.fetchone()[0]

    print(f"  Users in bsky.records:  {users_records:>12,}")
    print(f"  Users in bsky.posts:    {users_posts:>12,}")
    print(f"  Users in either:        {users_total:>12,}  ({time_mod.time() - t0:.0f}s)")

    return users_records, users_posts, users_total


# ---------------------------------------------------------------------------
# §4 — Events per user (THE key distribution)
# ---------------------------------------------------------------------------

def section4_events_per_user(conn, n_users=200000):
    print("\n" + "=" * 60)
    print("  §4 — Events per user (histogram)")
    print("=" * 60)

    # Fetch total events per user from records
    t0 = time_mod.time()
    # Use a random sample of users to keep query fast
    sql = f"""
        SELECT did, COUNT(*) AS total
        FROM bsky.records
        WHERE time_us > 0
        GROUP BY did
        ORDER BY RAND()
        LIMIT {n_users}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    print(f"  Sampled {len(rows):,} users from bsky.records ({time_mod.time() - t0:.0f}s)")

    counts = np.array([r[1] for r in rows], dtype=np.int64)

    # Same for posts only
    t0 = time_mod.time()
    sql = f"""
        SELECT did, COUNT(*) AS total
        FROM bsky.posts
        WHERE time_us > 0
        GROUP BY did
        ORDER BY RAND()
        LIMIT {n_users}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows_posts = cur.fetchall()
    counts_posts = np.array([r[1] for r in rows_posts], dtype=np.int64)
    print(f"  Sampled {len(rows_posts):,} users from bsky.posts ({time_mod.time() - t0:.0f}s)")

    # Also get the FULL distribution (not sampled) for key percentiles
    t0 = time_mod.time()
    sql = """
        SELECT total, COUNT(*) AS n_users
        FROM (
            SELECT did, COUNT(*) AS total
            FROM bsky.records
            WHERE time_us > 0
            GROUP BY did
        ) t
        GROUP BY total
        ORDER BY total
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        full_dist = cur.fetchall()
    print(f"  Full distribution: {len(full_dist):,} distinct event-count values "
          f"({time_mod.time() - t0:.0f}s)")

    # Compute percentiles from the full distribution
    full_counts = []
    for total, n_users in full_dist:
        full_counts.extend([total] * n_users)
    full_counts = np.array(full_counts, dtype=np.int64)

    ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    pvals = np.percentile(full_counts, ps)

    print(f"\n  Full distribution (all {len(full_counts):,} users in bsky.records):")
    print(f"  {'Percentile':<12s} {'Events':>10s}")
    print(f"  {'-'*22}")
    for p, v in zip(ps, pvals):
        print(f"  P{p:<11d} {v:>10.0f}")

    # Percentiles also by collection — for each major collection
    major_collections = [
        "app.bsky.feed.like",
        "app.bsky.feed.repost",
        "app.bsky.graph.follow",
        "app.bsky.graph.block",
        "app.bsky.actor.profile",
    ]
    collection_pcts = {}
    for coll in major_collections:
        sql = f"""
            SELECT total, COUNT(*) AS n_users
            FROM (
                SELECT did, COUNT(*) AS total
                FROM bsky.records
                WHERE time_us > 0 AND collection = '{coll}'
                GROUP BY did
            ) t
            GROUP BY total
            ORDER BY total
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            dist = cur.fetchall()
        if dist:
            vals = []
            for total, n_users in dist:
                vals.extend([total] * n_users)
            vals = np.array(vals, dtype=np.int64)
            collection_pcts[coll] = {
                "n_users": len(vals),
                "p50": np.median(vals),
                "p90": np.percentile(vals, 90),
                "p99": np.percentile(vals, 99),
                "max": vals.max(),
            }
            print(f"\n  {coll.replace('app.bsky.', '')}:")
            print(f"    users={len(vals):,}  median={np.median(vals):.0f}  "
                  f"P90={np.percentile(vals, 90):.0f}  P99={np.percentile(vals, 99):.0f}  "
                  f"max={vals.max():,}")

    # Plot — log-log histogram of events per user
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    # Panel A: records — log-log
    ax = axes[0]
    _loglog_hist(ax, counts, "§4a — Events per user (bsky.records)", "#4A90D9")

    # Panel B: posts — log-log
    ax = axes[1]
    _loglog_hist(ax, counts_posts, "§4b — Events per user (bsky.posts)", "#E6842A")

    # Panel C: linear CCDF
    ax = axes[2]
    sorted_c = np.sort(full_counts)
    ccdf = 1.0 - np.arange(len(sorted_c)) / len(sorted_c)
    ax.step(sorted_c, ccdf, where="post", color="#555555", linewidth=1.5)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Events per user")
    ax.set_ylabel("P(events ≥ x)")
    ax.set_title("§4c — CCDF (all users, bsky.records)")
    # Mark percentiles
    for p, v in zip([50, 90, 99], [pvals[ps.index(p)] for p in [50, 90, 99]]):
        ax.axvline(x=v, color="red", linestyle="--", alpha=0.5, linewidth=1)
        ax.text(v * 1.1, 0.02, f"P{p}={v:.0f}", fontsize=8, color="red")

    fig.tight_layout()
    fig.savefig(OUT / "04_events_per_user.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {OUT / '04_events_per_user.png'}")

    return full_counts, counts, counts_posts, collection_pcts


def _loglog_hist(ax, data, title, color):
    """Log-log histogram: log-spaced bins, bar chart."""
    data = data[data > 0]
    if len(data) == 0:
        ax.set_title(f"{title}\n(no data)")
        return

    logmin = np.log10(data.min())
    logmax = np.log10(data.max())
    bins = np.logspace(logmin, logmax, 40)

    hist, edges = np.histogram(data, bins=bins)
    widths = np.diff(edges)
    centers = (edges[:-1] + edges[1:]) / 2

    ax.bar(centers, hist, width=widths, color=color, alpha=0.85, edgecolor="white",
           linewidth=0.3, align="center")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Events per user")
    ax.set_ylabel("Number of users")
    ax.set_title(title)

    # Annotate
    ax.text(0.95, 0.95, f"n={len(data):,}\nmedian={np.median(data):.0f}",
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))


# ---------------------------------------------------------------------------
# §5 — Average events per DAY per user
# ---------------------------------------------------------------------------

def section5_events_per_day(conn, n_users=100000):
    print("\n" + "=" * 60)
    print("  §5 — Average events per day per user")
    print("=" * 60)

    t0 = time_mod.time()

    # For a sample of users, compute events per day
    # Strategy: get the user's active day count from records, then total events / active days
    sql = f"""
        SELECT did,
               COUNT(*) AS total_events,
               COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))) AS active_days
        FROM bsky.records
        WHERE time_us > 0
        GROUP BY did
        ORDER BY RAND()
        LIMIT {n_users}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    print(f"  {len(rows):,} users ({time_mod.time() - t0:.0f}s)")

    events_per_day = np.array([r[1] / max(r[2], 1) for r in rows], dtype=np.float64)
    active_days = np.array([r[2] for r in rows], dtype=np.int64)

    print(f"\n  Events per active day:")
    ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    for p, v in zip(ps, np.percentile(events_per_day, ps)):
        print(f"    P{p:>2d}: {v:.1f}")

    # Plot
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

    ax = axes[0]
    _loglog_hist(ax, events_per_day, "§5a — Events per active day per user (bsky.records)", "#50B86C")

    ax = axes[1]
    _loglog_hist(ax, active_days.astype(np.float64), "§5b — Active days per user", "#9B59B6")

    fig.tight_layout()
    fig.savefig(OUT / "05_events_per_day.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {OUT / '05_events_per_day.png'}")

    return events_per_day, active_days


# ---------------------------------------------------------------------------
# §6 — Average events per HOUR per user
# ---------------------------------------------------------------------------

def section6_events_per_hour(conn, n_users=50000):
    print("\n" + "=" * 60)
    print("  §6 — Average events per hour per user")
    print("=" * 60)

    t0 = time_mod.time()

    sql = f"""
        SELECT did,
               COUNT(*) AS total_events,
               COUNT(DISTINCT DATE_FORMAT(FROM_UNIXTIME(time_us / 1000000), '%%Y-%%m-%%d %%H')) AS active_hours
        FROM bsky.records
        WHERE time_us > 0
        GROUP BY did
        ORDER BY RAND()
        LIMIT {n_users}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    print(f"  {len(rows):,} users ({time_mod.time() - t0:.0f}s)")

    events_per_hour = np.array([r[1] / max(r[2], 1) for r in rows], dtype=np.float64)
    active_hours = np.array([r[2] for r in rows], dtype=np.int64)

    print(f"\n  Events per active hour:")
    for p, v in zip([1, 5, 10, 25, 50, 75, 90, 95, 99],
                     np.percentile(events_per_hour, [1, 5, 10, 25, 50, 75, 90, 95, 99])):
        print(f"    P{p:>2d}: {v:.1f}")

    # Plot
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

    ax = axes[0]
    _loglog_hist(ax, events_per_hour, "§6a — Events per active hour per user", "#E67E22")

    ax = axes[1]
    _loglog_hist(ax, active_hours.astype(np.float64), "§6b — Active hours per user", "#1ABC9C")

    fig.tight_layout()
    fig.savefig(OUT / "06_events_per_hour.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {OUT / '06_events_per_hour.png'}")

    return events_per_hour, active_hours


# ---------------------------------------------------------------------------
# §7 — Ratio-based categorization (WHAT USERS ACTUALLY DO)
# ---------------------------------------------------------------------------

def section7_ratios(conn, n_users=100000):
    print("\n" + "=" * 60)
    print("  §7 — Ratio-based categorization")
    print("=" * 60)

    t0 = time_mod.time()

    # For each sampled user, count events by collection + posts
    sql = f"""
        SELECT
            did,
            SUM(CASE WHEN collection = 'app.bsky.feed.like'     THEN 1 ELSE 0 END) AS n_likes,
            SUM(CASE WHEN collection = 'app.bsky.feed.repost'    THEN 1 ELSE 0 END) AS n_reposts,
            SUM(CASE WHEN collection = 'app.bsky.graph.follow'   THEN 1 ELSE 0 END) AS n_follows,
            SUM(CASE WHEN collection = 'app.bsky.graph.block'    THEN 1 ELSE 0 END) AS n_blocks,
            SUM(CASE WHEN collection = 'app.bsky.actor.profile'  THEN 1 ELSE 0 END) AS n_profiles,
            SUM(CASE WHEN collection NOT IN (
                'app.bsky.feed.like', 'app.bsky.feed.repost',
                'app.bsky.graph.follow', 'app.bsky.graph.block',
                'app.bsky.actor.profile'
            ) THEN 1 ELSE 0 END) AS n_other_records,
            COUNT(*) AS n_records_total
        FROM bsky.records
        WHERE time_us > 0
        GROUP BY did
        ORDER BY RAND()
        LIMIT {n_users}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows_records = cur.fetchall()

    # Also count posts per user
    sql = f"""
        SELECT did,
               SUM(CASE WHEN reply_root_uri IS NULL THEN 1 ELSE 0 END) AS n_posts,
               SUM(CASE WHEN reply_root_uri IS NOT NULL THEN 1 ELSE 0 END) AS n_replies,
               COUNT(*) AS n_posts_total
        FROM bsky.posts
        WHERE time_us > 0
        GROUP BY did
        ORDER BY RAND()
        LIMIT {n_users}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows_posts = cur.fetchall()
    print(f"  {len(rows_records):,} users from records, {len(rows_posts):,} from posts "
          f"({time_mod.time() - t0:.0f}s)")

    # Build per-user dict from records
    user_records = {}
    for r in rows_records:
        did = r[0]
        user_records[did] = {
            "likes": r[1], "reposts": r[2], "follows": r[3],
            "blocks": r[4], "profiles": r[5], "other": r[6],
            "total_records": r[7],
        }

    # Build per-user dict from posts
    user_posts = {}
    for r in rows_posts:
        did = r[0]
        user_posts[did] = {
            "posts": r[1], "replies": r[2], "total_posts": r[3],
        }

    # Merge
    all_dids = set(user_records.keys()) | set(user_posts.keys())
    merged = {}
    for did in all_dids:
        rec = user_records.get(did, {"likes": 0, "reposts": 0, "follows": 0, "blocks": 0,
                                      "profiles": 0, "other": 0, "total_records": 0})
        pos = user_posts.get(did, {"posts": 0, "replies": 0, "total_posts": 0})
        merged[did] = {
            **rec, **pos,
            "total_events": rec["total_records"] + pos["total_posts"],
        }

    # Compute three key ratios per user:
    #   create_ratio  = (posts + replies) / total_events
    #   amplify_ratio = reposts / total_events
    #   engage_ratio  = likes / total_events
    ratios = []
    for did, u in merged.items():
        te = max(u["total_events"], 1)
        ratios.append({
            "did": did,
            "total_events": u["total_events"],
            "create_ratio": (u["posts"] + u["replies"]) / te,
            "amplify_ratio": u["reposts"] / te,
            "engage_ratio": u["likes"] / te,
            "connect_ratio": u["follows"] / te,
            "n_posts": u["posts"],
            "n_replies": u["replies"],
            "n_likes": u["likes"],
            "n_reposts": u["reposts"],
            "n_follows": u["follows"],
            "n_blocks": u["blocks"],
        })

    # Print distributions of ratios
    create_r = np.array([r["create_ratio"] for r in ratios])
    amplify_r = np.array([r["amplify_ratio"] for r in ratios])
    engage_r = np.array([r["engage_ratio"] for r in ratios])
    connect_r = np.array([r["connect_ratio"] for r in ratios])

    print(f"\n  Ratio distributions ({len(ratios):,} users):")
    print(f"  {'Ratio':<20s} {'Zero%':>8s} {'Median':>8s} {'P75':>8s} {'P90':>8s} {'P99':>8s}")
    print(f"  {'-'*60}")
    for label, arr in [("create (posts+replies)", create_r),
                        ("amplify (reposts)", amplify_r),
                        ("engage (likes)", engage_r),
                        ("connect (follows)", connect_r)]:
        zero_pct = 100 * np.mean(arr == 0)
        print(f"  {label:<20s} {zero_pct:>7.1f}% {np.median(arr):>8.3f} "
              f"{np.percentile(arr, 75):>8.3f} {np.percentile(arr, 90):>8.3f} "
              f"{np.percentile(arr, 99):>8.3f}")

    # Scatter: create vs engage (creators vs engagers)
    fig, axes = plt.subplots(2, 2, figsize=(14, 12))

    # Panel A: create vs engage hexbin
    ax = axes[0, 0]
    mask = (create_r > 0) | (engage_r > 0)
    hb = ax.hexbin(create_r[mask], engage_r[mask], gridsize=40, cmap="YlOrRd",
                   mincnt=1, bins="log")
    ax.set_xlabel("Create ratio (posts + replies / total)")
    ax.set_ylabel("Engage ratio (likes / total)")
    ax.set_title("§7a — Create vs Engage")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.3, linewidth=0.8)
    fig.colorbar(hb, ax=ax, label="users")

    # Panel B: amplify vs create
    ax = axes[0, 1]
    mask = (amplify_r > 0) | (create_r > 0)
    hb = ax.hexbin(create_r[mask], amplify_r[mask], gridsize=40, cmap="YlOrRd",
                   mincnt=1, bins="log")
    ax.set_xlabel("Create ratio")
    ax.set_ylabel("Amplify ratio (reposts / total)")
    ax.set_title("§7b — Create vs Amplify")
    fig.colorbar(hb, ax=ax, label="users")

    # Panel C: Distribution of max ratio (what's the user's dominant activity?)
    ax = axes[1, 0]
    dominant = []
    for r in ratios:
        vals = {
            "create": r["create_ratio"],
            "engage": r["engage_ratio"],
            "amplify": r["amplify_ratio"],
            "connect": r["connect_ratio"],
        }
        dom = max(vals, key=vals.get)
        dominant.append(dom)
    from collections import Counter
    dom_counts = Counter(dominant)
    labels = list(dom_counts.keys())
    values = [dom_counts[l] for l in labels]
    colors_map = {"create": "#3498DB", "engage": "#E74C3C", "amplify": "#2ECC71",
                  "connect": "#9B59B6"}
    colors = [colors_map.get(l, "#95A5A6") for l in labels]
    ax.bar(labels, values, color=colors, edgecolor="white")
    for l, v in zip(labels, values):
        ax.text(l, v + len(ratios) * 0.005, f"{v:,}\n({100*v/len(ratios):.1f}%)",
                ha="center", fontsize=9)
    ax.set_title("§7c — Dominant activity per user")
    ax.set_ylabel("Number of users")

    # Panel D: events per user by dominant type (boxplot-style)
    ax = axes[1, 1]
    dom_data = defaultdict(list)
    for r in ratios:
        vals = {"create": r["create_ratio"], "engage": r["engage_ratio"],
                "amplify": r["amplify_ratio"], "connect": r["connect_ratio"]}
        dom = max(vals, key=vals.get)
        dom_data[dom].append(r["total_events"])
    dom_order = sorted(dom_data.keys(),
                       key=lambda k: np.median(dom_data[k]) if dom_data[k] else 0)
    bp = ax.boxplot([dom_data[k] for k in dom_order], labels=dom_order,
                    patch_artist=True, showfliers=False)
    for patch, k in zip(bp['boxes'], dom_order):
        patch.set_facecolor(colors_map.get(k, "#95A5A6"))
    ax.set_yscale("log")
    ax.set_ylabel("Total events per user")
    ax.set_title("§7d — Event count by dominant activity type")

    fig.tight_layout()
    fig.savefig(OUT / "07_ratios.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {OUT / '07_ratios.png'}")

    return ratios


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Proper EDA — Bluesky firehose")
    parser.add_argument("--sample-collections", type=int, default=0,
                        help="Sample N records for §1 event-type counting (0 = all)")
    parser.add_argument("--temporal-sample", type=int, default=10,
                        help="TABLESAMPLE percentage for §2 (0-100)")
    parser.add_argument("--users-sample", type=int, default=200000,
                        help="Users to sample for §4 histogram")
    parser.add_argument("--skip", type=str, default="",
                        help="Comma-separated sections to skip (e.g. '5,6')")
    args = parser.parse_args()

    skip = {int(x) for x in args.skip.split(",") if x.strip()}

    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}", file=sys.stderr)

    total_t0 = time_mod.time()

    if 1 not in skip:
        section1_event_types(conn, sample=args.sample_collections or None)

    if 2 not in skip:
        section2_temporal(conn, sample_pct=args.temporal_sample)

    if 3 not in skip:
        section3_user_counts(conn)

    if 4 not in skip:
        section4_events_per_user(conn, n_users=args.users_sample)

    if 5 not in skip:
        section5_events_per_day(conn)

    if 6 not in skip:
        section6_events_per_hour(conn)

    if 7 not in skip:
        section7_ratios(conn)

    conn.close()
    print(f"\n{'='*60}")
    print(f"  EDA complete in {time_mod.time() - total_t0:.0f}s")
    print(f"  Results: {OUT}/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
