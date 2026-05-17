#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "numpy",
#     "matplotlib",
# ]
# ///
"""
Post lifetime analysis — "How long does a top-level post stay alive?"

Queries pau_db.post_lifetime to compute the distribution of time from post
creation to last engagement (repost / like / reply / combined), generates
summary statistics and plots.

All metrics are precomputed in the table (last_engagement_us, total_engagement)
so this script only fetches and visualises.

Output:
  - Console summary: stats per engagement type + combined
  - post-lifetime/results/lifetime_histogram.png  (all types overlaid)
  - post-lifetime/results/lifetime_cdf.png        (all types overlaid)
  - post-lifetime/results/engagement_correlation.png (hexbin: likes vs reposts)

Usage:
    uv run post-lifetime/analyze_post_lifetime.py
    uv run post-lifetime/analyze_post_lifetime.py --no-plots
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pymysql

# ---------------------------------------------------------------------------
# Environment & config
# ---------------------------------------------------------------------------

def _load_env_file():
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for f in candidates:
        if f.exists():
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
            return

_load_env_file()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

DB_CONFIG = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": "pau_db",
    "charset": "utf8mb4",
}

RESULTS_DIR = Path(__file__).resolve().parent / "results"

ENGAGEMENT_LABELS = {
    "repost":   "Reposts",
    "like":     "Likes",
    "reply":    "Replies",
    "combined": "Combined (any)",
}

ENGAGEMENT_COLORS = {
    "repost":   "#1d9bf0",
    "like":     "#e0245e",
    "reply":    "#17bf63",
    "combined": "#794bc4",
}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def get_conn():
    return pymysql.connect(**DB_CONFIG)


def fetch_lifetimes(conn) -> dict[str, list[float]]:
    """
    Fetch post lifetimes (seconds) from precomputed columns.
    Returns per-type + combined lists (only posts with that engagement type).
    """
    sql = """
        SELECT
            (last_reposted_us   - UNIX_TIMESTAMP(created_at) * 1000000)
                / 1000000.0  AS repost_s,
            (last_liked_us      - UNIX_TIMESTAMP(created_at) * 1000000)
                / 1000000.0  AS like_s,
            (last_replied_us    - UNIX_TIMESTAMP(created_at) * 1000000)
                / 1000000.0  AS reply_s,
            (last_engagement_us - UNIX_TIMESTAMP(created_at) * 1000000)
                / 1000000.0  AS combined_s
        FROM post_lifetime
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    result: dict[str, list[float]] = {
        "repost": [], "like": [], "reply": [], "combined": [],
    }
    for row in rows:
        for i, key in enumerate(["repost", "like", "reply", "combined"]):
            val = row[i]
            if val is not None:
                result[key].append(float(val))
    return result


