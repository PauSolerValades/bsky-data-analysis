"""Posts per session, split by user type = (duration_family, gap_family) pair.

Types come from distribution-fit/results/pair_params_wide.tsv (per-user
parametric pair, trusted at n_obs >= 30 both sides). Sessions are sliced from
the shared cache (results/posts_per_session_cache.npz) via the sorted-did
boundaries in results/sessions_per_user.tsv.

Default: all pairs with >= 1% of trusted users. --pair dur,gap for one.

Output: plots/posts_per_session__<dur>__<gap>.png (one per pair),
        results/pair_posts_dist.tsv (appendix table, all pairs).

Usage:
    uv run inter-post-creation/posts_per_session_by_pair.py [--pair weibull_min,lognorm]
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns

HERE = Path(__file__).resolve().parent
REPO_SESSIONS = HERE.parent
WIDE = REPO_SESSIONS / "distribution-fit" / "results" / "pair_params_wide.tsv"
SPU = HERE / "results" / "sessions_per_user.tsv"
MIN_PAIR_SHARE = 0.01

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

DISPLAY = {"power_tail": "Power-law", "weibull_min": "Weibull", "lognorm": "Lognorm",
           "gamma": "Gamma", "expon": "Exp", "fisk": "Fisk"}


def session_did_index():
    """session -> did index, via sorted-did boundaries (cache order)."""
    spu = pl.read_csv(SPU, separator="\t")
    dids = spu["did"].to_numpy()
    n_sess = spu["n_sessions"].to_numpy()
    return dids, np.repeat(np.arange(len(dids)), n_sess)


def pair_did_positions(dids, dur, gap):
    wide = (pl.read_csv(WIDE, separator="\t")
              .filter((pl.col("dur_family") == dur) & (pl.col("gap_family") == gap)))
    pd = wide["did"].to_numpy()
    pos = np.searchsorted(dids, pd)
    ok = (pos < len(dids)) & (dids[pos] == pd)
    if (~ok).any():
        print(f"  warning: {(~ok).sum()} pair users not in sessions table", file=sys.stderr)
    return pos[ok]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default=None, help="dur,gap — single pair")
    args = ap.parse_args()

    z = np.load(HERE / "results" / "posts_per_session_cache.npz")
    counts = z["count"]
    dids, sess_did = session_did_index()

    wide = pl.read_csv(WIDE, separator="\t")
    pair_counts = (wide.group_by(["dur_family", "gap_family"]).len(name="n_users")
                       .sort("n_users", descending=True))
    total_users = int(pair_counts["n_users"].sum())
    keep = pair_counts.filter(pl.col("n_users") >= MIN_PAIR_SHARE * total_users)
    if args.pair:
        dur, gap = args.pair.split(",")
        keep = pair_counts.filter((pl.col("dur_family") == dur)
                                  & (pl.col("gap_family") == gap))
    print(f"trusted users: {total_users:,}  pairs >= {MIN_PAIR_SHARE:.0%}: "
          f"{keep.height} ({keep['n_users'].sum():,} users = "
          f"{100 * keep['n_users'].sum() / total_users:.1f}%)", file=sys.stderr)

    dist_rows = []
    for dur, gap, n_users in keep.iter_rows():
        pos = pair_did_positions(dids, dur, gap)
        sel = counts[np.isin(sess_did, pos)]
        n = len(sel)
        dist = np.bincount(sel)
        tail6 = dist[6:].sum()
        pct = np.concatenate([dist[:6] / n * 100, [tail6 / n * 100]])
        print(f"\n{dur} x {gap}: {n_users:,} users, {n:,} sessions "
              f"({100 * n / len(counts):.1f}% of all)", file=sys.stderr)
        for k in range(6):
            print(f"  {k} posts: {pct[k]:.2f}%", file=sys.stderr)
            dist_rows.append((dur, gap, n_users, k, int(dist[k]), round(pct[k], 2)))
        print(f"  >=6 posts: {pct[6]:.2f}%", file=sys.stderr)
        dist_rows.append((dur, gap, n_users, "6+", int(tail6), round(pct[6], 2)))

        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.bar(np.arange(7), pct, color=sns.color_palette("colorblind")[0], width=0.7)
        ax.bar_label(ax.containers[0], fmt="%.1f", padding=2, fontsize=9)
        ax.set_xticks(np.arange(7))
        ax.set_xticklabels(["0", "1", "2", "3", "4", "5", "6+"])
        ax.set_xlabel("posts per session")
        ax.set_ylabel("share of sessions (%)")
        ax.set_title(f"Post creations per session — {DISPLAY[dur]} $\\times$ "
                     f"{DISPLAY[gap]} users")
        ax.set_ylim(0, pct.max() * 1.15)
        fig.tight_layout()
        p = HERE / "plots" / f"posts_per_session__{dur}__{gap}.png"
        fig.savefig(p, dpi=300)
        print(f"  → {p.name}", file=sys.stderr)

    if args.pair is None:
        out = HERE / "results" / "pair_posts_dist.tsv"
        (pl.DataFrame(dist_rows, schema=["dur_family", "gap_family", "n_users",
                                         "k", "n_sessions", "pct"], orient="row")
           .write_csv(out, separator="\t"))
        print(f"\n→ {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
