"""Per-user activity variability at 1h, 4h, and 8h time bins.

For each user: how many events per time block, and how steady is that rate?
Low CV = steady, high CV = bursty. Single-block users can't be measured.
"""

import os
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
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


# ── Analysis ──────────────────────────────────────────────────────────────

def analyse(conn, bin_hours, label):
    """Fetch per-user per-bin counts, compute mean/std/CV, print, plot."""
    print(f"\n{'='*60}")
    print(f"  {label}-hour bins")
    print(f"{'='*60}")

    rows = query(conn, f"""
        SELECT did, bin, COUNT(*) AS cnt
        FROM (
            SELECT did,
                   FLOOR(time_us / ({bin_hours} * 3600 * 1000000)) AS bin
            FROM ({EVENTS_SQL}) e
        ) t
        GROUP BY did, bin
    """)
    print(f"  {len(rows):,} (did, bin) rows")

    user_counts = defaultdict(list)
    for did, _bin, cnt in rows:
        user_counts[did].append(cnt)

    means, stds, cv, n_bins = [], [], [], []
    for counts in user_counts.values():
        arr = np.array(counts, dtype=np.float64)
        mu = arr.mean()
        sd = arr.std(ddof=0) if len(arr) > 1 else 0.0
        c = sd / mu if mu > 0 else 0.0
        means.append(mu)
        stds.append(sd)
        cv.append(c)
        n_bins.append(len(arr))

    means = np.array(means)
    stds = np.array(stds)
    cv = np.array(cv)
    n_bins = np.array(n_bins)
    n = len(means)
    print(f"  {n:,} users")

    # Stats table
    print(f"\n  {'':>12s}  {'mean/block':>12s}  {'std/block':>12s}  "
          f"{'CV':>8s}  {'active blocks':>14s}")
    print(f"  {'-'*62}")
    for p in [10, 25, 50, 75, 90]:
        print(f"  P{p:<9d}  {np.percentile(means, p):>12.1f}  "
              f"{np.percentile(stds, p):>12.1f}  "
              f"{np.percentile(cv, p):>8.2f}  "
              f"{np.percentile(n_bins, p):>14.0f}")

    # CV breakdown
    for t in [0.5, 1.0, 2.0]:
        print(f"  CV ≤ {t:.1f}:  {np.sum(cv <= t):>10,}  "
              f"({100*np.sum(cv <= t)/n:.1f}%)")

    # Single-block
    single = np.sum(n_bins == 1)
    print(f"\n  single block: {single:>10,}  ({100*single/n:.1f}%)  "
          f"— can't compute variability")

    # Plot
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    ax = axes[0]
    mask = (means > 0) & (stds > 0)
    hb = ax.hexbin(means[mask], stds[mask], gridsize=80, cmap="YlOrBr",
                   bins="log", mincnt=1, xscale="log", yscale="log")
    ax.plot([1, max(means)], [1, max(means)], "k--", alpha=0.3, linewidth=0.8,
            label="std = mean  (CV=1)")
    ax.set_xlabel(f"Mean events per {label}h block")
    ax.set_ylabel(f"Std of events per {label}h block")
    ax.set_title(f"Mean vs Std ({label}h blocks)", fontsize=11, fontweight="bold")
    fig.colorbar(hb, ax=ax, label="users")
    ax.legend(fontsize=8)
    ax.set_xlim(left=0.5)
    ax.set_ylim(bottom=0.5)

    ax = axes[1]
    valid = cv[(cv > 0) & np.isfinite(cv)]
    lo = np.log10(max(valid.min(), 0.01))
    hi = np.log10(valid.max())
    bins = np.logspace(lo, hi, 50) if hi > lo else 20
    palette = sns.color_palette("colorblind")
    ax.hist(valid, bins=bins, color=palette[0], alpha=0.85, edgecolor="white",
            linewidth=0.3)
    for thr, color in [(0.5, "green"), (1.0, "red"), (2.0, "orange")]:
        ax.axvline(thr, color=color, linestyle="--", alpha=0.6, linewidth=1.2)
        ax.text(thr * 1.05, ax.get_ylim()[1] * 0.85,
                f"CV={thr}", fontsize=8, color=color)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("CV (std / mean)")
    ax.set_ylabel("Users")
    ax.set_title(f"CV distribution ({label}h blocks)", fontsize=11, fontweight="bold")

    fig.tight_layout()
    path = OUT / f"user_variability_{label}h.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    for hours, label in [(4, "4"), (8, "8")]:
        analyse(conn, hours, label)

    # Side-by-side summary
    print(f"\n{'='*60}")
    print("  Summary: CV ≤ 1.0 across bins")
    print(f"{'='*60}")
    print(f"  {'bin size':>10s}  {'users':>10s}  {'%':>8s}")
    print(f"  {'-'*30}")
    for hours, label in [(4, "4"), (8, "8")]:
        rows = query(conn, f"""
            SELECT did, bin, COUNT(*) AS cnt
            FROM (
                SELECT did, FLOOR(time_us / ({hours} * 3600 * 1000000)) AS bin
                FROM ({EVENTS_SQL}) e
            ) t GROUP BY did, bin
        """)
        uc = defaultdict(list)
        for did, _b, cnt in rows:
            uc[did].append(cnt)
        cvs = []
        for counts in uc.values():
            arr = np.array(counts, dtype=np.float64)
            mu = arr.mean()
            sd = arr.std(ddof=0) if len(arr) > 1 else 0.0
            cvs.append(sd / mu if mu > 0 else 0.0)
        cvs = np.array(cvs)
        n_cv1 = np.sum(cvs <= 1.0)
        print(f"  {label+'h':>10s}  {len(cvs):>10,}  {100*n_cv1/len(cvs):>7.1f}%")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
