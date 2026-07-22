"""Power-law vs lognormal comparison — events per user, per day, per hour.

For each distribution, fits both models and reports the log-likelihood
ratio test (R, p-value) to determine which distribution is a better fit.
Outputs a TSV and a lognormal PDF plot for events per user.
"""

import os
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import powerlaw
import pymysql
import seaborn as sns
from dotenv import load_dotenv
from scipy import stats

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


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    print("── Fetching data ──")
    data_user = fetch_events_per_user(conn)
    data_day  = fetch_events_per_day(conn)
    data_hour = fetch_events_per_hour(conn)
    conn.close()

    datasets = [
        (data_user, "Events per user"),
        (data_day,  "Events per day"),
        (data_hour, "Events per hour"),
    ]

    # ── Power-law vs lognormal for all three ──────────────────────────

    print("\n── Power-law vs lognormal (LLR test) ──")
    print(f"  {'Distribution':<20s} {'R':>14s} {'p':>10s} {'winner':>12s}")
    print(f"  {'-'*58}")

    results = []
    for data, name in datasets:
        data_pos = data[data > 0]
        pl_fit = powerlaw.Fit(data_pos, discrete=True, xmin=1, verbose=False)
        R, p = pl_fit.distribution_compare("power_law", "lognormal_positive")
        ln_better = R < 0 and p < 0.05
        pl_better = R > 0 and p < 0.05
        winner = "lognormal" if ln_better else ("powerlaw" if pl_better else "none")
        results.append((name, R, p, winner))
        print(f"  {name:<20s} {R:>14,.1f} {p:>10.4f} {winner:>12s}")

    # ── TSV output ────────────────────────────────────────────────────

    tsv_path = OUT / "fitting_comparison.tsv"
    with open(tsv_path, "w") as f:
        f.write("distribution\tLLR_R\tp_value\twinner\n")
        for name, R, p, winner in results:
            f.write(f"{name}\t{R:,.1f}\t{p:.4f}\t{winner}\n")
    print(f"\n  → saved {tsv_path}")

    # ── Plot: lognormal PDF for events per user ───────────────────────

    data = data_user[data_user > 0]
    shape, loc, scale = stats.lognorm.fit(data, floc=0)
    mu = np.log(scale)
    sigma = shape

    palette = sns.color_palette("colorblind")
    fig, ax = plt.subplots(figsize=(8, 5))

    lo = np.log10(data.min())
    hi = np.log10(data.max())
    bins = np.logspace(lo, hi, 80)

    ax.hist(data, bins=bins, color=palette[0], alpha=0.6, edgecolor="white",
            linewidth=0.2, density=True, label="data")

    x_fit = np.logspace(lo, hi, 200)
    pdf = stats.lognorm.pdf(x_fit, sigma, loc=0, scale=scale)
    ax.plot(x_fit, pdf, color=palette[1], linewidth=2,
            label=f"lognormal  μ={mu:.2f}, σ={sigma:.2f}")

    ax.set_xscale("log")
    ax.set_xlabel("Events per user")
    ax.set_ylabel("Density")
    ax.set_title("Events per user — lognormal fit", fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.3f}"))

    text = (
        f"n = {len(data):,}\n"
        f"μ = {mu:.2f}\n"
        f"σ = {sigma:.2f}\n"
        f"median = {np.exp(mu):.1f}"
    )
    ax.text(0.95, 0.95, text, transform=ax.transAxes, ha="right", va="top",
            fontsize=9, bbox=dict(boxstyle="round,pad=0.3",
                                  facecolor="white", alpha=0.85))

    fig.tight_layout()
    path = OUT / "event_per_user_fitting.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
