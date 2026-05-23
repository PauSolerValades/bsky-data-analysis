#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "numpy",
#     "matplotlib",
#     "pymysql",
# ]
# ///
"""
Phase 3 — Time-to-first-engagement ("time to ignition").

How long after creation does the FIRST repost / like / reply arrive?
Uses the precomputed first_* columns in post_lifetime.

Output:
  - Console: percentiles of time-to-first for each engagement type
  - eda/results/time_to_first_cdf.png   (CDF overlay)
  - eda/results/time_to_first_hist.png  (histogram, log-log)

Usage:
    uv run post-lifetime/eda/time_to_first.py
"""

import os
from pathlib import Path

import numpy as np
import pymysql

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_ENV = {}
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            _ENV[key] = val.strip().strip('"').strip("'")

DB_CONFIG = {
    "host": _ENV.get("DATABASE_HOST", "10.18.74.14"),
    "port": int(_ENV.get("DATABASE_PORT", "9030")),
    "user": _ENV.get("DATABASE_USER", "pau"),
    "password": _ENV.get("PAU_PASSWORD", ""),
    "database": "pau_db",
    "charset": "utf8mb4",
}

RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

# ===========================================================================
# Data
# ===========================================================================

def fetch_time_to_first(conn):
    """
    Fetch time-to-first (seconds) for each engagement type.
    Returns dict of numpy arrays.
    """
    sql = """
        SELECT
            (first_reposted_us - UNIX_TIMESTAMP(created_at)*1000000)/1000000.0,
            (first_liked_us    - UNIX_TIMESTAMP(created_at)*1000000)/1000000.0,
            (first_replied_us  - UNIX_TIMESTAMP(created_at)*1000000)/1000000.0
        FROM post_lifetime
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    result = {"repost": [], "like": [], "reply": []}
    keys = ["repost", "like", "reply"]
    for row in rows:
        for i, key in enumerate(keys):
            v = row[i]
            if v is not None and v > 0:
                result[key].append(float(v))

    return {k: np.array(v) for k, v in result.items()}


def print_stats(data_dict):
    """Print percentile table for each engagement type."""
    header = f"  {'Percentile':<12s}"
    for label in ["repost", "like", "reply"]:
        header += f" {label:>12s}"
    print(header)
    print(f"  {'─'*12} {'─'*12} {'─'*12} {'─'*12}")

    for pct in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        line = f"  p{pct:<11d}"
        for label in ["repost", "like", "reply"]:
            arr = data_dict[label]
            if len(arr) == 0:
                line += f" {'—':>12s}"
                continue
            val = np.percentile(arr, pct)
            if val < 60:
                line += f" {val:>8.1f}s  "
            elif val < 3600:
                line += f" {val/60:>8.1f}m  "
            else:
                line += f" {val/3600:>8.1f}h  "
        print(line)

    # Summary row
    print(f"  {'─'*12} {'─'*12} {'─'*12} {'─'*12}")
    line = f"  {'n':<12s}"
    for label in ["repost", "like", "reply"]:
        line += f" {len(data_dict[label]):>12,}"
    print(line)
    print()

    # Median and mean
    print(f"  {'Metric':<12s} {'repost':>12s} {'like':>12s} {'reply':>12s}")
    print(f"  {'─'*12} {'─'*12} {'─'*12} {'─'*12}")
    for metric_name, fn in [("mean", np.mean), ("median", np.median)]:
        line = f"  {metric_name:<12s}"
        for label in ["repost", "like", "reply"]:
            arr = data_dict[label]
            if len(arr) == 0:
                line += f" {'—':>12s}"
                continue
            val_s = fn(arr)
            if val_s < 60:
                line += f" {val_s:>8.1f}s  "
            elif val_s < 3600:
                line += f" {val_s/60:>8.1f}m  "
            else:
                line += f" {val_s/3600:>8.1f}h  "
        print(line)


# ===========================================================================
# Plotting
# ===========================================================================

def plot_ttf_cdf(data_dict, output_path):
    """CDF of time-to-first for each type."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"repost": "#1d9bf0", "like": "#e0245e", "reply": "#17bf63"}

    fig, ax = plt.subplots(figsize=(11, 7))
    for label in ["repost", "like", "reply"]:
        arr = np.sort(data_dict[label])
        if len(arr) == 0:
            continue
        arr = arr[arr > 0]
        y = np.arange(1, len(arr) + 1) / len(arr) * 100
        ax.semilogx(arr, y, color=colors[label], linewidth=1.5,
                    label=f"{label} (n={len(arr):,})")

    ax.set_xlabel("Time to first engagement (seconds)")
    ax.set_ylabel("Cumulative fraction of posts (%)")
    ax.set_title("Time-to-first-engagement CDFs")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # Mark median
    for label in ["repost", "like", "reply"]:
        arr = data_dict[label]
        if len(arr) == 0:
            continue
        med = np.median(arr)
        ax.axvline(med, color=colors[label], linestyle="--", alpha=0.4, linewidth=1)
        if med < 60:
            ax.text(med, 55, f" {med:.0f}s", fontsize=7, color=colors[label])
        elif med < 3600:
            ax.text(med, 55, f" {med/60:.0f}m", fontsize=7, color=colors[label])
        else:
            ax.text(med, 55, f" {med/3600:.1f}h", fontsize=7, color=colors[label])

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ {output_path}")


def plot_ttf_histogram(data_dict, output_path):
    """Log-log histogram of time-to-first for each type."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"repost": "#1d9bf0", "like": "#e0245e", "reply": "#17bf63"}

    fig, ax = plt.subplots(figsize=(11, 7))
    for label in ["repost", "like", "reply"]:
        arr = data_dict[label]
        if len(arr) == 0:
            continue
        arr = arr[arr > 0]
        log_min = np.log10(max(arr.min(), 1))
        log_max = np.log10(arr.max())
        bins = np.logspace(log_min, log_max, 60)
        ax.hist(arr, bins=bins, color=colors[label], alpha=0.35,
                edgecolor="white", linewidth=0.3,
                label=f"{label} (median={np.median(arr)/60:.1f}m)")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Time to first engagement (seconds)")
    ax.set_ylabel("Number of posts")
    ax.set_title("Time-to-first-engagement distributions")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ {output_path}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    print("=" * 65)
    print("  Phase 3 — Time-to-first-engagement")
    print("=" * 65)
    print()

    conn = pymysql.connect(**DB_CONFIG)
    try:
        print("Fetching time-to-first data …")
        data = fetch_time_to_first(conn)
        print()

        print("Percentile distribution:")
        print_stats(data)

        print("Generating plots …")
        plot_ttf_cdf(data, RESULTS / "time_to_first_cdf.png")
        plot_ttf_histogram(data, RESULTS / "time_to_first_hist.png")
        print()

        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
