#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "numpy",
#     "scipy",
#     "matplotlib",
#     "pymysql",
# ]
# ///
"""
Phase 2b — Temporal decay of engagement per post.

Fits per-post event accumulation curves N(t) ∝ t^β to characterise how
engagement decays over time within a post's lifetime.  Two approaches:

  Aggregate first (fit one curve to all events pooled):
    Bin all events by log-time, fit N(t) ∝ t^β.

  Per-post then aggregate (fit each post, then show distribution of β):
    For sampled posts with ≥20 events, fit individual N(t) curves.
    Show β distribution by engagement bucket.

Output:
  - Console: aggregate β, per-bucket β distributions
  - eda/results/temporal_decay_aggregate.png  (pooled N(t) curve)
  - eda/results/temporal_decay_per_post.png   (β distribution per bucket)

Usage:
    uv run post-lifetime/eda/temporal_decay.py
    uv run post-lifetime/eda/temporal_decay.py --sample 300
"""

import argparse
import os
from pathlib import Path

import numpy as np
import pymysql
from scipy.optimize import curve_fit
from scipy.stats import gaussian_kde

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def _load_env_file():
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".env",
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

def _env(k, d=""):
    return os.environ.get(k, d)

DB_CONFIG = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": "pau_db",
    "charset": "utf8mb4",
}

RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

# Engagement buckets
BUCKETS = [
    (20,   99,    "20–99"),
    (100,  999,   "100–999"),
    (1000, 9999,  "1K–10K"),
    (10000, None, "10K+"),
]


# ===========================================================================
# Data fetching
# ===========================================================================

def sample_posts(conn, samples_per_bucket: int) -> list[dict]:
    """
    Sample posts from each engagement bucket.
    Returns list of dicts with post_did, post_rkey, created_at, total_engagement, bucket.
    """
    sampled = []
    for lo, hi, label in BUCKETS:
        where = f"total_engagement >= {lo}"
        if hi is not None:
            where += f" AND total_engagement <= {hi}"

        sql = f"""
            SELECT post_did, post_rkey, created_at, total_engagement
            FROM post_lifetime
            WHERE {where}
            ORDER BY RAND()
            LIMIT {samples_per_bucket}
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

        if not rows:
            print(f"  ⚠ Bucket {label}: no posts found")
            continue

        for row in rows:
            sampled.append({
                "post_did": row[0],
                "post_rkey": row[1],
                "created_at": row[2],
                "total_engagement": row[3],
                "bucket": label,
            })
        print(f"  Bucket {label}: sampled {len(rows)} posts")

    return sampled


def fetch_events(conn, post_did: str, post_rkey: str) -> np.ndarray:
    """
    Fetch event timestamps (µs) for a post, sorted.
    Returns array of time_us values.
    """
    sql = """
        SELECT event_time_us
        FROM post_engagement_events
        WHERE post_did = %s AND post_rkey = %s
        ORDER BY event_time_us
    """
    with conn.cursor() as cur:
        cur.execute(sql, (post_did, post_rkey))
        return np.array([r[0] for r in cur.fetchall()], dtype=np.float64)


# ===========================================================================
# Aggregate approach: pool all events
# ===========================================================================

def fit_aggregate(conn, n_posts=2000):
    """
    Fetch events for n_posts random engaged posts, pool all event times,
    bin by log-time, fit N(t) ∝ t^β.
    """
    print(f"  Fetching events for {n_posts} random posts (aggregate approach) …")

    # Get random engaged post IDs
    sql = f"""
        SELECT post_did, post_rkey, created_at
        FROM post_lifetime
        WHERE total_engagement >= 5
        ORDER BY RAND()
        LIMIT {n_posts}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        posts = cur.fetchall()

    all_t_rel = []
    for i, (did, rkey, created_at) in enumerate(posts):
        if (i + 1) % 500 == 0:
            print(f"    {i+1}/{n_posts} …")
        events = fetch_events(conn, did, rkey)
        if len(events) == 0:
            continue
        created_us = created_at.timestamp() * 1_000_000
        t_rel = (events - created_us) / 1_000_000.0  # seconds
        t_rel = t_rel[t_rel > 0]
        all_t_rel.extend(t_rel.tolist())

    all_t_rel = np.array(all_t_rel)
    all_t_rel.sort()
    print(f"    Pooled {len(all_t_rel):,} events from {len(posts)} posts")

    # Log-spaced bins and cumulative count
    t_min = max(all_t_rel.min(), 1.0)
    t_max = all_t_rel.max()
    bin_edges = np.logspace(np.log10(t_min), np.log10(t_max), 60)
    bin_centers = np.sqrt(bin_edges[:-1] * bin_edges[1:])
    counts, _ = np.histogram(all_t_rel, bins=bin_edges)

    # Cumulative: events up to time t
    cum_counts = np.cumsum(counts)

    # Fit: cum_counts = A * t^β
    def power_law(t, A, beta):
        return A * t ** beta

    mask = cum_counts > 0
    if mask.sum() < 5:
        print("    Too few bins for fit")
        return None, None, None, None

    try:
        popt, pcov = curve_fit(
            power_law, bin_centers[mask], cum_counts[mask],
            p0=[cum_counts[mask][0] / bin_centers[mask][0], 0.5],
            maxfev=5000
        )
        beta = popt[1]
        beta_err = np.sqrt(pcov[1, 1]) if pcov.shape == (2, 2) else np.nan
        print(f"    Aggregate fit: β = {beta:.4f} ± {beta_err:.4f}")
        return bin_centers, cum_counts, beta, beta_err
    except Exception as e:
        print(f"    Fit failed: {e}")
        return bin_centers, cum_counts, None, None


