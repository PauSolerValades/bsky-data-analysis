"""Lognormal fit on events-per-day and events-per-hour per user.

Fits a lognormal to answer: where do tourists end and regular users begin?
"""

import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import powerlaw
import pymysql
import seaborn as sns
from dotenv import load_dotenv
from scipy import stats

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


def fetch_per_user_rates(conn):
    """Return (events_per_day, events_per_hour) as flat arrays."""
    rows = query(conn, f"""
        SELECT val_day, val_hour, COUNT(*) AS n_users
        FROM (
            SELECT did,
                   COUNT(*) / GREATEST(
                       COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))), 1
                   ) AS val_day,
                   COUNT(*) / GREATEST(
                       COUNT(DISTINCT DATE_FORMAT(FROM_UNIXTIME(time_us / 1000000),
                                                  '%Y-%m-%d %H')), 1
                   ) AS val_hour
            FROM ({EVENTS_SQL}) e
            GROUP BY did
        ) per_user
        GROUP BY val_day, val_hour
        ORDER BY val_day, val_hour
    """)
    day_data, hour_data = [], []
    for vd, vh, n in rows:
        day_data.extend([float(vd)] * int(n))
        hour_data.extend([float(vh)] * int(n))
    day_arr = np.array(day_data, dtype=np.float64)
    hour_arr = np.array(hour_data, dtype=np.float64)
    print(f"  events/day:  {len(day_arr):,} users, {len(set(day_data)):,} distinct")
    print(f"  events/hour: {len(hour_arr):,} users, {len(set(hour_data)):,} distinct")
    return day_arr[day_arr > 0], hour_arr[hour_arr > 0]


def analyse(name, data):
    """Fit lognormal, compute thresholds, compare vs powerlaw, plot."""
    shape, loc, scale = stats.lognorm.fit(data, floc=0)
    mu = np.log(scale)
    sigma = shape

    print(f"\n── {name} — lognormal fit ──")
    print(f"  μ   = {mu:.4f}")
    print(f"  σ   = {sigma:.4f}")
    print(f"  mode   = {np.exp(mu - sigma**2):.2f}")
    print(f"  median = {np.exp(mu):.2f}")
    print(f"  mean   = {np.exp(mu + sigma**2 / 2):.2f}")

    thresholds = [
        (f"μ-2σ", np.exp(mu - 2 * sigma)),
        (f"μ-σ",  np.exp(mu - sigma)),
        ("P10",   np.percentile(data, 10)),
        ("P25",   np.percentile(data, 25)),
        ("median", np.exp(mu)),
    ]

    print(f"\n  {'Threshold':>8s}  {'Value':>8s}  {'% below':>8s}")
    print(f"  {'-'*30}")
    for label, v in thresholds:
        pct = 100 * np.mean(data < v)
        print(f"  {label:>8s}  {v:>8.1f}  {pct:>7.1f}%")

    # Distribution comparison
    fit = powerlaw.Fit(data, discrete=True, xmin=1, verbose=0)
    R, p = fit.distribution_compare("lognormal_positive", "power_law")
    print(f"\n  lognormal vs power_law:  R = {R:,.0f}, p = {p:.4f}", end="")
    if R > 0 and p < 0.05:
        print("  ✓ lognormal significantly better")
    elif R < 0 and p < 0.05:
        print("  ✓ power law significantly better")
    else:
        print("  — no significant difference")

    # Plot
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

    ax = axes[0]
    sorted_d = np.sort(data)
    ccdf = 1 - np.arange(len(sorted_d)) / len(sorted_d)
    palette = sns.color_palette("colorblind")
    ax.step(sorted_d, ccdf, where="post", color=palette[0], linewidth=1.2, label="data")

    x_fit = np.logspace(np.log10(data.min()), np.log10(data.max()), 200)
    y_fit = 1 - stats.lognorm.cdf(x_fit, sigma, loc=0, scale=scale)
    ax.plot(x_fit, y_fit, "r--", linewidth=2, label=f"lognormal  μ={mu:.2f}, σ={sigma:.2f}")

    for label, v in thresholds[:3]:  # μ-2σ, μ-σ, median
        ax.axvline(v, color="red", linestyle=":", alpha=0.4, linewidth=0.8)
        ax.text(v * 1.05, 0.55, f"{label}\n{v:.1f}", fontsize=8, color="red", alpha=0.7)

    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel(name)
    ax.set_ylabel(f"P({name} ≥ x)")
    ax.set_title(f"CCDF — {name} per user", fontsize=12, fontweight="bold")
    ax.legend(fontsize=8)

    ax = axes[1]
    lo, hi = np.log10(data.min()), np.log10(data.max())
    bins = np.logspace(lo, hi, 60)
    ax.hist(data, bins=bins, color=palette[1], alpha=0.7, edgecolor="white",
            linewidth=0.3, density=True, label="data")
    pdf = stats.lognorm.pdf(x_fit, sigma, loc=0, scale=scale)
    ax.plot(x_fit, pdf, "r-", linewidth=2, label="lognormal fit")

    for label, v in thresholds[:3]:
        ax.axvline(v, color="red", linestyle=":", alpha=0.4, linewidth=0.8)

    ax.set_xscale("log")
    ax.set_xlabel(name)
    ax.set_ylabel("Density")
    ax.set_title(f"PDF — {name} per user", fontsize=12, fontweight="bold")
    ax.legend(fontsize=8)

    fig.tight_layout()
    slug = name.replace(" ", "_").replace("/", "_")
    path = OUT / f"user_lognormal_{slug}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    print("── Fetching per-user rates ──")
    data_day, data_hour = fetch_per_user_rates(conn)
    conn.close()

    analyse("events/day", data_day)
    print()
    analyse("events/hour", data_hour)

    print("\nDone.")


if __name__ == "__main__":
    main()
