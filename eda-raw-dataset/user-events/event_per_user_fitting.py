"""Lognormal fit on the events-per-user distribution.

Fits a lognormal and compares against power-law to confirm
lognormal is the better model.
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


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    print("── Fetching events per user ──")
    data = fetch_events_per_user(conn)
    conn.close()
    n = len(data)
    print(f"  n = {n:,} users\n")

    # ── Lognormal fit ─────────────────────────────────────────────────
    print("── Lognormal fit ──")
    shape, loc, scale = stats.lognorm.fit(data, floc=0)
    mu = np.log(scale)
    sigma = shape

    print(f"  μ      = {mu:.4f}")
    print(f"  σ      = {sigma:.4f}")
    print(f"  mode   = {np.exp(mu - sigma**2):.1f}")
    print(f"  median = {np.exp(mu):.1f}")
    print(f"  mean   = {np.exp(mu + sigma**2 / 2):.1f}")

    # ── Compare with power-law ────────────────────────────────────────
    print("\n── Distribution comparison ──")
    pl_fit = powerlaw.Fit(data, discrete=True, xmin=None, verbose=False)
    R, p = pl_fit.distribution_compare("power_law", "lognormal_positive")
    ln_better = R < 0 and p < 0.05
    print(f"  power_law vs lognormal:  R = {R:,.1f}, p = {p:.4f}  →  "
          f"{'lognormal significantly better' if ln_better else 'no significant difference'}")

    # ── Plot: histogram + lognormal PDF ───────────────────────────────

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

    # Stats box
    text = (
        f"n = {n:,}\n"
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
    print(f"\n  → saved {path}")

    # ── Parameter TSV ─────────────────────────────────────────────────
    tsv_path = OUT / "fitting_parameters.tsv"
    with open(tsv_path, "w") as f:
        f.write("distribution\tparameter\tvalue\n")
        f.write(f"lognormal\tmu\t{mu:.4f}\n")
        f.write(f"lognormal\tsigma\t{sigma:.4f}\n")
        f.write(f"lognormal\tmode\t{np.exp(mu - sigma**2):.1f}\n")
        f.write(f"lognormal\tmedian\t{np.exp(mu):.1f}\n")
        f.write(f"lognormal\tmean\t{np.exp(mu + sigma**2 / 2):.1f}\n")
        f.write(f"comparison\tLLR_lognormal_vs_powerlaw\t{-R:,.1f}\n")
        f.write(f"comparison\tp_value\t{p:.4f}\n")
        f.write(f"comparison\twinner\t{'lognormal' if ln_better else 'none'}\n")
    print(f"  → saved {tsv_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
