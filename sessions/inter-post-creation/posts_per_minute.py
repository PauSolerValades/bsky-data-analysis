"""Posts per session-minute: the length-normalized posting rate.

Normalizes post counts by session duration (count / duration_min), so sessions
of different lengths are comparable. Restricted to active sessions (≥ 1 post) —
the 66% of sessions with zero posts are already quantified in
posts_per_session.py. This is a homogeneous per-session characterization:
every session contributes one rate, regardless of user.

Reads the shared cache (results/posts_per_session_cache.npz) built by
posts_per_session.py.

Output: plots/posts_per_minute.png

Usage:
    uv run inter-post-creation/posts_per_minute.py
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

HERE = Path(__file__).resolve().parent
from posts_per_session import fetch  # shared cache/DB layer

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


def main():
    counts, durations = fetch()
    active = counts > 0
    rate = counts[active] * 60.0 / durations[active]  # posts per minute
    print(f"active sessions (≥1 post): {active.sum():,} ({100 * active.mean():.1f}%)  "
          f"posts/min: median {np.median(rate):.2f}  mean {rate.mean():.2f}  "
          f"p90 {np.percentile(rate, 90):.2f}  p99 {np.percentile(rate, 99):.2f}",
          file=sys.stderr)

    cap = float(np.percentile(rate, 99))
    fig, ax = plt.subplots(figsize=(7, 4.5))
    sns.histplot(rate[rate <= cap], bins=60, stat="percent",
                 color=sns.color_palette("colorblind")[1], ax=ax)
    ax.set_xlim(0, cap)
    ax.set_xlabel("posts per session-minute (sessions with ≥ 1 post)")
    ax.set_ylabel("share of active sessions (%)")
    ax.set_title("Post intensity among active sessions")
    fig.tight_layout()
    p = HERE / "plots" / "posts_per_minute.png"
    fig.savefig(p, dpi=300)
    print(f"→ {p}", file=sys.stderr)


if __name__ == "__main__":
    main()
