#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "polars",
#     "matplotlib",
#     "seaborn",
#     "numpy",
#     "scipy",
# ]
# ///
"""
§2 — Event-type composition per user (user classification / archetypes).

Uses per-user counts of posts, replies, reposts, likes, and follows to
identify behavioural archetypes: creators, engagers, curators, passive, etc.
"""

import sys
from pathlib import Path

_EDA_DIR = Path(__file__).resolve().parent
if str(_EDA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_EDA_DIR.parent))

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns
from matplotlib.colors import LogNorm

from eda._common import load_or_fetch_stats, savefig, set_mpl_style

OUT_DIR = Path(__file__).resolve().parent / "results"


def classify_archetype(
    n_posts: int,
    n_replies: int,
    n_reposts: int,
    n_likes: int,
    n_follows: int,
    total_events: int,
) -> str:
    """Assign a user archetype based on event-type composition."""
    authored = n_posts + n_replies   # content creation
    engaged = n_likes + n_reposts    # interaction with others
    total = authored + engaged

    # Network only: follows but nothing else in core events
    if total == 0 and n_follows > 0:
        return "Networker"
    if total == 0:
        return "Ghost"  # should not happen in core_events table but defensive

    post_ratio = authored / total if total > 0 else 0
    repost_ratio = n_reposts / engaged if engaged > 0 else 0

    # Low total = tourist
    if total_events <= 5:
        return "Tourist"

    # High creation ratio = Creator
    if post_ratio >= 0.7:
        if n_reposts / max(total, 1) >= 0.4:
            return "Creator-Curator"
        return "Creator"

    # High engagement ratio
    if post_ratio <= 0.3:
        if repost_ratio >= 0.4:
            return "Curator"
        return "Engager"

    # Balanced
    if 0.3 < post_ratio < 0.7:
        if repost_ratio >= 0.4:
            return "Balanced-Curator"
        return "Balanced"

    return "Other"


def plot_ratio_scatters(df: pl.DataFrame):
    """2D hexbin plots: likes vs posts, reposts vs posts, likes vs reposts."""
    set_mpl_style()
    # Filter to users with at least 1 event (avoid log(0))
    sub = df.filter(pl.col("total_events") > 0)

    fig, axes = plt.subplots(2, 2, figsize=(16, 14))

    pairs = [
        (sub["n_likes"], sub["n_posts"] + sub["n_replies"],
         "Likes", "Posts+Replies (authored)", axes[0, 0]),
        (sub["n_reposts"], sub["n_posts"] + sub["n_replies"],
         "Reposts", "Posts+Replies (authored)", axes[0, 1]),
        (sub["n_likes"], sub["n_reposts"],
         "Likes", "Reposts", axes[1, 0]),
    ]

    for xcol, ycol, xlbl, ylbl, ax in pairs:
        x = xcol.to_numpy().astype(np.float64)
        y = ycol.to_numpy().astype(np.float64)
        mask = (x > 0) & (y > 0)
        x = x[mask]
        y = y[mask]
        if len(x) == 0:
            ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center")
            ax.set_xlabel(xlbl); ax.set_ylabel(ylbl); continue

        hb = ax.hexbin(x, y, gridsize=80, cmap="YlOrRd",
                       mincnt=1, norm=LogNorm())
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel(xlbl)
        ax.set_ylabel(ylbl)
        ax.set_title(f"{ylbl} vs {xlbl}")
        ax.grid(True, alpha=0.3)
        # Diagonal line
        xmax = max(x.max(), y.max())
        ax.plot([1, xmax], [1, xmax], "k--", linewidth=0.8, alpha=0.3)
        plt.colorbar(hb, ax=ax, label="Users (log)")

    # Panel 4: likes ratio histogram
    ax = axes[1, 1]
    authored = sub["n_posts"].to_numpy() + sub["n_replies"].to_numpy()
    engaged = sub["n_likes"].to_numpy() + sub["n_reposts"].to_numpy()
    total = authored + engaged
    mask = total > 0
    post_ratio = np.where(mask, authored[mask] / total[mask], 0)
    ax.hist(post_ratio, bins=50, color="#4A90D9", alpha=0.85, edgecolor="none")
    ax.set_xlabel("Posts+Replies / (Posts+Replies+Likes+Reposts)")
    ax.set_ylabel("Number of users")
    ax.set_title("Authored-content ratio per user")
    # 0 = pure engager, 1 = pure creator
    ax.axvline(x=0.3, color="#E67E22", linestyle="--", alpha=0.6, label="Engager < 0.3")
    ax.axvline(x=0.7, color="#8E44AD", linestyle="--", alpha=0.6, label="Creator > 0.7")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    savefig(fig, "02_ratio_scatters.png")


