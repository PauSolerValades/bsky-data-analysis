#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "numpy",
#     "matplotlib",
#     "scipy",
# ]
# ///
"""
EDA — Bluesky firehose.

Reads ONLY from the two base tables: bsky.records and bsky.posts.
No pre-filtered tables. No pre-computed archetypes.

Sections:
  1. Event types + operation split + major events definition
  2. Temporal: per day, per hour (with explicit window boundaries)
  3. User counts
  4. Events per user + power-law fit + per-collection breakdown
  5. Average events per day per user
  6. Average events per hour per user
  7. Ratio-based categorization

Usage:
    uv run EDA/run.py
"""

import argparse
import os
import sys
import time as time_mod
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pymysql


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

plt.style.use("ggplot")

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "EDA" / "results"
OUT.mkdir(parents=True, exist_ok=True)


def load_env():
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def env(k, d=""):
    return os.environ.get(k, d)


load_env()

DB = {
    "host": env("DATABASE_HOST", "10.18.74.14"),
    "port": int(env("DATABASE_PORT", "9030")),
    "user": env("DATABASE_USER", "pau"),
    "password": env("PAU_PASSWORD", ""),
    "database": "bsky",
    "charset": "utf8mb4",
}



def save(fig, name):
    path = OUT / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {path}")


def pct(arr, q):
    return np.percentile(arr, q)


def pcts(arr, *qs):
    return tuple(np.percentile(arr, qs))


def loglog_hist(ax, data, title, color, xlabel="Value", n_bins=40):
    """Log-log histogram with log-spaced bins."""
    data = np.asarray(data, dtype=np.float64)
    data = data[data > 0]
    if len(data) == 0:
        ax.set_title(f"{title}\n(no data)")
        return

    lo = np.log10(data.min())
    hi = np.log10(data.max())
    bins = np.logspace(lo, hi, n_bins)
    hist, edges = np.histogram(data, bins=bins)
    widths = np.diff(edges)
    centers = (edges[:-1] + edges[1:]) / 2

    ax.bar(centers, hist, width=widths, color=color, alpha=0.85,
           edgecolor="white", linewidth=0.3, align="center")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Number of users")
    ax.set_title(title)

    ax.text(0.95, 0.95,
            f"n = {len(data):,}\nmedian = {np.median(data):,.1f}",
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))


def powerlaw_fit(data):
    """Fit power-law tail via MLE with KS-minimisation for xmin (Clauset et al.).

    Returns dict with keys: xmin, alpha, ks_stat, n_tail, n_total.
    Requires scipy.
    """
    from scipy import stats

    data = np.asarray(data, dtype=np.float64)
    data = data[data > 0]
    if len(data) < 20:
        return {"xmin": 1, "alpha": 2.0, "ks_stat": 1.0,
                "n_tail": len(data), "n_total": len(data)}

    data_sorted = np.sort(data)
    # Candidate xmins: unique values in lower half
    candidates = np.unique(
        data_sorted[data_sorted <= np.median(data_sorted)]
    )
    n_xmins = 50
    if len(candidates) > n_xmins:
        idx = np.linspace(0, len(candidates) - 1, n_xmins, dtype=int)
        candidates = candidates[idx]

    best_ks = np.inf
    best_xmin = 1
    best_alpha = 2.0

    for xmin in candidates:
        tail = data[data >= xmin]
        if len(tail) < 10:
            continue
        alpha = 1 + len(tail) / np.sum(np.log(tail / xmin))
        tail_sorted = np.sort(tail)
        emp = np.arange(1, len(tail_sorted) + 1) / len(tail_sorted)
        theo = 1 - (tail_sorted / xmin) ** (1 - alpha)
        ks = np.max(np.abs(emp - theo))
        if ks < best_ks:
            best_ks = ks
            best_xmin = xmin
            best_alpha = alpha

    n_tail = int(np.sum(data >= best_xmin))
    return {
        "xmin": best_xmin,
        "alpha": best_alpha,
        "ks_stat": best_ks,
        "n_tail": n_tail,
        "n_total": len(data),
    }


# ═══════════════════════════════════════════════════════════════════════════
# §1 — Event types
# ═══════════════════════════════════════════════════════════════════════════

