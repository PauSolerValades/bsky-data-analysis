"""Sensitivity of the user policy pi to the assumed dwell time s (s/post).

pi_like(s) = s * L / T (linear), from sessions/likes-reposts results,
excluding zero-duration sessions.

Aggregates below are from likes_reposts_per_session.tsv.gz
(awk: NR>1 && $3>$2): L=148,484,100 likes, R=24,427,796 reposts,
T=6,114,322,530 s of session time.

Usage: uv run likes-reposts/plot_pi_sensitivity.py
"""

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
    "ytick.labelsize": 10
})

OUT = Path(__file__).resolve().parent / "results" / "pi_sensitivity.png"

LIKES, REPOSTS, TOTAL_S = 148_484_100, 24_427_796, 6_114_322_530
CHOSEN_S = 3

s = np.linspace(1, 4, 200)
pi_like = s * LIKES / TOTAL_S
pi_repost = s * REPOSTS / TOTAL_S
pi_ignore = 1 - pi_like - pi_repost

fig, ax = plt.subplots(figsize=(6, 4))
for vals, label in zip([pi_ignore, pi_like, pi_repost],
                       [r"$\pi_{\mathrm{ignore}}$", r"$\pi_{\mathrm{like}}$", r"$\pi_{\mathrm{repost}}$"]):
    ax.plot(s, vals, label=label, linewidth=1.8)

ax.axvline(CHOSEN_S, color="gray", linestyle="--", linewidth=1)
ax.annotate(f"chosen: $s={CHOSEN_S}$ s/post", xy=(CHOSEN_S, 0.02), xytext=(CHOSEN_S - 0.03, 0.35),
            rotation=90, va="center", ha="right", color="gray")

ax.set_xlabel(r"Assumed dwell time $s$ (seconds per post)")
ax.set_ylabel(r"Policy probability $\pi$")
ax.set_xlim(1, 4)
ax.set_ylim(0, 1)
ax.legend(frameon=False)

OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, dpi=300, bbox_inches="tight")
print(f"Saved {OUT}")
