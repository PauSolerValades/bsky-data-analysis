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
§5 — Coverage analysis: who contributes the gaps?

For each event-count bucket, shows what percentage of users, total events,
total gaps, and session-candidate gaps (>120s) they represent.
Quantifies whose activity the session analysis is actually about.
"""

import sys
from pathlib import Path

_EDA_DIR = Path(__file__).resolve().parent
if str(_EDA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_EDA_DIR.parent))

import matplotlib.pyplot as plt
import numpy as np
import polars as pl

from eda._common import load_or_fetch_stats, log_spaced_bins, savefig, set_mpl_style

OUT_DIR = Path(__file__).resolve().parent / "results"

# Gap buckets (in events, from §1 power-law analysis)
# We'll use data-driven bins plus the validated 6-event tourist cutoff


def compute_coverage(df: pl.DataFrame) -> pl.DataFrame:
    """Compute per-bucket coverage stats."""
    df = df.with_columns([
        pl.when(pl.col("total_events") == 1).then(pl.lit("1"))
         .when(pl.col("total_events") <= 5).then(pl.lit("2–5"))
         .when(pl.col("total_events") <= 25).then(pl.lit("6–25"))
         .when(pl.col("total_events") <= 100).then(pl.lit("26–100"))
         .when(pl.col("total_events") <= 500).then(pl.lit("101–500"))
         .otherwise(pl.lit("501+")).alias("bucket"),
    ])

    total_users = len(df)
    total_events = df["total_events"].sum()
    total_gaps = total_events - total_users  # each user has n_events - 1 gaps

    coverage = (
        df.group_by("bucket")
        .agg([
            pl.len().alias("n_users"),
            pl.sum("total_events").alias("n_events"),
            (pl.sum("total_events") - pl.len()).alias("n_gaps"),
        ])
        .sort("bucket")
        .with_columns([
            (pl.col("n_users") / total_users * 100).alias("pct_users"),
            (pl.col("n_events") / total_events * 100).alias("pct_events"),
            (pl.col("n_gaps") / total_gaps * 100).alias("pct_gaps"),
        ])
    )
    return coverage


def plot_coverage(coverage: pl.DataFrame):
    """Grouped bar chart: %users vs %events vs %gaps per bucket."""
    set_mpl_style()
    buckets = coverage["bucket"].to_list()
    pct_users = coverage["pct_users"].to_numpy()
    pct_events = coverage["pct_events"].to_numpy()
    pct_gaps = coverage["pct_gaps"].to_numpy()

    fig, ax = plt.subplots(figsize=(14, 7))

    x = np.arange(len(buckets))
    width = 0.25
    bars1 = ax.bar(x - width, pct_users, width, color="#4A90D9", alpha=0.85, label="% of users")
    bars2 = ax.bar(x, pct_events, width, color="#27AE60", alpha=0.85, label="% of events")
    bars3 = ax.bar(x + width, pct_gaps, width, color="#E67E22", alpha=0.85, label="% of gaps")

    # Annotate bars
    for bars in [bars1, bars2, bars3]:
        for bar in bars:
            h = bar.get_height()
            if h >= 1:
                ax.text(bar.get_x() + bar.get_width() / 2, h + 0.5, f"{h:.1f}%",
                        ha="center", va="bottom", fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(buckets)
    ax.set_xlabel("Events per user (8-day window)")
    ax.set_ylabel("Percentage of total")
    ax.set_title("Coverage: who contributes the gaps?")
    ax.legend(fontsize=11)
    ax.grid(True, axis="y", alpha=0.3)

    # Annotate the "tourist" area
    ax.axvline(x=1.5, color="#AAAAAA", linestyle=":", linewidth=1.5, alpha=0.7)
    ax.text(2, max(max(pct_users), max(pct_events), max(pct_gaps)) * 0.85,
            "Tourists\n(hardly any gaps)", ha="center", fontsize=10, color="#666666")

    fig.tight_layout()
    savefig(fig, "05_coverage.png")


def run(force_reload: bool = False) -> dict:
    """Run §5 and return results dict."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_or_fetch_stats(force=force_reload)
    coverage = compute_coverage(df)
    plot_coverage(coverage)

    # Text summary
    lines = ["=== §5: Coverage analysis — who contributes the gaps? ===", ""]
    lines.append(f"{'Bucket':<15} {'Users%':>8} {'Events%':>8} {'Gaps%':>8}  Interpretation")
    lines.append("-" * 65)

    total_gaps = coverage["n_gaps"].sum()

    for row in coverage.iter_rows(named=True):
        b = row["bucket"]
        pu, pe, pg = row["pct_users"], row["pct_events"], row["pct_gaps"]
        ng = row["n_gaps"]

        # Interpretation
        if pg < 3:
            interp = "irrelevant for session analysis"
        elif pu > 30:
            interp = "numerous but low-gap (tourists)"
        elif pg > 30:
            interp = "DOMINANT — drives the elbow & session patterns"
        elif 5 < pg <= 30:
            interp = "meaningful contribution"
        else:
            interp = "minor"

        lines.append(f"  {b:<13} {pu:>7.1f}% {pe:>7.1f}% {pg:>7.1f}%  ← {interp}")

    lines.extend([
        "",
        f"Total gaps: {total_gaps:,}",
        "",
        "Note: if >60% of gaps come from a single bucket, the session threshold",
        "is effectively that bucket's threshold. Filter or stratify accordingly.",
    ])

    out = "\n".join(lines)
    (OUT_DIR / "05_summary.txt").write_text(out)
    print(f"\n{out}", file=sys.stderr)

    return {
        "section": "§5 — Coverage",
        "coverage_table": {
            row["bucket"]: {
                "pct_users": row["pct_users"],
                "pct_events": row["pct_events"],
                "pct_gaps": row["pct_gaps"],
            }
            for row in coverage.iter_rows(named=True)
        },
    }


if __name__ == "__main__":
    run()
