#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "polars",
#     "matplotlib",
#     "seaborn",
#     "kneed",
#     "numpy",
# ]
# ///
"""
Empirical session-threshold detection - elbow method on inter-arrival gaps.

Replicates the methodology from:
  "A Study of Tweet Sessions: How Many Tweets Does It Take to Make a Session?"
  Kooti et al., SocInfo 2016 - https://link.springer.com/chapter/10.1007/978-3-319-47874-6_6

Method:
  1. Sample N random users from pau_db.user_core_events.
  2. For each user, sort their events by time and compute Δt = t_{n+1} - t_n
     (inter-arrival gap in seconds).
  3. Build a histogram of all gaps (0-3600 s, fine bins).
  4. Apply the Kneedle algorithm to locate the elbow - the point where the
     distribution transitions from steep decline (within-session bursts) to
     a flat tail (between-session gaps).  That gap duration is the recommended
     session threshold.

Usage:
    uv run session-analysis/session_threshold_elbow.py --sample 300000
"""

import argparse
import os
import sys
import time as time_mod
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import pymysql
import seaborn as sns
from kneed import KneeLocator


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_env_file():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        print(f"ERROR: .env not found at {env_path}", file=sys.stderr)
        sys.exit(1)
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val and key not in os.environ:
                os.environ[key] = val


_load_env_file()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


DB_CONFIG = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": _env("DATABASE_NAME", "bsky"),
    "charset": "utf8mb4",
}

OUT_DIR = Path(__file__).resolve().parent / "results"
BIN_WIDTH_S = 10          # histogram bin width in seconds
MAX_GAP_S = 3600           # 60 minutes - per the study methodology
S_CURVE_SMOOTH = "s_curve"  # knee detection sensitivity (higher = coarser)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

SAMPLE_DIDS_SQL = """
SELECT did
FROM {source_table}
GROUP BY did
ORDER BY RAND()
LIMIT %s
"""

FETCH_EVENTS_SQL = """
SELECT did, time_us, event_type
FROM {source_table}
WHERE did IN ({placeholders})
ORDER BY did, time_us
"""


def sample_dids(conn: pymysql.Connection, n: int, source_table: str) -> list[str]:
    """Sample n random DIDs from the source table."""
    with conn.cursor() as cur:
        cur.execute(SAMPLE_DIDS_SQL.format(source_table=source_table), (n,))
        return [row[0] for row in cur]


FETCH_BATCH = 5000  # StarRocks limits IN-clause children to 10,000


def fetch_events(conn: pymysql.Connection, dids: list[str], source_table: str) -> pl.DataFrame:
    """Fetch all core events for a list of DIDs, returned as a Polars DataFrame.

    Batches the IN clause to stay under StarRocks' expr_children_limit (10,000).
    """
    if not dids:
        return pl.DataFrame(schema={"did": pl.Utf8, "time_us": pl.Int64, "event_type": pl.Utf8})

    all_rows: list[tuple] = []
    total_batches = (len(dids) + FETCH_BATCH - 1) // FETCH_BATCH

    with conn.cursor() as cur:
        for batch_idx in range(0, len(dids), FETCH_BATCH):
            batch_dids = dids[batch_idx:batch_idx + FETCH_BATCH]
            placeholders = ",".join(["%s"] * len(batch_dids))
            sql = FETCH_EVENTS_SQL.format(placeholders=placeholders, source_table=source_table)
            cur.execute(sql, batch_dids)
            all_rows.extend(cur.fetchall())
            bn = batch_idx // FETCH_BATCH + 1
            if bn % 5 == 0 or bn == total_batches:
                print(f"    batch {bn}/{total_batches} ({len(all_rows):,} events so far)", file=sys.stderr)

    return pl.DataFrame(
        all_rows,
        schema=["did", "time_us", "event_type"],
        orient="row",
    )


# ---------------------------------------------------------------------------
# Gap computation
# ---------------------------------------------------------------------------

def compute_gaps(df: pl.DataFrame) -> pl.Series:
    """Compute inter-arrival gaps (seconds) per user.

    Returns a Series of all gaps across all users (first event per user
    produces no gap and is excluded).
    """
    gaps = (
        df
        .sort(["did", "time_us"])
        .with_columns(
            (pl.col("time_us").diff().over("did") / 1_000_000.0).alias("gap_s")
        )
        .filter(pl.col("gap_s").is_not_null())
        .filter(pl.col("gap_s") <= MAX_GAP_S)
        .filter(pl.col("gap_s") >= 0)
        .get_column("gap_s")
    )
    return gaps