def fetch_summary_counts(conn) -> dict:
    """Get total top-level posts and engagement-level breakdowns."""
    sql = """
        SELECT
            COUNT(*)                                                      AS total,
            COUNT(NULLIF(total_reposts,   0))                             AS with_reposts,
            COUNT(NULLIF(total_likes,     0))                             AS with_likes,
            COUNT(NULLIF(total_replies,   0))                             AS with_replies,
            COUNT(NULLIF(total_engagement, 0))                            AS with_any,
            SUM(total_reposts)                                            AS sum_reposts,
            SUM(total_likes)                                              AS sum_likes,
            SUM(total_replies)                                            AS sum_replies,
            SUM(total_engagement)                                         AS sum_engagement
        FROM post_lifetime
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        total = row[0]
        return {
            "total_posts":     total,
            "with_reposts":    row[1],  "pct_reposts":  pct(row[1], total),
            "with_likes":      row[2],  "pct_likes":    pct(row[2], total),
            "with_replies":    row[3],  "pct_replies":  pct(row[3], total),
            "with_any":        row[4],  "pct_any":      pct(row[4], total),
            "no_engagement":   total - row[4],
            "sum_reposts":     row[5],
            "sum_likes":       row[6],
            "sum_replies":     row[7],
            "sum_engagement":  row[8],
        }

def pct(part, total):
    return round(100 * part / total, 2) if total else 0


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_stats(lifetimes: list[float]) -> dict:
    arr = np.array(lifetimes)
    return {
        "count":   len(arr),
        "mean_h":  np.mean(arr) / 3600,
        "median_h": np.median(arr) / 3600,
        "std_h":   np.std(arr) / 3600,
        "min_h":   np.min(arr) / 3600 if len(arr) else 0,
        "max_h":   np.max(arr) / 3600 if len(arr) else 0,
        "p50_h":   np.percentile(arr, 50) / 3600,
        "p75_h":   np.percentile(arr, 75) / 3600,
        "p90_h":   np.percentile(arr, 90) / 3600,
        "p95_h":   np.percentile(arr, 95) / 3600,
        "p99_h":   np.percentile(arr, 99) / 3600,
        "p999_h":  np.percentile(arr, 99.9) / 3600,
    }


def fmt_stats(stats: dict) -> str:
    lines = [
        f"  n      = {stats['count']:>12,}",
        f"  mean   = {stats['mean_h']:>10.1f} h  ({stats['mean_h']/24:.1f} days)",
        f"  median = {stats['median_h']:>10.1f} h  ({stats['median_h']/24:.1f} days)",
        f"  std    = {stats['std_h']:>10.1f} h",
        f"  min    = {stats['min_h']:>10.1f} h",
        f"  max    = {stats['max_h']:>10.1f} h  ({stats['max_h']/24:.1f} days)",
        f"  p50    = {stats['p50_h']:>10.1f} h",
        f"  p75    = {stats['p75_h']:>10.1f} h",
        f"  p90    = {stats['p90_h']:>10.1f} h",
        f"  p95    = {stats['p95_h']:>10.1f} h",
        f"  p99    = {stats['p99_h']:>10.1f} h",
        f"  p99.9  = {stats['p999_h']:>10.1f} h",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_histogram(lifetimes: dict[str, list[float]], output_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 7))

    for key in ["repost", "like", "reply", "combined"]:
        arr = np.array(lifetimes[key])
        if len(arr) == 0:
            continue
        arr = arr[arr > 0]
        log_min = np.log10(max(arr.min(), 1))
        log_max = np.log10(arr.max())
        bins = np.logspace(log_min, log_max, 70)
        ax.hist(arr, bins=bins, color=ENGAGEMENT_COLORS[key],
                alpha=0.35, edgecolor="white", linewidth=0.3,
                label=f"{ENGAGEMENT_LABELS[key]} (n={len(arr):,})")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Post lifetime (seconds)")
    ax.set_ylabel("Number of posts")
    ax.set_title("Top-level post lifetime distributions by engagement type")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ Histogram saved → {output_path}")


def plot_cdf(lifetimes: dict[str, list[float]], output_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 7))

    for key in ["repost", "like", "reply", "combined"]:
        arr = np.sort(np.array(lifetimes[key]))
        if len(arr) == 0:
            continue
        y = np.arange(1, len(arr) + 1) / len(arr) * 100
        ax.plot(arr, y, color=ENGAGEMENT_COLORS[key], linewidth=1.5,
                label=f"{ENGAGEMENT_LABELS[key]} (n={len(arr):,})")

    ax.set_xscale("log")
    ax.set_xlabel("Post lifetime (seconds)")
    ax.set_ylabel("Cumulative fraction of engaged posts (%)")
    ax.set_title("CDF of top-level post lifetimes by engagement type")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3)

    # Percentile markers on the combined line
    if len(lifetimes["combined"]) > 0:
        arr_c = np.sort(lifetimes["combined"])
        for pct, ls in [(50, "--"), (90, "-."), (99, ":")]:
            val = np.percentile(arr_c, pct)
            ax.axvline(val, color="gray", linestyle=ls, alpha=0.5, linewidth=1)
            ax.text(val, 2, f" p{pct}", fontsize=7, color="gray",
                    rotation=90, va="bottom")

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ CDF saved → {output_path}")


def plot_correlation(conn, output_path: Path):
    """Hexbin of likes vs reposts per post (log-log), only posts with both."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sql = """
        SELECT total_reposts, total_likes
        FROM post_lifetime
        WHERE total_reposts > 0 AND total_likes > 0
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    reposts = np.array([r[0] for r in rows], dtype=np.float64)
    likes   = np.array([r[1] for r in rows], dtype=np.float64)

    fig, ax = plt.subplots(figsize=(8, 8))
    hb = ax.hexbin(reposts, likes, gridsize=60, cmap="YlOrRd",
                   bins="log", mincnt=1, xscale="log", yscale="log")
    ax.set_xlabel("Total reposts")
    ax.set_ylabel("Total likes")
    ax.set_title(f"Likes vs Reposts (n={len(reposts):,} top-level posts with both)")
    plt.colorbar(hb, ax=ax, label="log₁₀(count)")

    x_ref = np.logspace(0, np.log10(max(reposts.max(), 10)), 100)
    ax.plot(x_ref, x_ref * 10, "k--", alpha=0.3, linewidth=1, label="y = 10x")
    ax.legend()

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ Correlation plot saved → {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Post lifetime analysis")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    print("=" * 68)
    print("  Post Lifetime Analysis — Top-level posts only")
    print("  Time from creation → last repost / like / reply")
    print("=" * 68)
    print()

    conn = get_conn()
    try:
        # ── Summary counts ───────────────────────────────────────────────
        print("Fetching summary counts …")
        cnt = fetch_summary_counts(conn)
        print(f"  Total top-level posts:  {cnt['total_posts']:>12,}")
        print(f"  With reposts:            {cnt['with_reposts']:>12,}  "
              f"({cnt['pct_reposts']}%)")
        print(f"  With likes:              {cnt['with_likes']:>12,}  "
              f"({cnt['pct_likes']}%)")
        print(f"  With replies:            {cnt['with_replies']:>12,}  "
              f"({cnt['pct_replies']}%)")
        print(f"  With any engagement:     {cnt['with_any']:>12,}  "
              f"({cnt['pct_any']}%)")
        print(f"  No engagement at all:    {cnt['no_engagement']:>12,}")
        print()
        print(f"  Total reposts given:     {cnt['sum_reposts']:>12,}")
        print(f"  Total likes given:       {cnt['sum_likes']:>12,}")
        print(f"  Total replies given:     {cnt['sum_replies']:>12,}")
        print(f"  Total engagement events: {cnt['sum_engagement']:>12,}")
        print()

        # ── Lifetime data ────────────────────────────────────────────────
        print("Fetching lifetime data (precomputed in DB) …")
        lifetimes = fetch_lifetimes(conn)
        for key in ["repost", "like", "reply", "combined"]:
            print(f"  {ENGAGEMENT_LABELS[key]:<20s} → {len(lifetimes[key]):>10,} posts")
        print()

        # ── Statistics per type ──────────────────────────────────────────
        for key in ["repost", "like", "reply", "combined"]:
            if len(lifetimes[key]) == 0:
                continue
            stats = compute_stats(lifetimes[key])
            print(f"── {ENGAGEMENT_LABELS[key]} lifetime ──")
            print(fmt_stats(stats))
            print()

        # ── Plots ────────────────────────────────────────────────────────
        if not args.no_plots:
            print("Generating plots …")
            plot_histogram(lifetimes, RESULTS_DIR / "lifetime_histogram.png")
            plot_cdf(lifetimes, RESULTS_DIR / "lifetime_cdf.png")
            plot_correlation(conn, RESULTS_DIR / "engagement_correlation.png")
            print()

        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
