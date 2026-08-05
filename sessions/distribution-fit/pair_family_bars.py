"""Barplot of the most common (duration, gap) family pairs.

Reads distribution-fit/results/pair_params_wide.tsv and plots the top
pairs by share of users, complementing the pairwise contingency table
in the thesis (@tbl-cal-pair-dist).

Usage:
    uv run distribution-fit/pair_family_bars.py
"""

import csv
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns

# ── Thesis styling (AGENTS.md) ────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

HERE = Path(__file__).resolve().parent
TSV = HERE / "results" / "pair_params_wide.tsv"
OUT = HERE / "results" / "pair_family_bars.png"

FAMILIES = {
    "power_tail": "Power-law",
    "weibull_min": "Weibull",
    "lognorm": "Lognorm",
    "gamma": "Gamma",
    "expon": "Exp",
    "fisk": "Fisk",
}


def main():
    pairs = Counter()
    with open(TSV, newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            pairs[(row["dur_family"], row["gap_family"])] += 1

    total = sum(pairs.values())
    # top 24; the remaining 8 pairs (17 users, <0.01%) are not plotted
    top = pairs.most_common(len(pairs) - 8)
    names = [f"{FAMILIES[d]} \u2192 {FAMILIES[g]}" for (d, g), _ in top]
    pcts = [100 * c / total for (_, _), c in top]

    fig, ax = plt.subplots(figsize=(11, 3.8))
    palette = sns.color_palette("colorblind", n_colors=len(top))
    bars = ax.bar(range(len(names)), pcts, color=palette, width=0.95)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=60, ha="right", fontsize=11)
    ax.set_ylabel("share of users (%)")
    for i, p in enumerate(pcts):
        ax.text(i, p + 0.1, f"{p:.1f}", ha="center", va="bottom", fontsize=11)
    ax.set_ylim(0, max(pcts) * 1.10)

    fig.tight_layout(pad=0.4)
    fig.savefig(OUT, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
