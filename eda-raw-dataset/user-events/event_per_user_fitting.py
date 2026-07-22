"""Distribution fitting on the events-per-user distribution.

Fits both power-law and lognormal to answer: which distribution best
describes the events-per-user data?

Result: lognormal is significantly better than power-law.
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
    """Return flat array of event counts per user."""
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
    for val, n in rows:
        data.extend([val] * int(n))
    arr = np.array(data, dtype=np.float64)
    print(f"  events/user: {len(arr):,} users, {len(rows):,} distinct values")
    return arr


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    print("── Fetching events per user ──")
    data = fetch_events_per_user(conn)
    conn.close()
    n = len(data)

    # ── Power-law fit ──────────────────────────────────────────────────
    print("\n── Power-law fit ──")
    pl_fit = powerlaw.Fit(data, discrete=True, xmin=None)

    print(f"  α        = {pl_fit.alpha:.4f}")
    print(f"  xmin     = {pl_fit.xmin:.0f}")
    print(f"  KS       = {pl_fit.D:.4f}")
    n_tail_pl = len(data[data >= pl_fit.xmin])
    print(f"  n (tail) = {n_tail_pl:,}  ({100 * n_tail_pl / n:.1f}% of users)")

    # ── Lognormal fit ─────────────────────────────────────────────────
    print("\n── Lognormal fit ──")
    shape, loc, scale = stats.lognorm.fit(data, floc=0)
    mu = np.log(scale)
    sigma = shape

    print(f"  μ  = {mu:.4f}")
    print(f"  σ  = {sigma:.4f}")
    print(f"  mode   = {np.exp(mu - sigma**2):.1f}")
    print(f"  median = {np.exp(mu):.1f}")
    print(f"  mean   = {np.exp(mu + sigma**2 / 2):.1f}")

    # ── Distribution comparison ───────────────────────────────────────
    print("\n── Distribution comparison (log-likelihood ratio) ──")
    R, p = pl_fit.distribution_compare("power_law", "lognormal_positive")
    pl_better = R > 0 and p < 0.05
    ln_better = R < 0 and p < 0.05
    print(f"  power_law vs lognormal:  R = {R:,.1f}, p = {p:.4f}  →  "
          f"{'power law significantly better' if pl_better else 'lognormal significantly better' if ln_better else 'no significant difference'}")

    # ── Parameter TSV ─────────────────────────────────────────────────
    tsv_path = OUT / "fitting_parameters.tsv"
    with open(tsv_path, "w") as f:
        f.write("distribution\tparameter\tvalue\n")
        f.write(f"powerlaw\talpha\t{pl_fit.alpha:.4f}\n")
        f.write(f"powerlaw\txmin\t{pl_fit.xmin:.0f}\n")
        f.write(f"powerlaw\tKS\t{pl_fit.D:.4f}\n")
        f.write(f"powerlaw\tn_tail\t{n_tail_pl}\n")
        f.write(f"powerlaw\tpct_tail\t{100 * n_tail_pl / n:.2f}\n")
        f.write(f"lognormal\tmu\t{mu:.4f}\n")
        f.write(f"lognormal\tsigma\t{sigma:.4f}\n")
        f.write(f"lognormal\tmode\t{np.exp(mu - sigma**2):.1f}\n")
        f.write(f"lognormal\tmedian\t{np.exp(mu):.1f}\n")
        f.write(f"lognormal\tmean\t{np.exp(mu + sigma**2 / 2):.1f}\n")
        f.write(f"comparison\tLLR_powerlaw_vs_lognormal\t{R:,.1f}\n")
        f.write(f"comparison\tp_value\t{p:.4f}\n")
        winner = "lognormal" if ln_better else ("powerlaw" if pl_better else "none")
        f.write(f"comparison\twinner\t{winner}\n")
    print(f"\n  → saved {tsv_path}")

    # ── Plot: CCDF with both fits ─────────────────────────────────────

    fig, ax = plt.subplots(figsize=(10, 5.5))
    palette = sns.color_palette("colorblind")

    # Data CCDF
    sorted_d = np.sort(data)
    ccdf = 1 - np.arange(len(sorted_d)) / len(sorted_d)
    ax.step(sorted_d, ccdf, where="post", color=palette[0], linewidth=1.2,
            label="data")

    # Lognormal fit
    x_fit = np.logspace(np.log10(data.min()), np.log10(data.max()), 200)
    y_ln = 1 - stats.lognorm.cdf(x_fit, sigma, loc=0, scale=scale)
    ax.plot(x_fit, y_ln, color=palette[1], linewidth=2,
            label=f"lognormal  μ={mu:.2f}, σ={sigma:.2f}")

    # Power-law fit (only above xmin)
    xmin = pl_fit.xmin
    x_pl = np.logspace(np.log10(xmin), np.log10(data.max()), 100)
    y_pl = (xmin / x_pl) ** (pl_fit.alpha - 1) * (n_tail_pl / n)
    ax.plot(x_pl, y_pl, color=palette[2], linestyle="--", linewidth=2,
            label=f"power-law  α={pl_fit.alpha:.2f}, xmin={xmin:.0f}")

    ax.axvline(xmin, color=palette[2], linestyle=":", alpha=0.4, linewidth=1.2)
    ax.text(xmin * 1.05, 0.5, f"  xmin = {xmin:.0f}", fontsize=9,
            color=palette[2], rotation=90, va="center")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Events per user")
    ax.set_ylabel("P(Events ≥ x)")
    ax.set_title("CCDF — Events per user (lognormal vs power-law)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)

    # Stats box
    winner_str = "lognormal" if ln_better else ("power-law" if pl_better else "no clear winner")
    text = (
        f"n = {n:,}\n"
        f"lognormal: μ={mu:.2f}, σ={sigma:.2f}\n"
        f"power-law: α={pl_fit.alpha:.2f}, xmin={xmin:.0f}\n"
        f"LLR test: {winner_str} (p={p:.4f})"
    )
    ax.text(0.95, 0.95, text, transform=ax.transAxes, ha="right", va="top",
            fontsize=9,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85))

    path = OUT / "event_per_user_fitting.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