def section1(conn):
    """What collections exist, split by operation. Define major event types."""
    print("\n" + "=" * 60)
    print("  §1 — Event types: collections × operation")
    print("=" * 60)

    # 1a — All collections, split by operation
    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT collection, operation, COUNT(*) AS cnt
            FROM bsky.records
            WHERE time_us > 0
            GROUP BY collection, operation
            ORDER BY cnt DESC
        """)
        rows = cur.fetchall()

    # Aggregate by collection
    coll_totals = defaultdict(int)
    coll_creates = defaultdict(int)
    coll_deletes = defaultdict(int)
    for coll, op, cnt in rows:
        coll_totals[coll] += cnt
        if op == "create":
            coll_creates[coll] += cnt
        elif op == "delete":
            coll_deletes[coll] += cnt

    total_records = sum(coll_totals.values())
    total_creates = sum(coll_creates.values())
    total_deletes = sum(coll_deletes.values())

    print(f"  {len(coll_totals)} collections, {total_records:,} records "
          f"({total_creates:,} creates, {total_deletes:,} deletes, "
          f"{total_records - total_creates - total_deletes:,} updates) "
          f"({time_mod.time() - t0:.0f}s)")

    sorted_colls = sorted(coll_totals, key=coll_totals.get, reverse=True)
    for coll in sorted_colls:
        total = coll_totals[coll]
        cr = coll_creates.get(coll, 0)
        dl = coll_deletes.get(coll, 0)
        up = total - cr - dl
        pct = 100 * total / total_records
        short = coll.replace("app.bsky.", "")
        parts = []
        if cr:
            parts.append(f"create={cr:,}")
        if dl:
            parts.append(f"delete={dl:,} ({100*dl/max(total,1):.0f}%)")
        if up:
            parts.append(f"update={up:,}")
        print(f"    {short:<40s} {total:>12,}  ({pct:5.1f}%)  [{', '.join(parts)}]")

    # Posts table
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bsky.posts WHERE time_us > 0")
        n_posts = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM bsky.posts WHERE time_us > 0 AND reply_root_uri IS NULL")
        n_top = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM bsky.posts WHERE time_us > 0 AND reply_root_uri IS NOT NULL")
        n_reply = cur.fetchone()[0]
    print(f"\n  bsky.posts: total={n_posts:,}  top_level={n_top:,}  replies={n_reply:,}")

    # 1b — User reach per event type (sorted, no arbitrary cutoff)
    print("\n" + "-" * 60)
    print("  §1b — User reach by event type")
    print("-" * 60)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT did FROM bsky.records WHERE time_us > 0
                UNION
                SELECT did FROM bsky.posts WHERE time_us > 0
            ) u
        """)
        n_users_total = cur.fetchone()[0]

    major_results = []
    for coll in sorted_colls:
        if coll_creates.get(coll, 0) == 0:
            continue
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT COUNT(DISTINCT did)
                FROM bsky.records
                WHERE time_us > 0
                  AND collection = '{coll}'
                  AND operation = 'create'
            """)
            n_users = cur.fetchone()[0]
        reach_pct = 100 * n_users / n_users_total
        short = coll.replace("app.bsky.", "")
        major_results.append((short, n_users, reach_pct, coll_totals[coll]))

    # Posts reach
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT did) FROM bsky.posts WHERE time_us > 0")
        n_post_users = cur.fetchone()[0]
    major_results.append(("feed.post (all)", n_post_users, 100*n_post_users/n_users_total, n_posts, True))
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT did) FROM bsky.posts WHERE time_us > 0 AND reply_root_uri IS NULL")
        n_top_users = cur.fetchone()[0]
    major_results.append(("feed.post (top-level)", n_top_users, 100*n_top_users/n_users_total, n_top, True))
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT did) FROM bsky.posts WHERE time_us > 0 AND reply_root_uri IS NOT NULL")
        n_reply_users = cur.fetchone()[0]
    major_results.append(("feed.post (reply)", n_reply_users, 100*n_reply_users/n_users_total, n_reply, True))

    major_results.sort(key=lambda x: x[1], reverse=True)

    print(f"  Total users: {n_users_total:,}")
    print(f"\n  {'Event type':<30s} {'Users':>10s} {'Reach':>8s} {'Events':>12s}")
    print(f"  {'-'*65}")
    for short, n_users, reach, n_events in major_results:
        print(f"  {short:<30s} {n_users:>10,} {reach:>7.1f}% {n_events:>12,}")

    print(f"\n  ⚠️  Database dump limitations:")
    print(f"    - Non-Bluesky AT Protocol collections were purposely discarded")
    print(f"      (site.standard, social.grain, etc.) — by design.")
    print(f"    - bsky.posts assumed text-only, no embed → quote posts are"
          f"      invisible. ~5.6% of posts are quotes. This was an error.")
    print(f"    - Repost 'via' chains are in record_json but not surfaced here")

    # 1c — Plot: stacked create/delete + user reach
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    top8 = sorted_colls[:8]
    top8_cr = [coll_creates.get(c, 0) for c in top8]
    top8_dl = [coll_deletes.get(c, 0) for c in top8]
    short8 = [c.replace("app.bsky.", "") for c in top8]
    x = np.arange(len(top8))
    ax1.bar(x, top8_cr, color="#4A90D9", edgecolor="white", label="create")
    ax1.bar(x, top8_dl, bottom=top8_cr, color="#E74C3C", edgecolor="white", label="delete")
    ax1.set_xticks(x)
    ax1.set_xticklabels(short8, rotation=45, ha="right", fontsize=9)
    ax1.set_ylabel("Record count")
    ax1.set_title(f"§1a — Records by collection ({total_records:,} total)")
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x/1e6:.0f}M"))
    ax1.legend()

    labels_m = [r[0] for r in major_results][:10]
    reaches_m = [r[2] for r in major_results][:10]
    colors_rm = ["#4A90D9" if "feed.post" in l else "#E6842A" for l in labels_m]
    ax2.barh(range(len(labels_m)), reaches_m, color=colors_rm, edgecolor="white")
    ax2.set_yticks(range(len(labels_m)))
    ax2.set_yticklabels(labels_m, fontsize=9)
    ax2.set_xlabel("% of users with ≥1 event")
    ax2.set_title(f"§1b — User reach ({n_users_total:,} total users)")
    ax2.invert_yaxis()

    save(fig, "01_event_types.png")

    return coll_totals, coll_creates, coll_deletes, major_results


# ═══════════════════════════════════════════════════════════════════════════
# §2 — Temporal: per day, per hour
# ═══════════════════════════════════════════════════════════════════════════

def section2(conn):
    """When do events happen?"""
    print("\n" + "=" * 60)
    print("  §2 — Temporal distribution")
    print("=" * 60)

    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DATE(FROM_UNIXTIME(time_us / 1000000)) AS day, COUNT(*) AS cnt
            FROM bsky.records
            WHERE time_us > 0
            GROUP BY day ORDER BY day
        """)
        days_r = cur.fetchall()
        cur.execute("""
            SELECT DATE(FROM_UNIXTIME(time_us / 1000000)) AS day, COUNT(*) AS cnt
            FROM bsky.posts
            WHERE time_us > 0
            GROUP BY day ORDER BY day
        """)
        days_p = cur.fetchall()
        cur.execute("""
            SELECT HOUR(FROM_UNIXTIME(time_us / 1000000)) AS hr, COUNT(*) AS cnt
            FROM bsky.records
            WHERE time_us > 0
            GROUP BY hr ORDER BY hr
        """)
        hrs_r = cur.fetchall()
        cur.execute("""
            SELECT HOUR(FROM_UNIXTIME(time_us / 1000000)) AS hr, COUNT(*) AS cnt
            FROM bsky.posts
            WHERE time_us > 0
            GROUP BY hr ORDER BY hr
        """)
        hrs_p = cur.fetchall()
    print(f"  {len(days_r)} days of records, {len(hrs_r)} hours "
          f"({time_mod.time() - t0:.0f}s)")

    # Print day range with explicit window warning
    if days_r:
        first_day = days_r[0][0]
        last_day = days_r[-1][0]
        n_days = len(days_r)
        print(f"\n  ╔══════════════════════════════════════════╗")
        print(f"  ║  DATA WINDOW                            ║")
        print(f"  ║  {first_day}  →  {last_day}          ║")
        print(f"  ║  {n_days} days                                       ║")
        print(f"  ║  WARNING: all per-day / per-hour stats   ║")
        print(f"  ║  are bound to this {n_days}-day snapshot.     ║")
        print(f"  ╚══════════════════════════════════════════╝")

    # Plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9))

    if days_r:
        d, c = zip(*days_r)
        ax1.bar(range(len(d)), c, color="#4A90D9", alpha=0.8, label="Records (all)")
    if days_p:
        d2, c2 = zip(*days_p)
        ax1.bar(range(len(d2)), c2, color="#E6842A", alpha=0.8, label="Posts")
    ax1.set_title(f"§2a — Events per day  [{first_day} → {last_day}, {n_days} days]")
    ax1.set_ylabel("Count")
    ax1.legend()
    step = max(1, len(d) // 12)
    ax1.set_xticks(range(0, len(d), step))
    ax1.set_xticklabels([str(d[i]) for i in range(0, len(d), step)], rotation=45, ha="right")

    if hrs_r:
        h1, c1 = zip(*hrs_r)
        ax2.bar(np.array(h1) - 0.2, c1, width=0.4, color="#4A90D9", alpha=0.8, label="Records")
    if hrs_p:
        h2, c2 = zip(*hrs_p)
        ax2.bar(np.array(h2) + 0.2, c2, width=0.4, color="#E6842A", alpha=0.8, label="Posts")
    ax2.set_title("§2b — Events per hour of day (UTC)")
    ax2.set_xlabel("Hour (UTC)")
    ax2.set_ylabel("Count")
    ax2.set_xticks(range(24))
    ax2.legend()

    save(fig, "02_temporal.png")
    return days_r, hrs_r


# ═══════════════════════════════════════════════════════════════════════════
# §3 — User counts
# ═══════════════════════════════════════════════════════════════════════════

def section3(conn):
    """How many distinct users?"""
    print("\n" + "=" * 60)
    print("  §3 — User counts")
    print("=" * 60)

    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT did) FROM bsky.records WHERE time_us > 0")
        u_records = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT did) FROM bsky.posts WHERE time_us > 0")
        u_posts = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT did FROM bsky.records WHERE time_us > 0
                UNION
                SELECT did FROM bsky.posts WHERE time_us > 0
            ) u
        """)
        u_union = cur.fetchone()[0]

    print(f"  Users in bsky.records:  {u_records:>12,}")
    print(f"  Users in bsky.posts:    {u_posts:>12,}")
    print(f"  Users in either:        {u_union:>12,}")
    print(f"  In records only:        {u_union - u_posts:>12,}")
    print(f"  In posts only:          {u_union - u_records:>12,}  ({time_mod.time() - t0:.0f}s)")

    return u_records, u_posts, u_union


# ═══════════════════════════════════════════════════════════════════════════
# §4 — Events per user
# ═══════════════════════════════════════════════════════════════════════════

def section4(conn):
    """The fundamental distribution: events per user."""
    print("\n" + "=" * 60)
    print("  §4 — Events per user")
    print("=" * 60)

    # Full distribution from BOTH tables (no sampling — need this for percentiles)
    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT total, COUNT(*) AS n_users
            FROM (
                SELECT did, COUNT(*) AS total
                FROM (
                    SELECT did FROM bsky.records WHERE time_us > 0
                    UNION ALL
                    SELECT did FROM bsky.posts WHERE time_us > 0
                ) e
                GROUP BY did
            ) t
            GROUP BY total
            ORDER BY total
        """)
        dist_r = cur.fetchall()
    print(f"\n  Full distribution from BOTH tables ({len(dist_r):,} distinct values "
          f"({time_mod.time() - t0:.0f}s)")

    # Expand to array for stats
    vals_r = []
    for total, n_users in dist_r:
        vals_r.extend([total] * n_users)
    vals_r = np.array(vals_r, dtype=np.int64)
    print(f"  Total users (both tables): {len(vals_r):,}")

    # Percentiles
    ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    print(f"\n  {'Pct':>6s}  {'Events':>10s}")
    print(f"  {'-'*18}")
    for pv in ps:
        print(f"  P{pv:<4d}  {pct(vals_r, pv):>10.0f}")

    # Power-law fit on the full distribution
    print(f"\n  Power-law fit (Clauset-Shalizi-Newman):")
    pl = powerlaw_fit(vals_r.astype(np.float64))
    tail_pct = 100 * pl["n_tail"] / pl["n_total"]
    print(f"    α = {pl['alpha']:.2f}")
    print(f"    xmin = {pl['xmin']:.0f}  (tail: {pl['n_tail']:,} users, {tail_pct:.1f}% of total)")
    print(f"    KS = {pl['ks_stat']:.4f}")
    print(f"    Interpretation: the event-count distribution follows a power-law")
    print(f"    for users with ≥{pl['xmin']:.0f} events. Below this, the distribution")
    print(f"    is dominated by tourists and one-time users.")

    # Engaged-events power-law fit (posts + replies + reposts + follows + blocks)
    # Same method, but on engaged-only events (no likes)
    print(f"\n  Power-law fit — engaged events only (no likes):")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT n_events, COUNT(*) AS n_users
            FROM (
                SELECT did, COUNT(*) AS n_events
                FROM (
                    SELECT did, time_us FROM bsky.posts WHERE time_us > 0
                    UNION ALL
                    SELECT did, time_us FROM bsky.records
                    WHERE time_us > 0
                      AND collection IN ('app.bsky.feed.repost','app.bsky.graph.follow','app.bsky.graph.block')
                      AND operation = 'create'
                ) e
                GROUP BY did
            ) t
            GROUP BY n_events ORDER BY n_events
        """)
        rows_eng = cur.fetchall()
    vals_eng = []
    for n, u in rows_eng:
        vals_eng.extend([n] * u)
    vals_eng = np.array(vals_eng, dtype=np.float64)
    pl_eng = powerlaw_fit(vals_eng)
    tail_pct_eng = 100 * pl_eng["n_tail"] / pl_eng["n_total"]
    print(f"    Users: {len(vals_eng):,}")
    print(f"    α = {pl_eng['alpha']:.2f}")
    print(f"    xmin = {pl_eng['xmin']:.0f}  (tail: {pl_eng['n_tail']:,} users, {tail_pct_eng:.1f}% of total)")
    print(f"    KS = {pl_eng['ks_stat']:.4f}")
    print(f"    Use ≥{pl_eng['xmin']:.0f} engaged events as the filter for the")
    print(f"    engaged_events table.")

    # By major collection (CREATES ONLY — no deletes/updates)
    collections = [
        "app.bsky.feed.like",
        "app.bsky.feed.repost",
        "app.bsky.graph.follow",
        "app.bsky.graph.block",
        "app.bsky.actor.profile",
    ]
    coll_stats = {}
    for coll in collections:
        t0 = time_mod.time()
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT total, COUNT(*) AS n_users
                FROM (
                    SELECT did, COUNT(*) AS total
                    FROM bsky.records
                    WHERE time_us > 0
                      AND collection = '{coll}'
                      AND operation = 'create'
                    GROUP BY did
                ) t
                GROUP BY total
                ORDER BY total
            """)
            dist = cur.fetchall()
        if not dist:
            continue
        vals = []
        for total, nu in dist:
            vals.extend([total] * nu)
        vals = np.array(vals, dtype=np.int64)
        short = coll.replace("app.bsky.", "")
        coll_stats[short] = {
            "n_users": len(vals), "p50": np.median(vals),
            "p90": pct(vals, 90), "p99": pct(vals, 99), "max": vals.max(),
        }
        print(f"\n  {short} ({len(vals):,} users):")
        print(f"    median={np.median(vals):.0f}  P90={pct(vals, 90):.0f}  "
              f"P99={pct(vals, 99):.0f}  max={vals.max():,}  ({time_mod.time() - t0:.0f}s)")

    # Posts table distribution
    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT total, COUNT(*) AS n_users
            FROM (
                SELECT did, COUNT(*) AS total
                FROM bsky.posts
                WHERE time_us > 0
                GROUP BY did
            ) t
            GROUP BY total
            ORDER BY total
        """)
        dist_p = cur.fetchall()
    vals_p = []
    for total, nu in dist_p:
        vals_p.extend([total] * nu)
    vals_p = np.array(vals_p, dtype=np.int64)
    print(f"\n  bsky.posts ({len(vals_p):,} users, {time_mod.time() - t0:.0f}s):")
    for pv in ps:
        print(f"  P{pv:<4d}  {pct(vals_p, pv):>10.0f}")

    # Plots — three panels
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    loglog_hist(axes[0], vals_r.astype(np.float64),
                "§4a — Events per user (bsky.records + bsky.posts)",
                "#4A90D9", xlabel="Events per user")
    loglog_hist(axes[1], vals_p.astype(np.float64),
                "§4b — Posts per user (bsky.posts)",
                "#E6842A", xlabel="Posts per user")

    # CCDF panel
    ax = axes[2]
    sorted_c = np.sort(vals_r)
    ccdf = 1 - np.arange(len(sorted_c)) / len(sorted_c)
    ax.step(sorted_c, ccdf, where="post", color="#333333", linewidth=1.5)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Events per user")
    ax.set_ylabel("P(Events ≥ x)")
    ax.set_title("§4c — CCDF (both tables, all users)")
    for pv in [50, 90, 99]:
        v = pcts(vals_r, pv)[0]
        ax.axvline(x=v, color="red", linestyle="--", alpha=0.4, linewidth=1)
        ax.text(v * 1.15, 0.015, f"P{pv}={v:.0f}", fontsize=8, color="red")
    # Power-law fit line
    if pl["n_tail"] > 0:
        xfit = np.logspace(np.log10(pl["xmin"]), np.log10(vals_r.max()), 100)
        yfit = (pl["xmin"] / xfit) ** (pl["alpha"] - 1)
        ax.plot(xfit, yfit, "b--", linewidth=2, alpha=0.6,
                label=f"power-law: α={pl['alpha']:.2f}, xmin={pl['xmin']:.0f}")
        ax.legend(fontsize=8)

    save(fig, "04_events_per_user.png")

    return vals_r, vals_p, coll_stats


# ═══════════════════════════════════════════════════════════════════════════
# §5 — Average events per day per user
# ═══════════════════════════════════════════════════════════════════════════

def section5(conn):
    """Events per active day per user."""
    print("\n" + "=" * 60)
    print("  §5 — Average events per day per user")
    print("=" * 60)

    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))) AS days
            FROM (
                SELECT did, time_us FROM bsky.records WHERE time_us > 0
                UNION ALL
                SELECT did, time_us FROM bsky.posts WHERE time_us > 0
            ) e
            GROUP BY did
        """)
        rows = cur.fetchall()
    print(f"  {len(rows):,} users ({time_mod.time() - t0:.0f}s)")

    events_per_day = np.array([r[0] / max(r[1], 1) for r in rows], dtype=np.float64)
    active_days = np.array([r[1] for r in rows], dtype=np.int64)

    print(f"\n  Events per active day (both tables):")
    for pv in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        print(f"    P{pv:>2d}: {pcts(events_per_day, pv)[0]:.1f}")

    print(f"\n  Active days:")
    for pv in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        print(f"    P{pv:>2d}: {pcts(active_days.astype(np.float64), pv)[0]:.0f}")

    # Plot
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    loglog_hist(axes[0], events_per_day, "§5a — Events per active day per user",
                "#50B86C", xlabel="Events per day")
    loglog_hist(axes[1], active_days.astype(np.float64), "§5b — Active days per user",
                "#9B59B6", xlabel="Active days")
    save(fig, "05_events_per_day.png")

    return events_per_day, active_days