def plot_archetype_distribution(df: pl.DataFrame):
    """Bar chart of user archetypes, split by event-count bucket."""
    set_mpl_style()
    df = df.with_columns([
        pl.struct(
            ["n_posts", "n_replies", "n_reposts", "n_likes", "n_follows", "total_events"]
        ).map_elements(
            lambda r: classify_archetype(
                r["n_posts"], r["n_replies"], r["n_reposts"],
                r["n_likes"], r["n_follows"], r["total_events"],
            ),
            return_dtype=pl.Utf8,
        ).alias("archetype"),
        pl.when(pl.col("total_events") <= 5).then(pl.lit("≤5"))
         .when(pl.col("total_events") <= 25).then(pl.lit("6–25"))
         .when(pl.col("total_events") <= 100).then(pl.lit("26–100"))
         .when(pl.col("total_events") <= 500).then(pl.lit("101–500"))
         .otherwise(pl.lit("500+")).alias("event_bucket"),
    ])

    counts = df.group_by(["event_bucket", "archetype"]).agg(pl.len().alias("n"))

    # Order buckets
    bucket_order = ["≤5", "6–25", "26–100", "101–500", "500+"]
    archetypes = counts["archetype"].unique().to_list()

    # Pivot
    pivot = {}
    for arch in archetypes:
        pivot[arch] = [0] * len(bucket_order)
    for row in counts.iter_rows(named=True):
        bi = bucket_order.index(row["event_bucket"])
        pivot[row["archetype"]][bi] = row["n"]

    fig, ax = plt.subplots(figsize=(14, 7))
    colors = ["#AAAAAA", "#4A90D9", "#27AE60", "#E67E22", "#8E44AD",
              "#D94A4A", "#2ECC71", "#F39C12", "#3498DB"]
    bottom = np.zeros(len(bucket_order))
    for i, arch in enumerate(pivot):
        vals = np.array(pivot[arch])
        ax.bar(range(len(bucket_order)), vals, bottom=bottom,
               label=arch, color=colors[i % len(colors)], alpha=0.85)
        bottom += vals

    ax.set_xticks(range(len(bucket_order)))
    ax.set_xticklabels(bucket_order)
    ax.set_xlabel("Events per user (8-day window)")
    ax.set_ylabel("Number of users")
    ax.set_title("User archetypes by event-count bucket")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(True, axis="y", alpha=0.3)

    fig.tight_layout()
    savefig(fig, "02_archetype_distribution.png")


def run(force_reload: bool = False) -> dict:
    """Run §2 and return results dict."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_or_fetch_stats(force=force_reload)

    plot_ratio_scatters(df)
    plot_archetype_distribution(df)

    # Summary stats
    df = df.with_columns([
        pl.struct(
            ["n_posts", "n_replies", "n_reposts", "n_likes", "n_follows", "total_events"]
        ).map_elements(
            lambda r: classify_archetype(
                r["n_posts"], r["n_replies"], r["n_reposts"],
                r["n_likes"], r["n_follows"], r["total_events"],
            ),
            return_dtype=pl.Utf8,
        ).alias("archetype"),
    ])

    arc_counts = df.group_by("archetype").agg(pl.len().alias("n")).sort("n", descending=True)
    lines = ["=== §2: User classification / archetypes ==="]
    for row in arc_counts.iter_rows(named=True):
        pct = 100 * row["n"] / len(df)
        lines.append(f"  {row['archetype']:<20}: {row['n']:>10,}  ({pct:.1f}%)")

    out = "\n".join(lines)
    (OUT_DIR / "02_summary.txt").write_text(out)
    print(file=sys.stderr)

    return {
        "section": "§2 — User classification",
        "archetypes": {r["archetype"]: r["n"] for r in arc_counts.iter_rows(named=True)},
    }


if __name__ == "__main__":
    run()
