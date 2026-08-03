"""Check parameter stability: compare two session tables.

Relevant postulates:
  - A good method shouldn't produce wildly different sessions when
    parameters change slightly (small epsilon → stability).

Usage:
    uv run sessions/analysis/stability.py --table-a sessions_hdbscan_e50 --table-b sessions_hdbscan_e70
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env")
from running_locally.local_db import Where, get_connection as local_connect

WHERE = Where.from_env()
TBL_PREFIX = "pau_db." if WHERE == Where.SERVER else ""

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stability check.")
    parser.add_argument("--table-a", type=str, required=True)
    parser.add_argument("--table-b", type=str, required=True)
    args = parser.parse_args()

    conn = local_connect(Where.from_env(), repo_root=str(REPO))

    # Per-user session counts (subqueries — StarRocks has no TEMP TABLE)
    rows = conn.query(f"""
        SELECT
            COALESCE(a.did, b.did) AS did,
            COALESCE(a.n_sessions, 0) AS n_a,
            COALESCE(b.n_sessions, 0) AS n_b
        FROM (
            SELECT did, COUNT(*) AS n_sessions
            FROM {TBL_PREFIX}{args.table_a}
            GROUP BY did
        ) a
        FULL OUTER JOIN (
            SELECT did, COUNT(*) AS n_sessions
            FROM {TBL_PREFIX}{args.table_b}
            GROUP BY did
        ) b ON a.did = b.did
    """)
    conn.close()

    dids = [r[0] for r in rows]
    n_a = np.array([r[1] for r in rows], dtype=np.int64)
    n_b = np.array([r[2] for r in rows], dtype=np.int64)

    # Metrics
    mask_both = (n_a > 0) & (n_b > 0)
    diff_pct = np.abs(n_a[mask_both] - n_b[mask_both]) / np.maximum(n_a[mask_both], 1)
    corr = np.corrcoef(n_a[mask_both], n_b[mask_both])[0, 1]
    pct_exact = 100 * np.mean(n_a[mask_both] == n_b[mask_both])

    print(f"── Stability: {args.table_a} vs {args.table_b} ──")
    print(f"  Users in A only:     {((n_a > 0) & (n_b == 0)).sum():>12,}")
    print(f"  Users in B only:     {((n_a == 0) & (n_b > 0)).sum():>12,}")
    print(f"  Users in both:       {mask_both.sum():>12,}")
    print(f"  Correlation (Pearson r): {corr:.4f}")
    print(f"  Exact match:         {pct_exact:.1f}%")
    print(f"  Median relative diff:  {np.median(diff_pct):.3f}")
    print(f"  P90 relative diff:     {np.percentile(diff_pct, 90):.3f}")
    print(f"  Sessions A / Sessions B: {n_a.sum():,} / {n_b.sum():,} "
          f"({100*n_a.sum()/max(n_b.sum(), 1):.1f}%)")

    # ── Plot ───────────────────────────────────────────────────────────

    fig, ax = plt.subplots(figsize=(8, 8))
    palette = sns.color_palette("colorblind")

    # Jitter for visibility
    rng = np.random.default_rng(42)
    j_a = n_a[mask_both].astype(np.float64) + rng.uniform(-0.3, 0.3, mask_both.sum())
    j_b = n_b[mask_both].astype(np.float64) + rng.uniform(-0.3, 0.3, mask_both.sum())

    # ponytail: xscale/yscale in hexbin bins in log space (set_xscale would bin linearly)
    hb = ax.hexbin(j_a, j_b, gridsize=50, cmap="YlOrRd", mincnt=1, bins="log",
                   xscale="log", yscale="log")
    max_val = max(n_a[mask_both].max(), n_b[mask_both].max())
    ax.plot([0, max_val], [0, max_val], "k--", alpha=0.3, linewidth=0.8,
            label="y = x")
    ax.set_xlabel(f"Sessions per user — {args.table_a}")
    ax.set_ylabel(f"Sessions per user — {args.table_b}")
    ax.set_title(f"Stability check\nr = {corr:.3f}   median diff = {np.median(diff_pct):.3f}   "
                 f"exact = {pct_exact:.1f}%",
                 fontsize=11, fontweight="bold")
    fig.colorbar(hb, ax=ax, label="users")
    ax.legend(fontsize=8)

    fig.tight_layout()
    path = OUT / f"stability__{args.table_a}_vs_{args.table_b}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


if __name__ == "__main__":
    main()
