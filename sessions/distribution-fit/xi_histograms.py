"""Histograms of the canonical GPD shape parameter xi, one per column.

Reads the duration_xi / gap_xi columns of params/params__*.tsv and plots the
distribution of xi for session durations and inter-session gaps, shading the
three EVT tail regimes (bounded | exponential | heavy) and marking the
+-eps boundaries. Axes clipped to the 1-99% central mass.

Output: plots/xi_tails/xi_hist_duration.png, plots/xi_tails/xi_hist_gap.png

Usage:
    uv run distribution-fit/xi_histograms.py [--eps 0.1]
"""

import argparse
import glob
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

# 1. Set the whitegrid style FIRST
sns.set_theme(style="whitegrid")

# 2. Force Matplotlib to use LaTeX for all text rendering
plt.rcParams.update({
    "text.usetex": False,  # no latex binary on this machine (matches other analysis plots)
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

HERE = Path(__file__).resolve().parent
PARAMS_DIR = HERE.parent / "params"
OUT_DIR = HERE / "plots" / "xi_tails"

CB_BOUNDED = "#0072B2"   # Okabe-Ito blue
CB_HEAVY = "#D55E00"     # Okabe-Ito vermillion
CB_EXP = "#E69F00"       # Okabe-Ito orange
CB_BAR = "#56B4E9"       # Okabe-Ito sky blue


def load_xi(side: str) -> np.ndarray:
    vals = []
    for f in sorted(glob.glob(str(PARAMS_DIR / "params__*.tsv"))):
        col = f"{side}_xi"
        with open(f) as fh:
            header = fh.readline().rstrip("\n").split("\t")
        if col not in header:
            continue
        import polars as pl
        df = pl.read_csv(f, separator="\t")
        vals += df[col].drop_nulls().to_list()
    return np.array(vals)


def plot_xi(side: str, eps: float):
    xi = load_xi(side)
    lo, hi = np.percentile(xi, 1), np.percentile(xi, 99)
    clip = xi[(xi >= lo) & (xi <= hi)]

    fig, ax = plt.subplots(figsize=(6.5, 4))
    # regime shading (full axis span so the colors read behind the bars)
    ax.axvspan(-clip.max(), -eps, color=CB_BOUNDED, alpha=0.12)
    ax.axvspan(-eps, eps, color=CB_EXP, alpha=0.18)
    ax.axvspan(eps, clip.max(), color=CB_HEAVY, alpha=0.12)
    for x, c in ((-eps, CB_BOUNDED), (eps, CB_HEAVY)):
        ax.axvline(x, color=c, linestyle="--", linewidth=1)

    ax.hist(clip, bins=60, color=CB_BAR, edgecolor="white", linewidth=0.3)
    ax.set_xlabel(r"GPD shape parameter $\xi$ — " + f"{'session durations' if side == 'duration' else 'inter-session gaps'}")
    ax.set_ylabel("users")
    ax.set_xlim(lo, hi)
    ax.set_title(f"{'Sessions' if side == 'duration' else 'Gaps'}: GPD shape parameter")

    # legend of regimes (dummy handles)
    from matplotlib.patches import Patch
    handles = [Patch(color=CB_BOUNDED, alpha=0.4, label=rf"bounded ($\xi<{-eps:g}$)"),
               Patch(color=CB_EXP, alpha=0.4, label=rf"exponential ($|\xi|\leq{eps:g}$)"),
               Patch(color=CB_HEAVY, alpha=0.4, label=rf"heavy ($\xi>{eps:g}$)")]
    ax.legend(handles=handles, frameon=False, loc="upper right")

    out = OUT_DIR / f"xi_hist_{side}.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"→ {out}  (n={len(xi):,}, clip [{lo:.2f}, {hi:.2f}])")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eps", type=float, default=0.1)
    args = ap.parse_args()
    OUT_DIR.mkdir(exist_ok=True)
    plot_xi("duration", args.eps)
    plot_xi("gap", args.eps)


if __name__ == "__main__":
    main()
