"""Per-user activity statistics.

Answers:
  1. How many events does each user have?        → histogram
  2. How many events per active day per user?    → histogram
  3. How many events per active hour per user?   → histogram

Goal: pick thresholds to filter out inactive users.
"""

import os
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import seaborn as sns
import pymysql
from dotenv import load_dotenv

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,  # LaTeX not installed; set True if available
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

# ── Config ────────────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO / ".env")

DB = {
    "host": os.environ["DATABASE_HOST"],
    "port": int(os.environ["DATABASE_PORT"]),
    "user": os.environ["DATABASE_USER"],
    "password": os.environ["PAU_PASSWORD"],
    "database": "bsky",
    "charset": "utf8mb4",
}

OUT = Path(__file__).resolve().parent / "plots"
OUT.mkdir(exist_ok=True)

# Collections to exclude (protocol fossils, not real user activity)
EXCLUDE_COLLECTIONS = (
    "'app.bsky.feed.post'",          # handled via bsky.posts instead
    "'app.bsky.graph.repost'",       # deprecated
    "'app.bsky.graph.verification'", # fossil
    "'app.bsky.lexicon.collection'", # fossil
    "'app.bsky.graph.cancellation'", # fossil
    "'app.bsky.draft.createDraft'",  # fossil
)

EXCLUDE_SQL = " AND collection NOT IN (" + ", ".join(EXCLUDE_COLLECTIONS) + ")"

# Unified event source: records (minus fossils, minus feed.post) + posts
EVENTS_SQL = f"""
    SELECT did, time_us FROM bsky.records
    WHERE 1=1{EXCLUDE_SQL}
    UNION ALL
    SELECT did, time_us FROM bsky.posts
"""


# ── Database helpers ──────────────────────────────────────────────────────

def query(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def fetch_distribution(conn, value_expr, label):
    """Run a query returning (value, count) pairs and expand into a flat array.

    value_expr is the per-user aggregate (e.g. 'COUNT(*)').
    """
    rows = query(conn, f"""
        SELECT val, COUNT(*) AS n_users
        FROM (
            SELECT did, {value_expr} AS val
            FROM ({EVENTS_SQL}) e
            GROUP BY did
        ) per_user
        GROUP BY val
        ORDER BY val
    """)
    vals = []
    for v, n in rows:
        vals.extend([float(v)] * int(n))
    print(f"  {label}: {len(vals):,} users, {len(rows):,} distinct values")
    return np.array(vals)


# ── Plotting ──────────────────────────────────────────────────────────────

def histogram(data, title, xlabel, out_name, bins="auto"):
    """Log-log histogram with summary stats."""
    data = data[data > 0]
    if len(data) == 0:
        return

    fig, ax = plt.subplots(figsize=(10, 5.5))

    if bins == "auto":
        lo = np.log10(data.min())
        hi = np.log10(data.max())
        bins = np.logspace(lo, hi, 60)

    palette = sns.color_palette("colorblind")
    ax.hist(data, bins=bins, color=palette[0], alpha=0.85, edgecolor="white", linewidth=0.3)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Users")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Percentile annotations
    ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    pv = np.percentile(data, ps)
    for p, v in zip(ps, pv):
        ax.axvline(v, color="red", linestyle="--", alpha=0.25, linewidth=0.7)
        ax.text(v * 1.1, ax.get_ylim()[1] * 0.85 ** (ps.index(p) + 1),
                f"P{p}={v:.1f}", fontsize=7, color="red", alpha=0.8)

    # Stats box
    text = (
        f"n = {len(data):,}\n"
        f"median = {np.median(data):,.1f}\n"
        f"mean = {data.mean():,.1f}\n"
        f"max = {data.max():,.0f}"
    )
    ax.text(0.95, 0.95, text, transform=ax.transAxes, ha="right", va="top",
            fontsize=9, bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85))

    path = OUT / out_name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    # ── §1: events per user ─────────────────────────────────────────────

    print("── §1: events per user ──")
    data1 = fetch_distribution(conn, "COUNT(*)", "events/user")
    histogram(data1,
              "§1 — Events per user (all sources, excluding fossils)",
              "Events per user",
              "user_01_events_per_user.png")

    # ── §2: events per active day per user ─────────────────────────────

    print("── §2: events per active day per user ──")
    rows = query(conn, f"""
        SELECT total, days
        FROM (
            SELECT did,
                   COUNT(*) AS total,
                   COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))) AS days
            FROM ({EVENTS_SQL}) e
            GROUP BY did
        ) t
    """)
    data2 = np.array([total / max(days, 1) for total, days in rows])
    print(f"  events/day: {len(data2):,} users")
    histogram(data2,
              "§2 — Events per active day per user",
              "Events per day",
              "user_02_events_per_day.png")

    # ── §3: events per active hour per user ────────────────────────────

    print("── §3: events per active hour per user ──")
    rows = query(conn, f"""
        SELECT total, hours
        FROM (
            SELECT did,
                   COUNT(*) AS total,
                   COUNT(DISTINCT DATE_FORMAT(FROM_UNIXTIME(time_us / 1000000), '%Y-%m-%d %H')) AS hours
            FROM ({EVENTS_SQL}) e
            GROUP BY did
        ) t
    """)
    data3 = np.array([total / max(hours, 1) for total, hours in rows])
    print(f"  events/hour: {len(data3):,} users")
    histogram(data3,
              "§3 — Events per active hour per user",
              "Events per hour",
              "user_03_events_per_hour.png")

    # ── Summary table ──────────────────────────────────────────────────

    print("── Percentile summary ──")
    ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    print(f"  {'Pct':>6s}  {'Events/user':>12s}  {'Events/day':>12s}  {'Events/hr':>12s}")
    print(f"  {'-'*52}")
    for p in ps:
        e1 = np.percentile(data1, p)
        e2 = np.percentile(data2, p)
        e3 = np.percentile(data3, p)
        print(f"  P{p:<4d}  {e1:>12.1f}  {e2:>12.1f}  {e3:>12.1f}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