# ---------------------------------------------------------------------------
# Elbow detection
# ---------------------------------------------------------------------------

def detect_elbow(gaps: pl.Series, bin_width_s: int = BIN_WIDTH_S) -> tuple[float, np.ndarray, np.ndarray]:
    """Build histogram of gaps, find the elbow via the Kneedle algorithm.

    Returns (elbow_x_seconds, bin_centers, counts).
    """
    gaps_np = gaps.to_numpy()

    bins = np.arange(0, MAX_GAP_S + bin_width_s, bin_width_s)
    counts, bin_edges = np.histogram(gaps_np, bins=bins)
    centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    # Kneedle: find the point of maximum curvature on the decreasing curve
    kneedle = KneeLocator(
        centers,
        counts,
        curve="convex",
        direction="decreasing",
        S=1.0,
        interp_method="interp1d",
    )

    elbow_s = kneedle.knee
    if elbow_s is None:
        # Fallback: try polynomial interpolation instead
        kneedle = KneeLocator(
            centers,
            counts,
            curve="convex",
            direction="decreasing",
            S=1.0,
            interp_method="polynomial",
        )
        elbow_s = kneedle.knee

    if elbow_s is None:
        print("WARNING: KneeLocator could not find an elbow. Falling back to 600 s (10 min).", file=sys.stderr)
        elbow_s = 600.0

    return float(elbow_s), centers, counts


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_histogram(
    centers: np.ndarray,
    counts: np.ndarray,
    elbow_s: float,
    sample_n: int,
    output_path: Path,
):
    """Plot the gap histogram with the detected elbow marked."""
    sns.set_theme(style="whitegrid", context="talk")
    fig, ax = plt.subplots(figsize=(14, 7))

    # Main histogram
    ax.bar(centers, counts, width=BIN_WIDTH_S, color="#4A90D9", alpha=0.85, edgecolor="none",
           label="Inter-arrival gaps (all users)")

    # Elbow line
    ax.axvline(x=elbow_s, color="#D94A4A", linewidth=3, linestyle="--",
               label=f"Elbow / session threshold  =  {elbow_s:.0f} s  ({elbow_s / 60:.1f} min)")

    # Zoom inset: first 300 s for burst detail
    inset_ax = ax.inset_axes([0.55, 0.50, 0.40, 0.40])
    mask_300 = centers <= 300
    inset_ax.bar(centers[mask_300], counts[mask_300], width=BIN_WIDTH_S,
                 color="#4A90D9", alpha=0.85, edgecolor="none")
    inset_ax.axvline(x=elbow_s, color="#D94A4A", linewidth=2, linestyle="--")
    inset_ax.set_title("Zoom: 0-5 min", fontsize=10)
    inset_ax.set_xlabel("Gap (s)", fontsize=9)
    inset_ax.set_ylabel("Count", fontsize=9)
    inset_ax.tick_params(labelsize=8)

    ax.set_xlabel("Inter-arrival gap (seconds)", fontsize=14)
    ax.set_ylabel("Number of consecutive event pairs", fontsize=14)
    ax.set_title(
        f"Inter-arrival gap distribution - {sample_n:,} users sampled\n"
        f"Session threshold (elbow) = {elbow_s:.0f} s ({elbow_s / 60:.1f} min)",
        fontsize=16, fontweight="bold",
    )
    ax.set_xlim(0, MAX_GAP_S)
    ax.legend(fontsize=12, loc="upper right")
    ax.tick_params(labelsize=12)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot saved to {output_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Empirical session-threshold detection via elbow method"
    )
    parser.add_argument(
        "--sample", type=int, default=175_000,
        help="Number of random users to sample (default: 175000, 10%% of 1.75M)",
    )
    parser.add_argument(
        "--bin-width", type=int, default=BIN_WIDTH_S,
        help=f"Histogram bin width in seconds (default: {BIN_WIDTH_S})",
    )
    parser.add_argument(
        "--max-gap", type=int, default=MAX_GAP_S,
        help=f"Maximum gap to consider in seconds (default: {MAX_GAP_S})",
    )
    parser.add_argument(
        "--min-events", type=int, default=0,
        help="Only include users with ≥ N events (default: 0 = all users)",
    )
    parser.add_argument(
        "--max-events", type=int, default=0,
        help="Exclude users with > N events, e.g. 400 to cut bots (default: 0 = no cap)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output path for the plot (default: results/session_elbow_<sample>.png)",
    )
    parser.add_argument(
        "--source-table", type=str, default="pau_db.user_core_events_dominant",
        help="Source table: user_core_events (all), user_core_events_dominant (101-500, default), "
             "or user_core_events_human (6-500)",
    )
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else (
        OUT_DIR / f"session_elbow_{args.sample}.png"
    )

    # -------------------------------------------------------------------
    # 1. Connect
    # -------------------------------------------------------------------
    print(f"Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']} ...", file=sys.stderr)
    conn = pymysql.connect(**DB_CONFIG)

    # -------------------------------------------------------------------
    # 2. Sample DIDs
    # -------------------------------------------------------------------
    t0 = time_mod.time()
    print(f"Sampling {args.sample:,} random DIDs from {args.source_table} ...", file=sys.stderr)
    dids = sample_dids(conn, args.sample, args.source_table)
    print(f"  → {len(dids):,} DIDs sampled in {time_mod.time() - t0:.1f}s", file=sys.stderr)

    # -------------------------------------------------------------------
    # 3. Fetch events
    # -------------------------------------------------------------------
    t1 = time_mod.time()
    print("Fetching events for sampled DIDs ...", file=sys.stderr)
    df = fetch_events(conn, dids, args.source_table)
    conn.close()
    print(f"  → {len(df):,} events fetched in {time_mod.time() - t1:.1f}s", file=sys.stderr)
    print(f"  → {df['did'].n_unique():,} unique DIDs with events", file=sys.stderr)

    # -------------------------------------------------------------------
    # 3b. Filter by event count (optional)
    # -------------------------------------------------------------------
    if args.min_events > 0 or args.max_events > 0:
        event_counts = df.group_by("did").agg(pl.len().alias("n"))
        if args.min_events > 0:
            keep = event_counts.filter(pl.col("n") >= args.min_events)["did"]
            df = df.filter(pl.col("did").is_in(keep))
        if args.max_events > 0:
            keep = event_counts.filter(pl.col("n") <= args.max_events)["did"]
            df = df.filter(pl.col("did").is_in(keep))
        mn = args.min_events if args.min_events > 0 else 1
        mx = args.max_events if args.max_events > 0 else "∞"
        print(f"  → {df['did'].n_unique():,} DIDs with {mn}-{mx} events "
              f"({len(df):,} events)", file=sys.stderr)

    # -------------------------------------------------------------------
    # 4. Compute gaps
    # -------------------------------------------------------------------
    t2 = time_mod.time()
    print("Computing inter-arrival gaps (polars) ...", file=sys.stderr)
    gaps = compute_gaps(df)
    print(f"  → {len(gaps):,} gaps in {time_mod.time() - t2:.1f}s", file=sys.stderr)
    print(f"  →  Mean gap: {gaps.mean():.1f}s  |  Median: {gaps.median():.1f}s", file=sys.stderr)

    # -------------------------------------------------------------------
    # 5. Detect elbow
    # -------------------------------------------------------------------
    t3 = time_mod.time()
    print("Detecting elbow (Kneedle algorithm) ...", file=sys.stderr)
    elbow_s, centers, counts = detect_elbow(gaps, args.bin_width)
    print(f"  →  Elbow at {elbow_s:.0f} s  ({elbow_s / 60:.1f} min)", file=sys.stderr)
    print(f"  →  {elbow_s / 60:.2f} minutes", file=sys.stderr)

    # -------------------------------------------------------------------
    # 6. Plot
    # -------------------------------------------------------------------
    t4 = time_mod.time()
    print("Plotting ...", file=sys.stderr)
    plot_histogram(centers, counts, elbow_s, args.sample, output_path)

    total_elapsed = time_mod.time() - t0
    print(f"\nDone in {total_elapsed:.0f}s", file=sys.stderr)
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  RECOMMENDED SESSION THRESHOLD:  {elbow_s:.0f} seconds  ({elbow_s / 60:.1f} minutes)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
