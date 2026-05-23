#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "numpy",
#     "matplotlib",
#     "pandas",
# ]
# ///
"""
Plot structural virality results from compute_virality Go output.

Reads: structural-virality/results/virality_results.csv
Writes: structural-virality/results/*.png

Usage:
    uv run structural-virality/03_plot_virality.py
    uv run structural-virality/03_plot_virality.py --input results/virality_results.csv
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# --- Plot helpers -----------------------------------------------------------

def _save(fig, name: str):
    path = RESULTS_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ {path}")


# --- Plot 1: ν distribution (histogram + KDE) -------------------------------

def plot_distribution(df: pd.DataFrame):
    v = df["structural_virality"].values

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Linear scale, focus on bulk
    ax1.hist(v, bins=100, color="#1d9bf0", edgecolor="white", alpha=0.85)
    ax1.axvline(1.0, color="#e0245e", linestyle="--", linewidth=1.5, label="ν=1 (broadcast)")
    ax1.set_xlabel("Structural Virality ν")
    ax1.set_ylabel("Number of cascades")
    ax1.set_title(f"Structural Virality Distribution\n(n={len(v):,} cascades)")
    ax1.legend()
    ax1.grid(axis="y", alpha=0.3)

    # Log-log, show tail
    counts, bins = np.histogram(v, bins=100)
    bin_centers = (bins[:-1] + bins[1:]) / 2
    mask = counts > 0
    ax2.loglog(bin_centers[mask], counts[mask], "o-", color="#17bf63",
               markersize=3, linewidth=1.2)
    ax2.set_xlabel("Structural Virality ν")
    ax2.set_ylabel("Number of cascades")
    ax2.set_title("ν Distribution (log-log)")
    ax2.axvline(1.0, color="#e0245e", linestyle="--", alpha=0.5)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    _save(fig, "virality_distribution.png")


# --- Plot 2: Cascade size vs ν scatter --------------------------------------

def plot_size_vs_virality(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 7))

    x = df["cascade_size"].values
    y = df["structural_virality"].values

    # Hexbin for large datasets, scatter for smaller
    if len(df) > 5000:
        hb = ax.hexbin(x, y, gridsize=80, cmap="YlOrRd", bins="log", mincnt=1)
        plt.colorbar(hb, ax=ax, label="Count (log scale)")
    else:
        ax.scatter(x, y, alpha=0.3, s=2, c="#1d9bf0", edgecolors="none")

    ax.set_xscale("log")
    ax.set_xlabel("Cascade Size (nodes)")
    ax.set_ylabel("Structural Virality ν")
    ax.set_title(f"Cascade Size vs Structural Virality\n(n={len(df):,} cascades)")
    ax.axhline(1.0, color="#e0245e", linestyle="--", linewidth=1, alpha=0.6)
    ax.grid(True, alpha=0.3)

    # Annotate with size bucket means
    size_buckets = [2, 3, 5, 10, 20, 50, 100, 500, 1000, 5000, 1000000]
    for i in range(len(size_buckets) - 1):
        lo, hi = size_buckets[i], size_buckets[i + 1]
        mask = (x >= lo) & (x < hi)
        if mask.sum() < 10:
            continue
        mean_v = y[mask].mean()
        mean_x = np.exp((np.log(lo) + np.log(min(hi, x[mask].max()))) / 2)
        ax.plot(mean_x, mean_v, "o", color="black", markersize=6, zorder=5)
        ax.annotate(f"{mean_v:.2f}", (mean_x, mean_v),
                     textcoords="offset points", xytext=(0, 10),
                     fontsize=8, ha="center", color="black")

    fig.tight_layout()
    _save(fig, "virality_vs_size.png")


# --- Plot 3: Top 50 most viral posts ----------------------------------------

def plot_top_viral(df: pd.DataFrame, n_top: int = 50):
    top = df.nlargest(n_top, "structural_virality")

    fig, ax = plt.subplots(figsize=(14, 8))

    labels = [f"P{i}" for i in range(1, n_top + 1)]
    values = top["structural_virality"].values
    sizes = top["cascade_size"].values
    colors = plt.cm.viridis(np.linspace(0.2, 0.9, n_top))

    bars = ax.barh(range(n_top)[::-1], values[::-1], color=colors[::-1],
                    edgecolor="black", alpha=0.85)

    for i, (v, sz) in enumerate(zip(values[::-1], sizes[::-1])):
        ax.text(v + 0.01, i, f"size={sz}", va="center", fontsize=7, color="gray")

    ax.set_yticks(range(n_top)[::-1])
    ax.set_yticklabels(labels[::-1])
    ax.set_xlabel("Structural Virality ν")
    ax.set_title(f"Top {n_top} Posts by Structural Virality")
    ax.grid(axis="x", alpha=0.3)

    fig.tight_layout()
    _save(fig, "virality_top50.png")


# --- Plot 4: CCDF of ν ------------------------------------------------------

def plot_ccdf(df: pd.DataFrame):
    v = np.sort(df["structural_virality"].values)
    ccdf = 1.0 - np.arange(len(v)) / len(v)

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.loglog(v, ccdf, "o-", color="#1d9bf0", markersize=2, linewidth=1)
    ax.set_xlabel("Structural Virality ν")
    ax.set_ylabel("P(Ν > ν)")
    ax.set_title(f"Complementary CDF of Structural Virality\n(n={len(v):,} cascades)")
    ax.axvline(1.0, color="#e0245e", linestyle="--", alpha=0.5)
    ax.grid(True, alpha=0.3)

    # Add percentile annotations
    for p, ls in [(50, "--"), (90, "-."), (99, ":")]:
        val = np.percentile(v, p)
        ax.axvline(val, color="gray", linestyle=ls, alpha=0.5)
        ax.text(val, 1.0 - p/100 + 0.02, f"p{p}={val:.2f}",
                fontsize=8, color="gray", rotation=90)

    fig.tight_layout()
    _save(fig, "virality_ccdf.png")


# --- Plot 5: ν by cascade-size bucket (box plot) ----------------------------

def plot_virality_by_bucket(df: pd.DataFrame):
    size_buckets = [2, 3, 4, 6, 11, 21, 51, 101, 501, 1001, 1000000]
    labels = ["2", "3", "4–5", "6–10", "11–20", "21–50",
              "51–100", "101–500", "501–1000", "1001+"]

    bucket_data = []
    bucket_labels = []
    bucket_counts = []

    for i in range(len(size_buckets) - 1):
        lo, hi = size_buckets[i], size_buckets[i + 1]
        mask = (df["cascade_size"] >= lo) & (df["cascade_size"] < hi)
        vals = df.loc[mask, "structural_virality"].values
        if len(vals) < 5:
            continue
        bucket_data.append(vals)
        bucket_labels.append(labels[i])
        bucket_counts.append(len(vals))

    fig, ax = plt.subplots(figsize=(14, 7))
    bp = ax.boxplot(bucket_data, labels=bucket_labels, patch_artist=True,
                     showfliers=False, widths=0.6)

    for patch, i in zip(bp["boxes"], range(len(bucket_data))):
        patch.set_facecolor(plt.cm.viridis(i / max(1, len(bucket_data) - 1)))

    ax.set_xlabel("Cascade Size (nodes)")
    ax.set_ylabel("Structural Virality ν")
    ax.set_title("Structural Virality by Cascade Size")
    ax.grid(axis="y", alpha=0.3)

    # Add count annotations
    for i, (lbl, cnt) in enumerate(zip(bucket_labels, bucket_counts)):
        ax.annotate(f"n={cnt:,}", (i + 1, ax.get_ylim()[1] * 0.98),
                    fontsize=7, ha="center", color="gray")

    fig.tight_layout()
    _save(fig, "virality_by_bucket.png")


# --- Plot 6: Max depth distribution -----------------------------------------

def plot_depth_distribution(df: pd.DataFrame):
    depths = df["max_depth"].values

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Histogram
    max_d = int(depths.max())
    bins = np.arange(0, min(max_d + 2, 50)) - 0.5
    ax1.hist(depths, bins=bins, color="#17bf63", edgecolor="white", alpha=0.85)
    ax1.set_xlabel("Max Tree Depth")
    ax1.set_ylabel("Number of cascades")
    ax1.set_title("Cascade Tree Depth Distribution")
    ax1.grid(axis="y", alpha=0.3)

    # Log-log
    depth_counts = np.bincount(depths.astype(int))
    valid = depth_counts > 0
    d_vals = np.arange(len(depth_counts))[valid]
    d_counts = depth_counts[valid]
    ax2.loglog(d_vals, d_counts, "o-", color="#e0245e", markersize=4, linewidth=1.2)
    ax2.set_xlabel("Max Tree Depth")
    ax2.set_ylabel("Number of cascades")
    ax2.set_title("Depth Distribution (log-log)")
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    _save(fig, "virality_depth_distribution.png")


# --- Main -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Plot structural virality results")
    parser.add_argument("--input", default=str(RESULTS_DIR / "virality_results.csv"),
                        help="Path to virality_results.csv from Go binary")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found. Run the Go binary first.")
        return

    print(f"Loading {input_path} ...")
    df = pd.read_csv(input_path)
    print(f"  {len(df):,} cascades loaded\n")

    # Filter: ensure valid data
    df = df[df["cascade_size"] >= 2]
    df = df[df["structural_virality"] >= 0]
    print(f"  {len(df):,} cascades after filtering (size ≥ 2, ν ≥ 0)\n")

    # Stats
    v = df["structural_virality"]
    print("ν summary:")
    print(f"  mean:  {v.mean():.4f}")
    print(f"  median:{v.median():.4f}")
    print(f"  std:   {v.std():.4f}")
    print(f"  min:   {v.min():.4f}")
    print(f"  max:   {v.max():.4f}")
    print(f"  p90:   {v.quantile(0.90):.4f}")
    print(f"  p99:   {v.quantile(0.99):.4f}")

    # ν = 1 (pure broadcast) fraction
    broadcast = (v == 1.0).sum()
    print(f"\n  ν = 1.0 (pure broadcast): {broadcast:,} ({100*broadcast/len(v):.1f}%)")

    print(f"\n  Max cascade size: {df['cascade_size'].max():,}")
    print(f"  Max depth: {df['max_depth'].max()}")

    print("\nGenerating plots...")
    plot_distribution(df)
    plot_size_vs_virality(df)
    plot_top_viral(df)
    plot_ccdf(df)
    plot_virality_by_bucket(df)
    plot_depth_distribution(df)
    print("\nDone.")


if __name__ == "__main__":
    main()
