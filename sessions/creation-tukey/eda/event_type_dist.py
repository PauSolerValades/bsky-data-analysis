#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "polars",
#     "matplotlib",
#     "seaborn",
#     "numpy",
# ]
# ///
"""
§6 — Events-per-user by event type, separately.

Separate histograms for posts, replies, reposts, likes, and follows per user.
Reveals different distribution shapes: some events are much rarer than others,
and the creator/engager split is quantifiable.
"""

import sys
from pathlib import Path

_EDA_DIR = Path(__file__).resolve().parent
if str(_EDA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_EDA_DIR.parent))

import matplotlib.pyplot as plt
import numpy as np
import polars as pl

from _common import load_or_fetch_stats, log_spaced_bins, savefig, set_mpl_style

OUT_DIR = Path(__file__).resolve().parent / "results"

EVENT_TYPES = [
    ("n_posts",    "Posts authored",        "#4A90D9"),
    ("n_replies",  "Replies",               "#27AE60"),
    ("n_reposts",  "Reposts",               "#E67E22"),
    ("n_likes",    "Likes (from users table)", "#8E44AD"),
    ("n_follows",  "Follows (from users table)", "#D94A4A"),
]


def plot_per_type_histograms(df: pl.DataFrame):
    """Overlaid histograms of each event type."""
    set_mpl_style()

    fig, axes = plt.subplots(2, 3, figsize=(18, 11))
    axes_flat = axes.flatten()

    for i, (col, label, color) in enumerate(EVENT_TYPES):
        ax = axes_flat[i]
        data = df[col].to_numpy().astype(np.float64)
        nonzero = data[data > 0]
        if len(nonzero) == 0:
            ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center")
            ax.set_title(label)
            continue

        bins = log_spaced_bins(nonzero, 35)
        ax.hist(nonzero, bins=bins, color=color, alpha=0.85, edgecolor="none")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel(f"{label} per user")
        ax.set_ylabel("Number of users")
        ax.set_title(f"{label} (n={len(nonzero):,} non-zero)")
        ax.grid(True, alpha=0.3)

        # Show median
        med = np.median(nonzero)
        ax.axvline(x=med, color="black", linestyle=":", linewidth=1.5, alpha=0.7)
        ax.text(med * 1.1, ax.get_ylim()[1] * 0.85, f"Median={med:.0f}",
                fontsize=8, color="black")

    # ---- Extra panel: Overlaid CCDF for comparison ----
    ax = axes_flat[5]
    for col, label, color in EVENT_TYPES:
        data = df[col].to_numpy().astype(np.float64)
        nonzero = np.sort(data[data > 0])
        if len(nonzero) < 2:
            continue
        ccdf = 1 - np.arange(1, len(nonzero) + 1) / len(nonzero)
        ax.loglog(nonzero, ccdf, color=color, linewidth=1.5, alpha=0.8, label=label)
    ax.set_xlabel("Events per user")
    ax.set_ylabel("P(X > x)")
    ax.set_title("Complementary CDFs (all event types)")
    ax.legend(fontsize=7, loc="lower left")
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    savefig(fig, "06_event_type_distributions.png")


def plot_type_ratios_histogram(df: pl.DataFrame):
    """Histogram of key ratios: reposts/likes, posts/likes, replies/posts."""
    set_mpl_style()

    sub = df.filter(pl.col("total_events") > 5)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    configs = [
        ("n_reposts", "n_likes", "Reposts / Likes ratio", axes[0], "#E67E22"),
        ("n_posts", "n_likes", "Posts / Likes ratio", axes[1], "#4A90D9"),
        ("n_replies", "n_posts", "Replies / Posts ratio", axes[2], "#27AE60"),
    ]

    for num_col, den_col, title, ax, color in configs:
        num = sub[num_col].to_numpy().astype(np.float64)
        den = sub[den_col].to_numpy().astype(np.float64)
        mask = (num > 0) & (den > 0)
        ratios = num[mask] / den[mask]
        if len(ratios) == 0:
            ax.text(0.5, 0.5, "No data", transform=ax.transAxes)
            ax.set_title(title)
            continue
        # Clip extreme values
        ratios_clipped = ratios[ratios <= np.percentile(ratios, 99)]
        ax.hist(ratios_clipped, bins=60, color=color, alpha=0.85, edgecolor="none")
        ax.set_xlabel("Ratio")
        ax.set_ylabel("Number of users")
        ax.set_title(title)
        ax.axvline(x=1.0, color="black", linestyle=":", alpha=0.5, label="Parity")
        ax.axvline(x=np.median(ratios_clipped), color="#D94A4A", linestyle="--",
                   alpha=0.6, label=f"Median={np.median(ratios_clipped):.2f}")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    fig.tight_layout()
    savefig(fig, "06_type_ratios.png")


def run(force_reload: bool = False) -> dict:
    """Run §6 and return results dict."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_or_fetch_stats(force=force_reload)

    plot_per_type_histograms(df)
    plot_type_ratios_histogram(df)

    # Summary
    lines = ["=== §6: Events-per-user by event type ===", ""]
    for col, label, _ in EVENT_TYPES:
        data = df[col].to_numpy().astype(np.float64)
        nonzero = data[data > 0]
        nz = len(nonzero)
        if nz > 0:
            lines.append(
                f"  {label:<18}: {nz:>10,} non-zero users  "
                f"median={np.median(nonzero):.0f}  "
                f"mean={np.mean(nonzero):.1f}  "
                f"max={int(nonzero.max()):,}"
            )
        else:
            lines.append(f"  {label:<18}: 0 non-zero users")
    lines.append("")

    out = "\n".join(lines)
    (OUT_DIR / "06_summary.txt").write_text(out)
    print(f"\n{out}", file=sys.stderr)

    return {"section": "§6 — Event-type distributions"}


if __name__ == "__main__":
    run()