# ===========================================================================
# Per-post approach
# ===========================================================================

def fit_post_curve(t_rel_sec: np.ndarray, min_events=20):
    """
    Fit N(t) ∝ t^β for a single post's event timeline.
    Returns (beta, beta_err, n_events) or None if fit fails.
    """
    if len(t_rel_sec) < min_events:
        return None

    t_rel_sec = np.sort(t_rel_sec[t_rel_sec > 0])
    n = len(t_rel_sec)

    # Use actual event times as cumulative: N(t_i) = i+1
    cum = np.arange(1, n + 1, dtype=np.float64)

    def power_law(t, A, beta):
        return A * t ** beta

    # For few events, use all; for many, sample
    if n > 5000:
        idx = np.linspace(0, n - 1, 5000, dtype=int)
        t_sample = t_rel_sec[idx]
        cum_sample = cum[idx]
    else:
        t_sample = t_rel_sec
        cum_sample = cum

    try:
        popt, pcov = curve_fit(
            power_law, t_sample, cum_sample,
            p0=[1.0, 0.5], maxfev=5000
        )
        beta = popt[1]
        beta_err = np.sqrt(pcov[1, 1]) if pcov.shape == (2, 2) else np.nan
        return beta, beta_err, n
    except Exception:
        return None


def fit_per_post(conn, sampled_posts: list[dict], min_events=20):
    """
    For each sampled post, fetch events and fit N(t) ∝ t^β.
    Returns dict: bucket_label → list of β values.
    """
    results = {b[2]: [] for b in BUCKETS}
    total = len(sampled_posts)

    for i, post in enumerate(sampled_posts):
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{total} posts fitted …")

        events = fetch_events(conn, post["post_did"], post["post_rkey"])
        if len(events) == 0:
            continue

        created_us = post["created_at"].timestamp() * 1_000_000
        t_rel = (events - created_us) / 1_000_000.0
        t_rel = t_rel[t_rel > 0]

        fit_result = fit_post_curve(t_rel, min_events=min_events)
        if fit_result is not None:
            beta, beta_err, n = fit_result
            if 0 < beta < 2:  # filter absurd values
                results[post["bucket"]].append(beta)

    return results


# ===========================================================================
# Plotting
# ===========================================================================

