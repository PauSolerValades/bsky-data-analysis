"""Per-pair histograms of fitted params: one 2-row PNG per (dur_family, gap_family) pair.

Level 1 (top) = sessions (blue, dur params), level 2 (bottom) = gaps (orange,
gap params). Panels are placed at explicit figure-fraction coordinates so each
row's params form a centred block: a 1-parameter distribution shows a single
panel dead-centre of its row, a 2-parameter one shows two panels spread
symmetrically about the centre. No 3-col gridspec, no empty middle slot.

Power_tail sides (pareto/lomax/genpareto are reparametrizations of one another)
are shown on a common canonical GPD axis (xi, sigma) from
power_tail_canonical.tsv, so all power_tail users plot the same semantics.

The 24 relevant family pairs = top 24 by user count, matching pair_family_bars.py
(drops the bottom 8 pairs, 17 users, <0.01%). Titles state the pair explicitly;
filenames are pair + side. Log-x when values are all-positive and span > 50x.

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
    "text.usetex": True,
    "axes.labelsize": 11,
    "font.size": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

HERE = Path(__file__).resolve().parent
WIDE = HERE / "results/pair_params_wide.tsv"
CANON = HERE / "results/power_tail_canonical.tsv"
OUT = HERE / "plots/pair_params"
TOP = 24  # drop the bottom 8 tail pairs, matching pair_family_bars.py

PARAMS = ["shape", "scale", "rate", "meanlog", "sdlog"]
# LaTeX symbol per fitted parameter (drawn as $…$ so usetex renders it)
SYMBOLS = {"xi": "\\xi", "sigma": "\\sigma",
           "shape": "k", "scale": "\\lambda", "rate": "\\mu",
           "meanlog": "\\mu", "sdlog": "\\sigma"}
COLORS = {"dur": "steelblue", "gap": "darkorange"}
LABELS = {"dur": "sessions", "gap": "gaps"}
SIDE_COL = {"dur": "duration", "gap": "gap"}
POWER_TAIL = "power_tail"

# (did, col) -> (xi, sigma) canonical GPD, from step3
canon = {}
with open(CANON, newline="") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        canon[(row["did"], row["col"])] = (float(row["xi"]), float(row["sigma"]))

# (family_pair, side, param) -> values; param in {"xi","sigma"} for power_tail
vals = defaultdict(list)
n_users = defaultdict(int)
with open(WIDE, newline="") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        pair = (row["dur_family"], row["gap_family"])
        n_users[pair] += 1
        for side in ("dur", "gap"):
            family = pair[0] if side == "dur" else pair[1]
            if family == POWER_TAIL:
                xi, sig = canon.get((row["did"], SIDE_COL[side]), (None, None))
                if xi is not None:
                    vals[(pair, side, "xi")].append(xi)
                    vals[(pair, side, "sigma")].append(sig)
            else:
                for p in PARAMS:
                    v = row.get(f"{side}_{p}")
                    if v:
                        vals[(pair, side, p)].append(float(v))


def hist_bins(v):
    if v.min() > 0 and v.max() / v.min() > 50:
        return "log", np.logspace(np.log10(v.min()), np.log10(v.max()), 50)
    return None, 50


# Explicit axes placement (figure fractions). Each row's panels form a block
# centred on the row: 1 chip at x=0.5, or 2 chips symmetric about 0.5.
FIG_W, FIG_H = 11, 7
CHIP_W, CHIP_H = 0.40, 0.34
MGAP = 0.04
ROW_Y = {"dur": 0.58, "gap": 0.12}   # bottom edge of each row's chips


def chip_xs(n):
    total = n * CHIP_W + (n - 1) * MGAP   # block width, centred at 0.5
    start = 0.5 - total / 2
    return [start + i * (CHIP_W + MGAP) for i in range(n)]


OUT.mkdir(parents=True, exist_ok=True)
for pair in sorted(n_users, key=n_users.get, reverse=True)[:TOP]:
    fig = plt.figure(figsize=(FIG_W, FIG_H))
    for side in ("dur", "gap"):
        panels = [(p, np.array(vals[(pair, side, p)]))
                  for p in ("xi", "sigma", *PARAMS) if vals[(pair, side, p)]]
        for x, (p, v) in zip(chip_xs(len(panels)), panels):
            ax = fig.add_axes([x, ROW_Y[side], CHIP_W, CHIP_H])
            xscale, bins = hist_bins(v)
            if xscale:
                ax.set_xscale(xscale)
            ax.hist(v, bins=bins, color=COLORS[side], edgecolor="white",
                    linewidth=0.3, label=LABELS[side])
            ax.set_title(f"{LABELS[side].capitalize()} — ${SYMBOLS[p]}$", fontsize=11)
    fig.suptitle(f"({pair[0]}, {pair[1]})   (n={n_users[pair]:,} users)")
    fname = OUT / f"{pair[0]}__{pair[1]}.png"
    fig.savefig(fname, dpi=150)
    plt.close(fig)
    print(f"→ {fname}")
