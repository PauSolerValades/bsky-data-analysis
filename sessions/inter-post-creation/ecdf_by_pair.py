"""Inter-post gap ECDFs, split by user type = (duration_family, gap_family) pair.

Re-groups the per-type ECDF work (previously by global family, see
export_ecdf_by_family.py) onto the settled parametric types from
distribution-fit/results/pair_params_wide.tsv. For each pair: ECDF of
within-session vs global inter-post gaps (step plot, log-x), plus the
within-gap sample export for the simulator.

Default: all pairs with >= 1% of trusted users. --pair dur,gap for one.

Output: plots/interpost_ecdf__<dur>__<gap>.png (one per pair),
        results/within_ecdf__<dur>__<gap>.txt (250k gaps, one per line).

Usage:
    uv run inter-post-creation/ecdf_by_pair.py [--pair weibull_min,lognorm]
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns

HERE = Path(__file__).resolve().parent
WIDE = HERE.parent / "distribution-fit" / "results" / "pair_params_wide.tsv"
MIN_PAIR_SHARE = 0.01
MAX_LINES = 250_000

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


def ecdf(v, ax, label, color):
    xs = np.sort(v)
    ax.plot(xs, np.arange(1, len(xs) + 1) / len(xs), label=label,
            color=color, linewidth=1.2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default=None, help="dur,gap — single pair")
    args = ap.parse_args()

    wide = pl.read_csv(WIDE, separator="\t")
    pair_counts = (wide.group_by(["dur_family", "gap_family"]).len(name="n_users")
                       .sort("n_users", descending=True))
    total_users = int(pair_counts["n_users"].sum())
    keep = pair_counts.filter(pl.col("n_users") >= MIN_PAIR_SHARE * total_users)
    if args.pair:
        dur, gap = args.pair.split(",")
        keep = pair_counts.filter((pl.col("dur_family") == dur)
                                  & (pl.col("gap_family") == gap))
    print(f"trusted users: {total_users:,}  pairs >= {MIN_PAIR_SHARE:.0%}: {keep.height}",
          file=sys.stderr)

    pairs = wide.select("did", pl.concat_str(["dur_family", "gap_family"],
                                             separator="__").alias("pair"))
    gaps = (pl.scan_parquet(str(HERE / "data/chunk*.parquet"))
              .filter(pl.col("value") > 0)
              .select("did", "col", "value")
              .collect()
              .join(pairs, on="did"))
    print(f"gaps with a trusted pair: {gaps.height:,}", file=sys.stderr)

    rng = np.random.default_rng(42)
    for dur, gap_f, n_users in keep.iter_rows():
        sub = gaps.filter(pl.col("pair") == f"{dur}__{gap_f}")
        lines = []
        for col, label, color in (("interpost_within", "within-session", "#0072B2"),
                                  ("interpost_global", "global", "#D55E00")):
            v = sub.filter(pl.col("col") == col)["value"].to_numpy()
            if len(v) == 0:
                continue
            if len(v) > MAX_LINES:
                v = v[rng.choice(len(v), size=MAX_LINES, replace=False)]
            q = np.quantile(v, [0.25, 0.5, 0.75, 0.9, 0.99])
            print(f"{dur} x {gap_f} [{label}]: {len(v):,} gaps  "
                  f"p25 {q[0]:.1f}  p50 {q[1]:.1f}  p75 {q[2]:.1f}  "
                  f"p90 {q[3]:.1f}  p99 {q[4]:.1f}", file=sys.stderr)
            lines.append((v, label, color))

            if col == "interpost_within":
                out_txt = HERE / "results" / f"within_ecdf__{dur}__{gap_f}.txt"
                with open(out_txt, "w") as f:
                    f.writelines(f"{x:.6f}\n" for x in v)

        fig, ax = plt.subplots(figsize=(7, 4.5))
        for v, label, color in lines:
            ecdf(v, ax, label, color)
        ax.set_xscale("log")
        ax.set_xlabel("inter-post gap (s)")
        ax.set_ylabel("ECDF")
        ax.set_title(f"Inter-post gap ECDF — {DISPLAY[dur]} $\\times$ "
                     f"{DISPLAY[gap_f]} users")
        ax.legend()
        fig.tight_layout()
        p = HERE / "plots" / f"interpost_ecdf__{dur}__{gap_f}.png"
        fig.savefig(p, dpi=300)
        print(f"  → {p.name}", file=sys.stderr)


if __name__ == "__main__":
    main()
