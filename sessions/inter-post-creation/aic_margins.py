"""ΔAIC margin per unit: gap between AIC-best and runner-up, within vs global.

Reads results/gof__chunk*.tsv, computes margin = second-best AIC − best AIC
per (did, col), prints summary stats and plots the within-vs-global margin
distributions (histogram).

Usage:
    uv run inter-post-creation/aic_margins.py
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns

HERE = Path(__file__).resolve().parent
R = HERE / "results"
OUT = HERE / "plots"

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

MIN_OBS = 30
COLS = [("interpost_within", "within-session gaps"),
        ("interpost_global", "global gaps")]


def main():
    gof = pl.concat([pl.read_csv(p, separator="\t",
                                 schema_overrides={"did": pl.String})
                     for p in sorted(R.glob("gof__chunk*.tsv"))])
    print(f"gof rows: {gof.height:,}", file=sys.stderr)

    # margin = ΔAIC from best to runner-up, per (did, col)
    m = (gof.filter(pl.col("n_obs") >= MIN_OBS)
            .sort(["did", "col", "aic"])
            .group_by(["did", "col"], maintain_order=True)
            .agg(pl.col("aic").head(2))
            .with_columns((pl.col("aic").list.get(1) - pl.col("aic").list.get(0))
                          .alias("margin"))
            .drop_nulls()
            .select(["did", "col", "margin"]))
    print(f"units (n_obs>={MIN_OBS}, ≥2 fits): {m.height:,}", file=sys.stderr)

    margins = {}
    for col, _ in COLS:
        s = m.filter(pl.col("col") == col)["margin"].to_numpy()
        margins[col] = s
        print(f"{col}: n={len(s):,} median={np.median(s):.2f} "
              f"mean={s.mean():.2f} p90={np.percentile(s, 90):.2f} "
              f"p99={np.percentile(s, 99):.2f} "
              f"margin<2: {100 * np.mean(s < 2):.1f}%  "
              f"margin<1: {100 * np.mean(s < 1):.1f}%", file=sys.stderr)

    # One histogram per column, percent-normalised, x capped at CAP
    # (fixed cap, not p99: the tail stretches the axis and hides the mass)
    CAP = 20
    OUT.mkdir(exist_ok=True)
    for col, label in COLS:
        s = margins[col]
        fig, ax = plt.subplots(figsize=(7, 4.5))
        sns.histplot(s[s <= CAP], bins=80, stat="percent",
                     color=sns.color_palette("colorblind")[0 if col.endswith("within") else 1],
                     ax=ax)
        ax.set_xlim(0, CAP)
        ax.set_xlabel("$\\Delta$AIC margin to runner-up (best $-$ 2nd best)")
        ax.set_ylabel("share of users (%)")
        ax.set_title(f"AIC margin between best and runner-up fit — {label}")
        fig.tight_layout()
        p = OUT / f"aic_margins_{'within' if col.endswith('within') else 'global'}.png"
        fig.savefig(p, dpi=300)
        print(f"→ {p}", file=sys.stderr)


if __name__ == "__main__":
    main()