def plot_aggregate(bin_centers, cum_counts, beta, output_path):
    """Pooled N(t) with power-law fit."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.loglog(bin_centers, cum_counts, ".", markersize=3, color="#794bc4",
              alpha=0.6, label="Pooled events (binned)")

    if beta is not None:
        t_fit = np.logspace(np.log10(bin_centers[0]), np.log10(bin_centers[-1]), 200)
        # Recompute A using the fitted beta
        A = cum_counts[0] / bin_centers[0] ** beta
        ax.loglog(t_fit, A * t_fit ** beta, "-", color="black", linewidth=2,
                  label=f"Fit: N(t) ∝ t^{{{beta:.3f}}}")

    ax.set_xlabel("Time since creation (seconds)")
    ax.set_ylabel("Cumulative events N(t)")
    ax.set_title("Aggregate engagement accumulation curve (pooled posts)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ {output_path}")


def plot_per_post(beta_by_bucket: dict, output_path):
    """Distribution of fitted β per engagement bucket."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = ["#1d9bf0", "#e0245e", "#17bf63", "#794bc4"]
    fig, axes = plt.subplots(2, 2, figsize=(14, 11))
    axes = axes.flatten()

    for idx, (bucket_label, color) in enumerate(zip(
        [b[2] for b in BUCKETS], colors
    )):
        ax = axes[idx]
        betas = beta_by_bucket.get(bucket_label, [])
        if not betas:
            ax.text(0.5, 0.5, f"{bucket_label}\nno data", ha="center",
                    va="center", transform=ax.transAxes)
            ax.set_title(bucket_label)
            continue

        betas = np.array(betas)
        # Histogram
        ax.hist(betas, bins=30, color=color, alpha=0.6, edgecolor="white",
                density=True)

        # KDE overlay
        try:
            kde = gaussian_kde(betas)
            x_kde = np.linspace(betas.min(), betas.max(), 200)
            ax.plot(x_kde, kde(x_kde), "-", color="black", linewidth=1.5)
        except Exception:
            pass

        ax.axvline(np.median(betas), color="red", linestyle="--", alpha=0.7,
                   label=f"median β = {np.median(betas):.3f}")
        ax.axvline(1.0, color="gray", linestyle=":", alpha=0.5,
                   label="β = 1 (linear)")

        ax.set_xlabel("Fitted β (N(t) ∝ t^β)")
        ax.set_ylabel("Density")
        ax.set_title(f"Bucket: {bucket_label} (n={len(betas)})")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    # Summary stats
    ax = axes[3]
    ax.axis("off")
    lines = ["  β distribution summary:", ""]
    for bucket_label in [b[2] for b in BUCKETS]:
        betas = beta_by_bucket.get(bucket_label, [])
        if betas:
            b_arr = np.array(betas)
            lines.append(f"  {bucket_label:<10s}: n={len(b_arr):<5d}  "
                         f"median={np.median(b_arr):.4f}  "
                         f"mean={np.mean(b_arr):.4f}  "
                         f"std={np.std(b_arr):.4f}")
        else:
            lines.append(f"  {bucket_label:<10s}: no data")
    ax.text(0.05, 0.95, "\n".join(lines), transform=ax.transAxes,
            fontsize=9, verticalalignment="top", fontfamily="monospace")

    fig.suptitle("Per-post temporal decay: distribution of β", fontsize=13)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ {output_path}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=200,
                        help="Posts per engagement bucket (default: 200)")
    args = parser.parse_args()

    print("=" * 65)
    print("  Phase 2b — Temporal decay of engagement")
    print("=" * 65)
    print()

    conn = pymysql.connect(**DB_CONFIG)
    try:
        # ── Aggregate approach ──────────────────────────────────────────
        print("── Aggregate approach (pool all events) ──")
        bc, cc, beta_agg, beta_err = fit_aggregate(conn, n_posts=3000)
        print()

        # ── Per-post approach ───────────────────────────────────────────
        print(f"── Per-post approach (sample {args.sample} posts/bucket) ──")
        sampled = sample_posts(conn, args.sample)
        print()

        print("Fitting per-post decay curves …")
        beta_by_bucket = fit_per_post(conn, sampled, min_events=20)
        for label in [b[2] for b in BUCKETS]:
            betas = beta_by_bucket.get(label, [])
            if betas:
                b_arr = np.array(betas)
                print(f"  {label}: {len(betas)} fits  |  "
                      f"median β={np.median(b_arr):.4f}  "
                      f"mean={np.mean(b_arr):.4f}  "
                      f"std={np.std(b_arr):.4f}")
            else:
                print(f"  {label}: no successful fits")
        print()

        # ── Plots ───────────────────────────────────────────────────────
        print("Generating plots …")
        if bc is not None and cc is not None:
            plot_aggregate(bc, cc, beta_agg,
                           RESULTS / "temporal_decay_aggregate.png")
        if any(len(v) > 0 for v in beta_by_bucket.values()):
            plot_per_post(beta_by_bucket,
                          RESULTS / "temporal_decay_per_post.png")
        print()
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
