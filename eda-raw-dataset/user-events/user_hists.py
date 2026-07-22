"""Plain histograms — events per user, per day, per hour.

Three-panel figure showing the empirical distributions.
Used to visually justify threshold cutoffs.
"""

import os
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pymysql
import seaborn as sns
from dotenv import load_dotenv

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 10,
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

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)

EXCLUDE_COLLECTIONS = (
    "'app.bsky.feed.post'",
    "'app.bsky.graph.repost'",
    "'app.bsky.graph.verification'",
    "'app.bsky.lexicon.collection'",
    "'app.bsky.graph.cancellation'",
    "'app.bsky.draft.createDraft'",
)
EXCLUDE_SQL = " AND collection NOT IN (" + ", ".join(EXCLUDE_COLLECTIONS) + ")"

EVENTS_SQL = f"""
    SELECT did, time_us FROM bsky.records WHERE 1=1{EXCLUDE_SQL}
    UNION ALL
    SELECT did, time_us FROM bsky.posts
"""


# ── Helpers ───────────────────────────────────────────────────────────────

def query(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def fetch_events_per_user(conn):
    rows = query(conn, f"""
        SELECT val, COUNT(*) AS n_users
        FROM (
            SELECT did, COUNT(*) AS val
            FROM ({EVENTS_SQL}) e
            GROUP BY did
        ) per_user
        GROUP BY val
        ORDER BY val
    """)
    data = []
    for v, n in rows:
        data.extend([float(v)] * int(n))
    return np.array(data)


def fetch_events_per_day(conn):
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
    return np.array([total / max(days, 1) for total, days in rows])


def fetch_events_per_hour(conn):
    rows = query(conn, f"""
        SELECT total, hours
        FROM (
            SELECT did,
                   COUNT(*) AS total,
                   COUNT(DISTINCT DATE_FORMAT(FROM_UNIXTIME(time_us / 1000000),
                                              '%Y-%m-%d %H')) AS hours
            FROM ({EVENTS_SQL}) e
            GROUP BY did
        ) t
    """)
    return np.array([total / max(hours, 1) for total, hours in rows])


# ── Plotting ──────────────────────────────────────────────────────────────

def plot_panel(ax, data, title, xlabel):
    """Plain log-log histogram with percentile markers."""
    data = data[data > 0]
    if len(data) == 0:
        return

    lo = np.log10(data.min())
    hi = np.log10(data.max())
    bins = np.logspace(lo, hi, 50)

    palette = sns.color_palette("colorblind")

    ax.hist(data, bins=bins, color=palette[0], alpha=0.7, edgecolor="white",
            linewidth=0.3)

    # Percentile lines
    ps = [25, 50, 75, 90]
    pv = np.percentile(data, ps)
    for p, v in zip(ps, pv):
        ax.axvline(v, color=palette[2], linestyle="--", alpha=0.4, linewidth=0.8)
        ax.text(v * 1.08, ax.get_ylim()[1] * 0.92 ** (ps.index(p) + 1),
                f"P{p}={v:.1f}", fontsize=7, color=palette[2], alpha=0.8)

    # Stats box
    text = (
        f"n = {len(data):,}\n"
        f"median = {np.median(data):,.1f}\n"
        f"mean = {data.mean():,.1f}"
    )
    ax.text(0.95, 0.95, text, transform=ax.transAxes, ha="right", va="top",
            fontsize=8, bbox=dict(boxstyle="round,pad=0.3",
                                  facecolor="white", alpha=0.85))

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Users")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    print("── Fetching data ──")
    data_user = fetch_events_per_user(conn)
    data_day  = fetch_events_per_day(conn)
    data_hour = fetch_events_per_hour(conn)
    conn.close()

    print(f"  events/user:  {len(data_user):,}")
    print(f"  events/day:   {len(data_day):,}")
    print(f"  events/hour:  {len(data_hour):,}")

    # ── Plot ───────────────────────────────────────────────────────────

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))

    datasets = [
        (axes[0], data_user, "Events per user", "Events per user"),
        (axes[1], data_day,  "Events per active day", "Events per day"),
        (axes[2], data_hour, "Events per active hour", "Events per hour"),
    ]

    for ax, data, title, xlabel in datasets:
        plot_panel(ax, data, title, xlabel)

    fig.tight_layout()
    path = OUT / "user_hists.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  → saved {path}")

    # ── Console summary ───────────────────────────────────────────────

    print(f"\n  {'Distribution':<25s} {'P25':>8s} {'P50':>8s} {'P75':>8s} {'P90':>8s}")
    print(f"  {'-'*60}")
    for ax, data, title, xlabel in datasets:
        ps = np.percentile(data, [25, 50, 75, 90])
        print(f"  {title:<25s} {ps[0]:>8.1f} {ps[1]:>8.1f} {ps[2]:>8.1f} {ps[3]:>8.1f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