# ═══════════════════════════════════════════════════════════════════════════
# §6 — Average events per hour per user
# ═══════════════════════════════════════════════════════════════════════════

def section6(conn):
    """Events per active hour per user."""
    print("\n" + "=" * 60)
    print("  §6 — Average events per hour per user")
    print("=" * 60)

    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(DISTINCT DATE_FORMAT(FROM_UNIXTIME(time_us / 1000000),
                                           '%Y-%m-%d %H')) AS hours
            FROM (
                SELECT did, time_us FROM bsky.records WHERE time_us > 0
                UNION ALL
                SELECT did, time_us FROM bsky.posts WHERE time_us > 0
            ) e
            GROUP BY did
        """)
        rows = cur.fetchall()
    print(f"  {len(rows):,} users ({time_mod.time() - t0:.0f}s)")

    events_per_hour = np.array([r[0] / max(r[1], 1) for r in rows], dtype=np.float64)
    active_hours = np.array([r[1] for r in rows], dtype=np.int64)

    print(f"\n  Events per active hour (both tables):")
    for pv in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        print(f"    P{pv:>2d}: {pcts(events_per_hour, pv)[0]:.1f}")

    # Plot
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    loglog_hist(axes[0], events_per_hour, "§6a — Events per active hour per user",
                "#E67E22", xlabel="Events per hour")
    loglog_hist(axes[1], active_hours.astype(np.float64), "§6b — Active hours per user",
                "#1ABC9C", xlabel="Active hours")
    save(fig, "06_events_per_hour.png")

    return events_per_hour, active_hours


# ═══════════════════════════════════════════════════════════════════════════
# §7 — Ratio-based categorization
# ═══════════════════════════════════════════════════════════════════════════

def section7(conn, top_n=None):
    """What do users actually do? Ratios of event types per user."""
    print("\n" + "=" * 60)
    print("  §7 — Ratio-based categorization")
    print("=" * 60)

    # Strategy: get total count and per-collection count for each user.
    # We need likes, reposts, follows from records; posts + replies from posts.
    # Do it with a single query per source, joining on did.

    t0 = time_mod.time()

    # Records: per-user event counts by collection
    limit_clause = f" LIMIT {top_n}" if top_n else ""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                did,
                COUNT(*) AS total_records,
                SUM(CASE WHEN collection = 'app.bsky.feed.like'     THEN 1 ELSE 0 END) AS n_likes,
                SUM(CASE WHEN collection = 'app.bsky.feed.repost'    THEN 1 ELSE 0 END) AS n_reposts,
                SUM(CASE WHEN collection = 'app.bsky.graph.follow'   THEN 1 ELSE 0 END) AS n_follows,
                SUM(CASE WHEN collection = 'app.bsky.graph.block'    THEN 1 ELSE 0 END) AS n_blocks
            FROM bsky.records
            WHERE time_us > 0
            GROUP BY did
            {limit_clause}
        """)
        rows_r = cur.fetchall()
    print(f"  {len(rows_r):,} users from records ({time_mod.time() - t0:.0f}s)")

    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                did,
                COUNT(*) AS total_posts,
                SUM(CASE WHEN reply_root_uri IS NULL THEN 1 ELSE 0 END) AS n_top_posts,
                SUM(CASE WHEN reply_root_uri IS NOT NULL THEN 1 ELSE 0 END) AS n_replies
            FROM bsky.posts
            WHERE time_us > 0
            GROUP BY did
            {limit_clause}
        """)
        rows_p = cur.fetchall()
    print(f"  {len(rows_p):,} users from posts ({time_mod.time() - t0:.0f}s)")

    # Merge
    u_rec = {r[0]: r for r in rows_r}
    u_pos = {r[0]: r for r in rows_p}
    all_dids = set(u_rec) | set(u_pos)

    ratios = []
    for did in all_dids:
        rr = u_rec.get(did, (did, 0, 0, 0, 0, 0))
        rp = u_pos.get(did, (did, 0, 0, 0))
        total = rr[1] + rp[1]
        if total == 0:
            continue
        ratios.append({
            "did": did,
            "total": total,
            "create_ratio": (rp[2] + rp[3]) / total,   # posts + replies
            "engage_ratio": rr[2] / total,               # likes
            "amplify_ratio": rr[3] / total,              # reposts
            "connect_ratio": rr[4] / total,              # follows
            "block_ratio": rr[5] / total,
            "n_posts": rp[2], "n_replies": rp[3],
            "n_likes": rr[2], "n_reposts": rr[3],
            "n_follows": rr[4], "n_blocks": rr[5],
        })

    # Distribution of ratios
    create_r = np.array([r["create_ratio"] for r in ratios])
    engage_r = np.array([r["engage_ratio"] for r in ratios])
    amplify_r = np.array([r["amplify_ratio"] for r in ratios])
    connect_r = np.array([r["connect_ratio"] for r in ratios])

    print(f"\n  Merged: {len(ratios):,} users")
    print(f"\n  {'Ratio':<22s} {'Zero%':>7s} {'Median':>8s} {'P75':>8s} {'P90':>8s} {'P99':>8s}")
    print(f"  {'-'*60}")
    for label, arr in [("create (posts+replies)", create_r),
                        ("engage (likes)", engage_r),
                        ("amplify (reposts)", amplify_r),
                        ("connect (follows)", connect_r)]:
        zp = 100 * np.mean(arr == 0)
        print(f"  {label:<22s} {zp:>6.1f}% {np.median(arr):>8.3f} "
              f"{pct(arr, 75):>8.3f} {pct(arr, 90):>8.3f} {pct(arr, 99):>8.3f}")

    # Dominant activity per user (which ratio is highest?)
    dominant = []
    for r in ratios:
        vals = {"create": r["create_ratio"], "engage": r["engage_ratio"],
                "amplify": r["amplify_ratio"], "connect": r["connect_ratio"]}
        dom = max(vals, key=vals.get)
        dominant.append(dom)
    dom_counts = Counter(dominant)
    print(f"\n  Dominant activity type:")
    for k in sorted(dom_counts, key=dom_counts.get, reverse=True):
        print(f"    {k:<10s}  {dom_counts[k]:>10,}  ({100*dom_counts[k]/len(ratios):.1f}%)")

    # Plots
    fig, axes = plt.subplots(2, 2, figsize=(14, 12))

    # A: create vs engage hexbin
    ax = axes[0, 0]
    m = (create_r > 0) | (engage_r > 0)
    hb = ax.hexbin(create_r[m], engage_r[m], gridsize=50, cmap="YlOrRd",
                   mincnt=1, bins="log")
    ax.set_xlabel("Create ratio (posts + replies / total)")
    ax.set_ylabel("Engage ratio (likes / total)")
    ax.set_title("§7a — Create vs Engage")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.25, linewidth=0.8)
    fig.colorbar(hb, ax=ax, label="users")

    # B: create vs amplify
    ax = axes[0, 1]
    m = (amplify_r > 0) | (create_r > 0)
    hb = ax.hexbin(create_r[m], amplify_r[m], gridsize=50, cmap="YlOrRd",
                   mincnt=1, bins="log")
    ax.set_xlabel("Create ratio")
    ax.set_ylabel("Amplify ratio (reposts / total)")
    ax.set_title("§7b — Create vs Amplify")
    fig.colorbar(hb, ax=ax, label="users")

    # C: dominant activity type per user
    ax = axes[1, 0]
    labels = sorted(dom_counts, key=dom_counts.get, reverse=True)
    colors = {"create": "#3498DB", "engage": "#E74C3C", "amplify": "#2ECC71",
              "connect": "#9B59B6"}
    values = [dom_counts[l] for l in labels]
    bar_colors = [colors.get(l, "#95A5A6") for l in labels]
    bars = ax.bar(labels, values, color=bar_colors, edgecolor="white")
    for l, v in zip(labels, values):
        ax.text(l, v + len(ratios) * 0.005, f"{v:,}\n({100*v/len(ratios):.1f}%)",
                ha="center", fontsize=10)
    ax.set_title("§7c — Dominant activity per user")
    ax.set_ylabel("Users")

    # D: event count by dominant type
    ax = axes[1, 1]
    dom_events = defaultdict(list)
    for r, dom in zip(ratios, dominant):
        dom_events[dom].append(r["total"])
    bp = ax.boxplot([dom_events[k] for k in labels], tick_labels=labels,
                    patch_artist=True, showfliers=False)
    for patch, k in zip(bp['boxes'], labels):
        patch.set_facecolor(colors.get(k, "#95A5A6"))
    ax.set_yscale("log")
    ax.set_ylabel("Total events per user")
    ax.set_title("§7d — Event count by dominant activity type")

    save(fig, "07_ratios.png")

    return ratios


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="EDA — Bluesky firehose")
    parser.add_argument("--skip", default="", help="Sections to skip (e.g. '5,6')")
    parser.add_argument("--ratio-top", type=int, default=None,
                        help="Limit ratio analysis to top N users (faster)")
    args = parser.parse_args()
    skip = {int(x.strip()) for x in args.skip.split(",") if x.strip()}

    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}")

    total_t0 = time_mod.time()

    _run(1, section1, conn, skip)
    _run(2, section2, conn, skip)
    _run(3, section3, conn, skip)
    _run(4, section4, conn, skip)
    _run(5, section5, conn, skip)
    _run(6, section6, conn, skip)
    _run(7, section7, conn, skip, top_n=args.ratio_top)

    conn.close()
    print(f"\n{'='*60}")
    print(f"  Done in {time_mod.time() - total_t0:.0f}s  →  {OUT}/")
    print(f"{'='*60}")


def _run(n, fn, conn, skip, **kw):
    if n in skip:
        print(f"\n  [§{n} — SKIPPED]")
        return
    return fn(conn, **kw)


if __name__ == "__main__":
    main()
