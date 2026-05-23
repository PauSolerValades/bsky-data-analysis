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
§7 — Composite user scoring / ranking.

Combines multiple activity dimensions into a single engagement score:
  - Density:    events per active day (normalized)
  - Breadth:    number of distinct event types used (0–5: post, reply, repost, like, follow)
  - Consistency: active_days / max_active_days (normalized, cap at 8 days)
  - Span:       log of active_days (simple proxy for "real user")

All four are min-max normalized to [0,1] and averaged (equal weight).
The score distribution reveals natural cutoffs between tourists, casuals,
actives, and power users.
"""

import sys
from pathlib import Path

_EDA_DIR = Path(__file__).resolve().parent
if str(_EDA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_EDA_DIR.parent))

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from matplotlib.colors import LogNorm
from scipy.stats import gaussian_kde

from _common import load_or_fetch_stats, savefig, set_mpl_style

OUT_DIR = Path(__file__).resolve().parent / "results"


def compute_composite_score(df: pl.DataFrame) -> pl.DataFrame:
    """Compute per-user composite score."""
    max_active_days = 8  # known from the data window

    # Breadth: count of non-zero event types
    df = df.with_columns([
        pl.fold(
            acc=pl.lit(0),
            function=lambda acc, x: acc + (x > 0).cast(pl.Int64),
            exprs=[
                pl.col("n_posts"), pl.col("n_replies"), pl.col("n_reposts"),
                pl.col("n_likes"), pl.col("n_follows"),
            ],
        ).alias("breadth"),
    ])

    # Density: events per active day (cap at some reasonable max for normalization)
    epad = df["events_per_active_day"].fill_null(0).clip(0, 200)  # cap at 200/day

    # Consistency: active_days / 8
    consistency = df["active_days"].clip(1, 8) / max_active_days

    # Span: log(active_days + 1) / log(9)
    span = (df["active_days"] + 1).log10() / np.log10(9)

    # Normalize each component to [0, 1]
    epad_norm = (epad - epad.min()) / (epad.max() - epad.min()) if epad.max() > epad.min() else 0
    breadth_norm = df["breadth"] / 5.0
    consistency_norm = consistency
    span_norm = span

    score = (
        epad_norm.fill_null(0).to_numpy() * 0.30 +
        breadth_norm.fill_null(0).to_numpy() * 0.20 +
        consistency_norm.fill_null(0).to_numpy() * 0.25 +
        span_norm.fill_null(0).to_numpy() * 0.25
    )

    df = df.with_columns([
        pl.Series("breadth", df["breadth"].to_list()),
        pl.Series("score", score),
        pl.Series("density_norm", epad_norm.fill_null(0).to_numpy()),
        pl.Series("consistency_norm", consistency_norm.fill_null(0).to_numpy()),
    ])

    return df


def plot_score_distribution(df: pl.DataFrame):
    """Score histogram with KDE, and score vs components scatter."""
    set_mpl_style()
    scores = df["score"].to_numpy()
    mask = scores > 0  # exclude tourists with 0 events
    scores = scores[mask]

    fig, axes = plt.subplots(2, 2, figsize=(18, 13))

    # ---- Panel 1: Score histogram ----
    ax = axes[0, 0]
    ax.hist(scores, bins=80, color="#4A90D9", alpha=0.85, edgecolor="none")
    # KDE overlay
    if len(scores) > 10:
        kde = gaussian_kde(scores, bw_method=0.05)
        xs = np.linspace(0, 1, 200)
        kde_y = kde(xs)
        ax2_kde = ax.twinx()
        ax2_kde.plot(xs, kde_y, "r-", linewidth=2, alpha=0.7)
        ax2_kde.set_ylabel("KDE density", color="red")
        ax2_kde.tick_params(axis="y", labelcolor="red")

    # Annotate natural cutoffs (valleys in the distribution)
    ax.axvline(x=0.1, color="#AAAAAA", linestyle=":", alpha=0.5, label="~0.1 (very low)")
    ax.axvline(x=0.3, color="#E67E22", linestyle=":", alpha=0.5, label="~0.3 (casual)")
    ax.axvline(x=0.5, color="#27AE60", linestyle=":", alpha=0.5, label="~0.5 (active)")
    ax.set_xlabel("Composite engagement score")
    ax.set_ylabel("Number of users")
    ax.set_title("Composite engagement score distribution")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # ---- Panel 2: Score vs total events ----
    ax = axes[0, 1]
    te = df["total_events"].to_numpy().astype(np.float64)
    sc = df["score"].to_numpy()
    mask2 = (te > 0) & (sc > 0)
    ax.hexbin(te[mask2], sc[mask2], gridsize=60, cmap="YlOrRd", mincnt=1, norm=LogNorm())
    ax.set_xscale("log")
    ax.set_xlabel("Total events")
    ax.set_ylabel("Composite score")
    ax.set_title("Score vs total events")
    ax.grid(True, alpha=0.3)

    # ---- Panel 3: Score percentiles by event-count bucket ----
    ax = axes[1, 0]
    df = df.with_columns([
        pl.when(pl.col("total_events") <= 5).then(pl.lit("≤5"))
         .when(pl.col("total_events") <= 25).then(pl.lit("6–25"))
         .when(pl.col("total_events") <= 100).then(pl.lit("26–100"))
         .when(pl.col("total_events") <= 500).then(pl.lit("101–500"))
         .otherwise(pl.lit("500+")).alias("bucket"),
    ])
    bucket_order = ["≤5", "6–25", "26–100", "101–500", "500+"]
    box_data = []
    labels = []
    for b in bucket_order:
        b_scores = df.filter(pl.col("bucket") == b)["score"].drop_nulls().to_numpy()
        if len(b_scores) > 5:
            box_data.append(b_scores)
            labels.append(f"{b}\n(n={len(b_scores):,})")
    ax.boxplot(box_data, labels=labels, patch_artist=True,
               boxprops=dict(facecolor="#4A90D9", alpha=0.6),
               flierprops=dict(markersize=2))
    ax.set_xlabel("Event-count bucket")
    ax.set_ylabel("Composite score")
    ax.set_title("Score distribution by user class")
    ax.grid(True, alpha=0.3)

    # ---- Panel 4: Score component contributions (mean per score decile) ----
    ax = axes[1, 1]
    deciles = np.percentile(scores, np.arange(10, 101, 10))
    contributions = []
    for i in range(len(deciles)):
        lo = 0 if i == 0 else deciles[i-1]
        hi = deciles[i]
        mask_d = (scores >= lo) & (scores <= hi)
        if mask_d.sum() == 0:
            continue
        d_sub = df.filter(pl.col("score").is_between(lo, hi))
        contributions.append({
            "decile": i + 1,
            "density": d_sub["density_norm"].mean(),
            "breadth": (d_sub["breadth"] / 5.0).mean(),
            "consistency": d_sub["consistency_norm"].mean(),
            "span": np.log10(d_sub["active_days"].to_numpy() + 1).mean() / np.log10(9),
        })

    if contributions:
        cd = pl.DataFrame(contributions)
        dec_x = cd["decile"].to_numpy()
        ax.fill_between(dec_x, 0, cd["density"], label="Density", alpha=0.5, color="#4A90D9")
        ax.fill_between(dec_x, cd["density"], cd["density"] + cd["breadth"],
                        label="Breadth", alpha=0.5, color="#27AE60")
        ax.fill_between(dec_x, cd["density"] + cd["breadth"],
                        cd["density"] + cd["breadth"] + cd["consistency"],
                        label="Consistency", alpha=0.5, color="#E67E22")
        ax.fill_between(dec_x, cd["density"] + cd["breadth"] + cd["consistency"],
                        cd["density"] + cd["breadth"] + cd["consistency"] + cd["span"],
                        label="Span", alpha=0.5, color="#8E44AD")
        ax.set_xlabel("Score decile")
        ax.set_ylabel("Component contribution (stacked)")
        ax.set_title("Score component breakdown by decile")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    fig.tight_layout()
    savefig(fig, "07_composite_score.png")


def run(force_reload: bool = False) -> dict:
    """Run §7 and return results dict."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_or_fetch_stats(force=force_reload)

    df = compute_composite_score(df)
    plot_score_distribution(df)

    scores = df["score"].drop_nulls().to_numpy()
    scores_pos = scores[scores > 0]

    # Find natural cutoffs using percentiles
    pcts = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    lines = [
        "=== §7: Composite user scoring ===",
        f"Users scored: {len(scores_pos):,} (non-zero)",
        "",
        "Components (equal weight):",
        "  30% Density   — events per active day",
        "  20% Breadth   — distinct event types used",
        "  25% Consistency — active_days / 8",
        "  25% Span      — log(active_days)",
        "",
        "Score percentiles:",
    ]
    for p in pcts:
        lines.append(f"  P{p:>2d}: {float(np.percentile(scores_pos, p)):.4f}")

    lines.extend([
        "",
        "Proposed score tiers:",
        f"  Tourist:     score < {float(np.percentile(scores_pos, 25)):.3f}",
        f"  Casual:      {float(np.percentile(scores_pos, 25)):.3f} – {float(np.percentile(scores_pos, 50)):.3f}",
        f"  Active:      {float(np.percentile(scores_pos, 50)):.3f} – {float(np.percentile(scores_pos, 90)):.3f}",
        f"  Power user:  score ≥ {float(np.percentile(scores_pos, 90)):.3f}",
    ])

    out = "\n".join(lines)
    (OUT_DIR / "07_summary.txt").write_text(out)
    print(f"\n{out}", file=sys.stderr)

    return {
        "section": "§7 — Composite score",
        "median_score": float(np.percentile(scores_pos, 50)),
        "p90_score": float(np.percentile(scores_pos, 90)),
    }


if __name__ == "__main__":
    run()
