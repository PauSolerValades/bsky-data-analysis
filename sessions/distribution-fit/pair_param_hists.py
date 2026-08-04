"""Per-pair histograms of fitted params: one PNG per (dur_dist, gap_dist) pair,
up to 4 subplots (dur params + gap params). Top 15 pairs by user count.

Checks what the median [Q1-Q3] table can't: bimodality / degenerate spreads
in the across-user distribution of fitted params. Log-x when values are
all-positive and span > 50x.

Usage:
    uv run distribution-fit/pair_param_hists.py
"""

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

HERE = Path(__file__).resolve().parent
WIDE = HERE / "results/pair_params_wide.tsv"
OUT = HERE / "plots/pair_params"
TOP = 15

PARAMS = ["shape", "scale", "rate", "meanlog", "sdlog"]

vals = defaultdict(list)
n_users = defaultdict(int)
with open(WIDE, newline="") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        pair = (row["dur_dist"], row["gap_dist"])
        n_users[pair] += 1
        for side in ("dur", "gap"):
            for p in PARAMS:
                v = row.get(f"{side}_{p}")
                if v:
                    vals[(pair, side, p)].append(float(v))

OUT.mkdir(parents=True, exist_ok=True)
for pair in sorted(n_users, key=n_users.get, reverse=True)[:TOP]:
    panels = [(side, p, np.array(vals[(pair, side, p)]))
              for side in ("dur", "gap") for p in PARAMS
              if vals[(pair, side, p)]]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    for ax, (side, p, v) in zip(axes.flat, panels):
        bins = 50
        if v.min() > 0 and v.max() / v.min() > 50:
            ax.set_xscale("log")
            bins = np.logspace(np.log10(v.min()), np.log10(v.max()), 50)
        ax.hist(v, bins=bins, color="steelblue", edgecolor="white", linewidth=0.3)
        ax.set_title(f"{side} {p} ({pair[0] if side == 'dur' else pair[1]})",
                     fontsize=11)
    for ax in list(axes.flat)[len(panels):]:
        ax.axis("off")
    fig.suptitle(f"{pair[0]} -> {pair[1]}   (n={n_users[pair]:,})")
    fig.tight_layout()
    fig.savefig(OUT / f"{pair[0]}__{pair[1]}.png", dpi=150)
    plt.close(fig)
    print(f"→ {OUT / f'{pair[0]}__{pair[1]}.png'}")
