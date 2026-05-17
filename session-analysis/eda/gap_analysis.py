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
§4 — Per-user gap distribution analysis (before session clustering).

Understands raw inter-arrival gaps per user before imposing any session threshold:
- Histograms of per-user median gaps, IQR, skewness
- Per-user gap CDFs stratified by event-count buckets
- Scatter: per-user median gap vs total events

This tells us whether a single global threshold works or per-class thresholds are needed.

Heavy section — samples 100K users by default to keep it tractable.
Uses the parquet cache from §1 for event-count bucketing, then fetches raw events
only for the sampled users.
"""

import sys
from pathlib import Path

_EDA_DIR = Path(__file__).resolve().parent
if str(_EDA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_EDA_DIR.parent))

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import pymysql
import seaborn as sns
from matplotlib.colors import LogNorm

from eda._common import (
    load_or_fetch_stats,
    savefig,
    set_mpl_style,
    get_connection,
)

OUT_DIR = Path(__file__).resolve().parent / "results"

DEFAULT_SAMPLE = 50_000
FETCH_BATCH = 2_000


def sample_dids_by_strata(df: pl.DataFrame, n_total: int) -> pl.DataFrame:
    """Sample DIDs stratified by event-count bucket (preserves distribution shape)."""
    df = df.with_columns([
        pl.when(pl.col("total_events") <= 5).then(pl.lit("≤5"))
         .when(pl.col("total_events") <= 25).then(pl.lit("6–25"))
         .when(pl.col("total_events") <= 100).then(pl.lit("26–100"))
         .when(pl.col("total_events") <= 500).then(pl.lit("101–500"))
         .otherwise(pl.lit("500+")).alias("bucket"),
    ])

    bucket_sizes = df.group_by("bucket").agg(pl.len().alias("n"))
    total = len(df)
    bucket_sizes = bucket_sizes.with_columns(
        (pl.col("n") / total * n_total).cast(pl.Int64).clip(1).alias("sample_n")
    )

    sampled = []
    for row in bucket_sizes.iter_rows(named=True):
        b = row["bucket"]
        n = row["sample_n"]
        pool = df.filter(pl.col("bucket") == b).sample(
            n=min(n, len(df.filter(pl.col("bucket") == b))),
            seed=42,
        )
        sampled.append(pool)

    return pl.concat(sampled)


def fetch_events_for_dids(conn: pymysql.Connection, dids: list[str]) -> pl.DataFrame:
    """Fetch time_us for a list of DIDs from pau_db.user_core_events."""
    if not dids:
        return pl.DataFrame(schema={"did": pl.Utf8, "time_us": pl.Int64})

    all_rows = []
    with conn.cursor() as cur:
        for i in range(0, len(dids), FETCH_BATCH):
            batch = dids[i:i + FETCH_BATCH]
            placeholders = ",".join(["%s"] * len(batch))
            sql = f"""
                SELECT did, time_us
                FROM pau_db.user_core_events
                WHERE did IN ({placeholders})
                ORDER BY did, time_us
            """
            cur.execute(sql, batch)
            all_rows.extend(cur.fetchall())

    return pl.DataFrame(all_rows, schema=["did", "time_us"], orient="row")


def compute_per_user_gap_stats(df: pl.DataFrame) -> pl.DataFrame:
    """Compute per-user gap statistics from raw events.

    Returns DataFrame: did, median_gap_s, iqr_gap_s, skewness_gap, n_gaps.
    """
    gaps_expr = (
        (pl.col("time_us").diff().over("did") / 1_000_000.0)
        .alias("gap_s")
    )
    stats = (
        df
        .sort(["did", "time_us"])
        .with_columns(gaps_expr)
        .filter(pl.col("gap_s").is_not_null())
        .filter(pl.col("gap_s") >= 0)
        .group_by("did")
        .agg([
            pl.median("gap_s").alias("median_gap_s"),
            (pl.col("gap_s").quantile(0.75) - pl.col("gap_s").quantile(0.25)).alias("iqr_gap_s"),
            pl.len().alias("n_gaps"),
        ])
    )
    return stats


def add_gap_skewness(events_df: pl.DataFrame, stats_df: pl.DataFrame) -> pl.DataFrame:
    """Compute skewness per user from raw events (polars doesn't have skew natively)."""
    skews = {}

    # Process per-user to get skewness (polars lacks native group_by skew)
    for did, group in events_df.sort(["did", "time_us"]).group_by("did", maintain_order=True):
        times = group["time_us"].to_numpy()
        if len(times) < 3:
            continue
        gaps = np.diff(times) / 1_000_000.0
        gaps = gaps[gaps >= 0]
        if len(gaps) < 3:
            continue
        from scipy.stats import skew
        skew_val = float(skew(gaps))
        did_str = did[0]
        skews[did_str] = skew_val

    skew_df = pl.DataFrame(
        [{"did": k, "skewness_gap": v} for k, v in skews.items()]
    )
    return stats_df.join(skew_df, on="did", how="left")


def plot_gap_statistics(gap_stats: pl.DataFrame, user_stats: pl.DataFrame):
    """Multi-panel: per-user median gap hist, IQR hist, skewness hist,
    median gap vs total events scatter."""
    set_mpl_style()

    # Join with user stats for event-count context
    merged = gap_stats.join(
        user_stats.select(["did", "total_events"]), on="did", how="left"
    )

    # Add bucket
    merged = merged.with_columns([
        pl.when(pl.col("total_events") <= 5).then(pl.lit("≤5"))
         .when(pl.col("total_events") <= 25).then(pl.lit("6–25"))
         .when(pl.col("total_events") <= 100).then(pl.lit("26–100"))
         .when(pl.col("total_events") <= 500).then(pl.lit("101–500"))
         .otherwise(pl.lit("500+")).alias("bucket"),
    ])

    fig, axes = plt.subplots(2, 3, figsize=(20, 12))

    # ---- Histogram: per-user median gap ----
    ax = axes[0, 0]
    med_gaps = merged["median_gap_s"].drop_nulls().to_numpy()
    med_gaps_clipped = med_gaps[med_gaps <= np.percentile(med_gaps, 99)]
    ax.hist(med_gaps_clipped, bins=80, color="#4A90D9", alpha=0.85, edgecolor="none")
    ax.axvline(x=60, color="#E67E22", linestyle="--", label="1 min")
    ax.axvline(x=300, color="#D94A4A", linestyle="--", label="5 min")
    ax.axvline(x=600, color="#8E44AD", linestyle="--", label="10 min")
    ax.set_xlabel("Median inter-arrival gap (seconds)")
    ax.set_ylabel("Number of users")
    ax.set_title("Per-user median gap (P99 clipped)")
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    # ---- Histogram: per-user IQR of gaps ----
    ax = axes[0, 1]
    iqr_gaps = merged["iqr_gap_s"].drop_nulls().to_numpy()
    iqr_clipped = iqr_gaps[iqr_gaps <= np.percentile(iqr_gaps, 99)]
    ax.hist(iqr_clipped, bins=80, color="#27AE60", alpha=0.85, edgecolor="none")
    ax.set_xlabel("IQR of inter-arrival gaps (seconds)")
    ax.set_ylabel("Number of users")
    ax.set_title("Per-user gap IQR (P99 clipped)")
    ax.grid(True, alpha=0.3)

    # ---- Histogram: per-user gap skewness ----
    ax = axes[0, 2]
    sk = merged["skewness_gap"].drop_nulls().to_numpy()
    if len(sk) > 0:
        sk_clipped = sk[(sk > -5) & (sk < 20)]
        ax.hist(sk_clipped, bins=80, color="#8E44AD", alpha=0.85, edgecolor="none")
        ax.set_xlabel("Gap skewness")
        ax.set_ylabel("Number of users")
        ax.set_title("Per-user gap skewness")
        ax.axvline(x=0, color="#D94A4A", linestyle=":", alpha=0.5)
        ax.grid(True, alpha=0.3)
    else:
        ax.text(0.5, 0.5, "No skewness data", transform=ax.transAxes, ha="center")

    # ---- CDF of gaps by event-count bucket ----
    # For this we need raw gap data per user, stratified. Compute from per-user medians.
    ax = axes[1, 0]
    bucket_order = ["≤5", "6–25", "26–100", "101–500", "500+"]
    colors_b = ["#AAAAAA", "#4A90D9", "#27AE60", "#E67E22", "#D94A4A"]
    for bucket, color in zip(bucket_order, colors_b):
        bdata = merged.filter(pl.col("bucket") == bucket)["median_gap_s"].drop_nulls().to_numpy()
        if len(bdata) < 10:
            continue
        bdata = bdata[bdata <= np.percentile(bdata, 95)]
        sorted_b = np.sort(bdata)
        cdf = np.arange(1, len(sorted_b) + 1) / len(sorted_b)
        ax.plot(sorted_b, cdf, color=color, linewidth=1.5, alpha=0.8, label=bucket)
    ax.set_xlabel("Median inter-arrival gap (seconds)")
    ax.set_ylabel("Cumulative fraction of users")
    ax.set_title("CDF of per-user median gap by event-count")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # ---- Scatter: median gap vs total events ----
    ax = axes[1, 1]
    te = merged["total_events"].to_numpy()
    mg = merged["median_gap_s"].to_numpy()
    mask = (te > 0) & (mg > 0)
    ax.hexbin(te[mask], mg[mask], gridsize=50, cmap="YlOrRd", mincnt=1, norm=LogNorm())
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Total events")
    ax.set_ylabel("Median gap (seconds)")
    ax.set_title("Median gap vs total events")
    ax.grid(True, alpha=0.3)

    # ---- Boxplot: median gap by bucket ----
    ax = axes[1, 2]
    box_data = []
    labels = []
    for bucket in bucket_order:
        bdata = merged.filter(pl.col("bucket") == bucket)["median_gap_s"].drop_nulls().to_numpy()
        bdata = bdata[bdata <= np.percentile(bdata, 95)]
        if len(bdata) > 5:
            box_data.append(bdata)
            labels.append(bucket)
    ax.boxplot(box_data, labels=labels, patch_artist=True,
               boxprops=dict(facecolor="#4A90D9", alpha=0.6))
    ax.set_xlabel("Event-count bucket")
    ax.set_ylabel("Median gap (seconds, P95 clipped)")
    ax.set_title("Median gap distribution by user class")
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    savefig(fig, "04_per_user_gaps.png")


def run(sample_size: int = DEFAULT_SAMPLE, force_reload: bool = False) -> dict:
    """Run §4 and return results dict."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    user_stats = load_or_fetch_stats(force=force_reload)

    print(f"Sampling {sample_size:,} users stratified by event-count ...", file=sys.stderr)
    sampled = sample_dids_by_strata(user_stats, sample_size)
    dids = sampled["did"].to_list()
    print(f"  → {len(dids):,} DIDs", file=sys.stderr)

    print("Fetching raw events for sampled users ...", file=sys.stderr)
    conn = get_connection()
    try:
        events_df = fetch_events_for_dids(conn, dids)
    finally:
        conn.close()
    print(f"  → {len(events_df):,} events from {events_df['did'].n_unique():,} users",
          file=sys.stderr)

    print("Computing per-user gap statistics ...", file=sys.stderr)
    gap_stats = compute_per_user_gap_stats(events_df)
    gap_stats = add_gap_skewness(events_df, gap_stats)

    plot_gap_statistics(gap_stats, user_stats)

    # Summary
    mg = gap_stats["median_gap_s"].drop_nulls().to_numpy()
    iqr = gap_stats["iqr_gap_s"].drop_nulls().to_numpy()

    lines = [
        "=== §4: Per-user gap distribution analysis ===",
        f"Sampled {len(dids):,} users, {len(gap_stats):,} with ≥2 events",
        "",
        "-- Per-user median gap percentiles --",
    ]
    for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        lines.append(f"  P{p:>2d}: {np.percentile(mg, p):>10.0f}s ({np.percentile(mg, p)/60:.1f} min)")

    lines.extend([
        "",
        f"  Users with median gap < 1 min:  {(mg < 60).sum():,} ({(mg < 60).sum()/len(mg)*100:.1f}%)",
        f"  Users with median gap < 5 min:  {(mg < 300).sum():,} ({(mg < 300).sum()/len(mg)*100:.1f}%)",
        f"  Users with median gap < 10 min: {(mg < 600).sum():,} ({(mg < 600).sum()/len(mg)*100:.1f}%)",
        "",
        "Interpretation:",
        "  If per-user median gaps cluster narrowly → single global threshold works.",
        "  If medians vary widely by event-count → per-class thresholds needed.",
    ])

    out = "\n".join(lines)
    (OUT_DIR / "04_summary.txt").write_text(out)
    print(f"\n{out}", file=sys.stderr)

    return {
        "section": "§4 — Gap distribution",
        "n_sampled": len(dids),
        "n_with_gaps": len(gap_stats),
        "median_of_medians": float(np.median(mg)),
        "pct_below_5min": 100 * (mg < 300).sum() / len(mg),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    run(sample_size=args.sample, force_reload=args.force)
