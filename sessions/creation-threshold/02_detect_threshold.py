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
Detect the fixed session threshold via the Kneedle elbow method.

Replicates Kooti et al., SocInfo 2016:
  1. Sample N users from pau_db.user_core_events_dominant (101–500 events).
  2. Compute inter-arrival gaps (Δt = t_{n+1} − t_n) for each user.
  3. Histogram all gaps (0–3600 s, 10 s bins).
  4. Kneedle algorithm finds the point of maximum curvature → session threshold.

Result: Δt = 265 s (4.4 min) — used by 03_cluster_fixed.py.

Usage:
    uv run session-creation-threshold/02_detect_threshold.py
"""

import argparse
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
    "database": _ENV.get("DATABASE_NAME", "bsky"),
    "charset": "utf8mb4",
}

OUT_DIR = Path(__file__).resolve().parent / "results"
BIN_WIDTH_S = 10
MAX_GAP_S = 3600


# ---------------------------------------------------------------------------
# Sampling & fetching
# ---------------------------------------------------------------------------

SAMPLE_DIDS_SQL = """
SELECT did FROM {source_table} GROUP BY did ORDER BY RAND() LIMIT %s
"""

FETCH_EVENTS_SQL = """
SELECT did, time_us, event_type
FROM {source_table}
WHERE did IN ({placeholders})
ORDER BY did, time_us
"""

FETCH_BATCH = 5000  # stay under StarRocks' IN-clause limit


def sample_dids(conn: pymysql.Connection, n: int, source_table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(SAMPLE_DIDS_SQL.format(source_table=source_table), (n,))
        return [row[0] for row in cur]


def fetch_events(conn: pymysql.Connection, dids: list[str], source_table: str) -> pl.DataFrame:
    if not dids:
        return pl.DataFrame(schema={"did": pl.Utf8, "time_us": pl.Int64, "event_type": pl.Utf8})

    all_rows: list[tuple] = []
    total = (len(dids) + FETCH_BATCH - 1) // FETCH_BATCH

    with conn.cursor() as cur:
        for bi in range(0, len(dids), FETCH_BATCH):
            batch = dids[bi : bi + FETCH_BATCH]
            ph = ",".join(["%s"] * len(batch))
            cur.execute(FETCH_EVENTS_SQL.format(placeholders=ph, source_table=source_table), batch)
            all_rows.extend(cur.fetchall())
            bn = bi // FETCH_BATCH + 1
            if bn % 5 == 0 or bn == total:
                print(f"    batch {bn}/{total} ({len(all_rows):,} events)", file=sys.stderr)

    return pl.DataFrame(all_rows, schema=["did", "time_us", "event_type"], orient="row")


# ---------------------------------------------------------------------------
# Gaps & elbow
# ---------------------------------------------------------------------------

def compute_gaps(df: pl.DataFrame) -> pl.Series:
    return (
        df.sort(["did", "time_us"])
        .with_columns((pl.col("time_us").diff().over("did") / 1_000_000.0).alias("gap_s"))
        .filter(pl.col("gap_s").is_not_null())
        .filter(pl.col("gap_s") <= MAX_GAP_S)
        .filter(pl.col("gap_s") >= 0)
        .get_column("gap_s")
    )


def detect_elbow(gaps: pl.Series, bin_width_s: int = BIN_WIDTH_S) -> tuple[float, np.ndarray, np.ndarray]:
    gaps_np = gaps.to_numpy()
    bins = np.arange(0, MAX_GAP_S + bin_width_s, bin_width_s)
    counts, _ = np.histogram(gaps_np, bins=bins)
    centers = (bins[:-1] + bins[1:]) / 2

    kneedle = KneeLocator(centers, counts, curve="convex", direction="decreasing", S=1.0)
    elbow_s = kneedle.knee

    if elbow_s is None:
        kneedle = KneeLocator(centers, counts, curve="convex", direction="decreasing",
                              S=1.0, interp_method="polynomial")
        elbow_s = kneedle.knee

    if elbow_s is None:
        print("WARNING: KneeLocator failed. Falling back to 600 s.", file=sys.stderr)
        elbow_s = 600.0

    return float(elbow_s), centers, counts


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

def plot_histogram(centers, counts, elbow_s, output_path: Path):
    sns.set_theme(style="whitegrid", context="talk")
    fig, ax = plt.subplots(figsize=(14, 7))

    ax.bar(centers, counts, width=BIN_WIDTH_S, color="#4A90D9", alpha=0.85, edgecolor="none",
           label="Inter-arrival gaps")
    ax.axvline(x=elbow_s, color="#D94A4A", linewidth=3, linestyle="--",
               label=f"Elbow = {elbow_s:.0f} s ({elbow_s / 60:.1f} min)")

    # Zoom: first 300 s
    inset = ax.inset_axes([0.55, 0.50, 0.40, 0.40])
    m = centers <= 300
    inset.bar(centers[m], counts[m], width=BIN_WIDTH_S, color="#4A90D9", alpha=0.85, edgecolor="none")
    inset.axvline(x=elbow_s, color="#D94A4A", linewidth=2, linestyle="--")
    inset.set_title("Zoom: 0–5 min", fontsize=10)
    inset.tick_params(labelsize=8)

    ax.set_xlabel("Inter-arrival gap (seconds)")
    ax.set_ylabel("Count")
    ax.set_title(f"Inter-arrival gap distribution — dominant stratum (101–500 events)\n"
                 f"Session threshold = {elbow_s:.0f} s ({elbow_s / 60:.1f} min)")
    ax.set_xlim(0, MAX_GAP_S)
    ax.legend(fontsize=12)
    ax.tick_params(labelsize=12)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot → {output_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Detect session threshold via elbow method")
    parser.add_argument("--source-table", default="pau_db.user_core_events_dominant")
    parser.add_argument("--output", default=None, help="Plot path (default: results/session_elbow.png)")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else (OUT_DIR / "session_elbow.png")

    print(f"Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']} ...", file=sys.stderr)
    conn = pymysql.connect(**DB_CONFIG)

    # Sample all DIDs from the dominant table (~96K)
    t0 = time_mod.time()
    print(f"Sampling all DIDs from {args.source_table} ...", file=sys.stderr)
    dids = sample_dids(conn, 200_000, args.source_table)  # 200K > 96K → gets all
    print(f"  → {len(dids):,} DIDs in {time_mod.time() - t0:.1f}s", file=sys.stderr)

    print("Fetching events ...", file=sys.stderr)
    t1 = time_mod.time()
    df = fetch_events(conn, dids, args.source_table)
    conn.close()
    print(f"  → {len(df):,} events ({df['did'].n_unique():,} DIDs) in {time_mod.time() - t1:.1f}s", file=sys.stderr)

    print("Computing gaps ...", file=sys.stderr)
    t2 = time_mod.time()
    gaps = compute_gaps(df)
    print(f"  → {len(gaps):,} gaps in {time_mod.time() - t2:.1f}s", file=sys.stderr)
    print(f"  →  Mean: {gaps.mean():.1f}s  Median: {gaps.median():.1f}s", file=sys.stderr)

    print("Detecting elbow ...", file=sys.stderr)
    elbow_s, centers, counts = detect_elbow(gaps)
    print(f"  →  {elbow_s:.0f} s ({elbow_s / 60:.1f} min)", file=sys.stderr)

    print("Plotting ...", file=sys.stderr)
    plot_histogram(centers, counts, elbow_s, output_path)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  SESSION THRESHOLD:  {elbow_s:.0f} s  ({elbow_s / 60:.1f} min)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
