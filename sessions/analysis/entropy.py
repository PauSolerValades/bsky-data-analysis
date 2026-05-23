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
Per-user time-interval entropy — automated-account detection.

Computes Shannon entropy over the distribution of inter-arrival gaps for every
user in pau_db.user_core_events.  Low entropy → highly regular posting
intervals (suggesting automation).  High entropy → irregular/varied gaps
(suggesting organic human behaviour).

Formula (Kooti et al., SocInfo 2016):
    p(Δt_i) = n_{Δt_i} / N                    -- probability of gap value i
    H_Δt    = −Σ p(Δt_i) · log₂(p(Δt_i))       -- Shannon entropy (bits)

Gaps are rounded to the nearest second so repeated patterns collapse into a
single symbol.

By default processes ALL users and writes results to pau_db.user_time_entropy.
Use --sample N to run on a random subset instead (faster for exploration).

Usage:
    # All users → DB (default)
    uv run sessions/analysis/entropy.py

    # Sample for quick exploration + plot
    uv run sessions/analysis/entropy.py --sample 50000 --plot
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
FETCH_BATCH = 5000         # StarRocks IN-clause limit
PROCESS_BATCH = 10_000     # DIDs per processing batch (fetch → compute → insert)
INSERT_FLUSH = 10_000      # rows per DB insert batch (StarRocks limit)
GAP_ROUND_S = 1            # round gaps to nearest second for entropy symbols
MIN_GAPS_PER_USER = 5      # skip users with too few gaps


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

ALL_DIDS_SQL = """
SELECT did
FROM pau_db.user_core_events
GROUP BY did
ORDER BY did
"""

SAMPLE_DIDS_SQL = """
SELECT did
FROM pau_db.user_core_events
GROUP BY did
ORDER BY RAND()
LIMIT %s
"""

FETCH_EVENTS_SQL = """
SELECT did, time_us
FROM pau_db.user_core_events
WHERE did IN ({placeholders})
ORDER BY did, time_us
"""

CREATE_ENTROPY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pau_db.user_time_entropy (
    `did`             varchar(128) NOT NULL,
    `entropy_bits`    double NOT NULL,
    `num_gaps`        int NOT NULL,
    `num_unique_gaps` int NOT NULL,
    `is_automated`    tinyint NOT NULL
) ENGINE = OLAP
DUPLICATE KEY(`did`)
DISTRIBUTED BY HASH(`did`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1"
);
"""

INSERT_ENTROPY_SQL = """
INSERT INTO pau_db.user_time_entropy
    (did, entropy_bits, num_gaps, num_unique_gaps, is_automated)
VALUES
    (%s, %s, %s, %s, %s)
"""


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_all_dids(conn: pymysql.Connection) -> list[str]:
    """Return every DID in pau_db.user_core_events."""
    with conn.cursor() as cur:
        cur.execute(ALL_DIDS_SQL)
        return [row[0] for row in cur]


def sample_dids(conn: pymysql.Connection, n: int) -> list[str]:
    """Return n random DIDs."""
    with conn.cursor() as cur:
        cur.execute(SAMPLE_DIDS_SQL, (n,))
        return [row[0] for row in cur]


def fetch_events(conn: pymysql.Connection, dids: list[str]) -> pl.DataFrame:
    """Fetch time_us for a list of DIDs in batches."""
    if not dids:
        return pl.DataFrame(schema={"did": pl.Utf8, "time_us": pl.Int64})

    all_rows: list[tuple] = []
    total_batches = (len(dids) + FETCH_BATCH - 1) // FETCH_BATCH

    with conn.cursor() as cur:
        for batch_idx in range(0, len(dids), FETCH_BATCH):
            batch_dids = dids[batch_idx:batch_idx + FETCH_BATCH]
            placeholders = ",".join(["%s"] * len(batch_dids))
            sql = FETCH_EVENTS_SQL.format(placeholders=placeholders)
            cur.execute(sql, batch_dids)
            all_rows.extend(cur.fetchall())


    return pl.DataFrame(all_rows, schema=["did", "time_us"], orient="row")


# ---------------------------------------------------------------------------
# Entropy computation
# ---------------------------------------------------------------------------

def compute_user_entropy(df: pl.DataFrame, gap_round_s: int = GAP_ROUND_S) -> pl.DataFrame:
    """Compute Shannon entropy (bits) of inter-arrival gaps for each user.

    Returns DataFrame: did, entropy_bits, num_gaps, num_unique_gaps.
    """
    gaps = (
        df
        .sort(["did", "time_us"])
        .with_columns(
            (pl.col("time_us").diff().over("did") / 1_000_000.0)
            .round(gap_round_s)
            .cast(pl.Int64)
            .alias("gap_s")
        )
        .filter(pl.col("gap_s").is_not_null())
        .filter(pl.col("gap_s") >= 0)
    )

    gap_counts = gaps.group_by(["did", "gap_s"]).agg(pl.len().alias("n_i"))

    totals = gap_counts.group_by("did").agg(pl.sum("n_i").alias("N"))

    entropy = (
        gap_counts
        .join(totals, on="did")
        .filter(pl.col("N") >= MIN_GAPS_PER_USER)
        .with_columns((pl.col("n_i") / pl.col("N")).alias("p_i"))
        .with_columns((-pl.col("p_i") * pl.col("p_i").log(base=2)).alias("h_i"))
        .group_by("did")
        .agg([
            pl.sum("h_i").alias("entropy_bits"),
            pl.first("N").alias("num_gaps"),
            pl.len().alias("num_unique_gaps"),
        ])
        .sort("entropy_bits")
    )

    return entropy


# ---------------------------------------------------------------------------
# Automated-account detection
# ---------------------------------------------------------------------------

def find_entropy_threshold(entropy_df: pl.DataFrame) -> float:
    """Kneedle algorithm on the entropy histogram to split automated from human."""
    entropies = entropy_df["entropy_bits"].to_numpy()
    if len(entropies) < 10:
        return 1.0

    bins = np.linspace(0, entropies.max(), min(200, len(entropies) // 10))
    counts, edges = np.histogram(entropies, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2

    nonzero = counts > 0
    if not nonzero.any():
        return 1.0
    first_nonzero = np.argmax(nonzero)
    x = centers[first_nonzero:]
    y = counts[first_nonzero:]

    if len(x) < 5:
        return float(x[0])

    try:
        kneedle = KneeLocator(x, y, curve="concave", direction="increasing",
                              S=1.0, interp_method="interp1d")
        threshold = kneedle.knee
        if threshold is None:
            kneedle = KneeLocator(x, y, curve="concave", direction="increasing",
                                  S=1.0, interp_method="polynomial")
            threshold = kneedle.knee
        if threshold is None:
            threshold = 1.0
    except Exception:
        threshold = 1.0

    return float(threshold)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_entropy_distribution(
    entropy_df: pl.DataFrame,
    threshold: float,
    sample_n: int,
    output_path: Path,
):
    """Plot entropy histogram with automated/human threshold marked."""
    entropies = entropy_df["entropy_bits"].to_numpy()
    n_automated = int((entropies < threshold).sum())
    n_human = len(entropies) - n_automated
    max_entropy = entropy_df["entropy_bits"].max()

    sns.set_theme(style="whitegrid", context="talk")
    fig, axes = plt.subplots(1, 2, figsize=(18, 7))

    # Panel 1: Full distribution
    ax1 = axes[0]
    bins = np.linspace(0, max_entropy, 120)
    ax1.hist(entropies, bins=bins, color="#4A90D9", alpha=0.85, edgecolor="none",
             label=f"All users (n={len(entropies):,})")
    ax1.axvline(x=threshold, color="#D94A4A", linewidth=3, linestyle="--",
                label=(f"Automation threshold = {threshold:.2f} bits\n"
                       f"Automated: {n_automated:,} ({100*n_automated/len(entropies):.1f}%)\n"
                       f"Human:     {n_human:,} ({100*n_human/len(entropies):.1f}%)"))
    ax1.set_xlabel("Time-interval entropy (bits)", fontsize=13)
    ax1.set_ylabel("Number of users", fontsize=13)
    ax1.set_title(f"Entropy distribution — {sample_n:,} users", fontsize=15, fontweight="bold")
    ax1.legend(fontsize=10, loc="upper right")
    ax1.tick_params(labelsize=11)

    # Panel 2: Zoom on low-entropy region
    ax2 = axes[1]
    zoom_max = min(threshold * 2.5, max_entropy)
    zoom_bins = np.linspace(0, zoom_max, 80)
    ax2.hist(entropies[entropies <= zoom_max], bins=zoom_bins,
             color="#4A90D9", alpha=0.85, edgecolor="none")
    ax2.axvline(x=threshold, color="#D94A4A", linewidth=3, linestyle="--",
                label=f"Threshold = {threshold:.2f} bits")
    ax2.set_xlabel("Time-interval entropy (bits)", fontsize=13)
    ax2.set_ylabel("Number of users", fontsize=13)
    ax2.set_title("Zoom: low-entropy region", fontsize=14, fontweight="bold")
    ax2.legend(fontsize=11, loc="upper right")
    ax2.tick_params(labelsize=11)

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
        description="Per-user time-interval entropy — all users, saved to DB"
    )
    parser.add_argument(
        "--sample", type=int, default=0,
        help="Process only N random users instead of all (0 = all, default)",
    )
    parser.add_argument(
        "--plot", action="store_true",
        help="Generate entropy distribution plot",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output path for the plot",
    )
    args = parser.parse_args()

    # -------------------------------------------------------------------
    # 1. Connect & create table
    # -------------------------------------------------------------------
    print(f"Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']} ...", file=sys.stderr)
    conn = pymysql.connect(**DB_CONFIG)
    with conn.cursor() as cur:
        cur.execute(CREATE_ENTROPY_TABLE_SQL)
    conn.commit()
    print("Table pau_db.user_time_entropy ready.", file=sys.stderr)

    # -------------------------------------------------------------------
    # 2. Get DIDs
    # -------------------------------------------------------------------
    t0 = time_mod.time()
    if args.sample > 0:
        print(f"Sampling {args.sample:,} random DIDs ...", file=sys.stderr)
        all_dids = sample_dids(conn, args.sample)
    else:
        print("Loading ALL DIDs from pau_db.user_core_events ...", file=sys.stderr)
        all_dids = load_all_dids(conn)
    print(f"  → {len(all_dids):,} DIDs ({time_mod.time() - t0:.1f}s)", file=sys.stderr)

    # -------------------------------------------------------------------
    # 3. Process in batches: fetch → compute → insert
    # -------------------------------------------------------------------
    total_dids = len(all_dids)
    n_batches = (total_dids + PROCESS_BATCH - 1) // PROCESS_BATCH
    insert_buffer: list[tuple] = []
    all_entropies: list[float] = []

    t1 = time_mod.time()
    for bn in range(0, total_dids, PROCESS_BATCH):
        batch_dids = all_dids[bn:bn + PROCESS_BATCH]
        bi = bn // PROCESS_BATCH + 1

        # Fetch events for this batch of DIDs
        df = fetch_events(conn, batch_dids)

        # Compute entropy and queue insert rows
        batch_rows: list[tuple] = []
        if not df.is_empty():
            entropy_df = compute_user_entropy(df)
            if not entropy_df.is_empty():
                for row in entropy_df.iter_rows(named=True):
                    batch_rows.append((
                        row["did"], row["entropy_bits"],
                        row["num_gaps"], row["num_unique_gaps"], 0,
                    ))
                    all_entropies.append(row["entropy_bits"])

        # Flush immediately (each batch is well under StarRocks 10K row limit)
        if batch_rows:
            with conn.cursor() as cur:
                cur.executemany(INSERT_ENTROPY_SQL, batch_rows)
            conn.commit()

        if bi % 10 == 0 or bi == n_batches:
            elapsed = time_mod.time() - t1
            rate = bi * PROCESS_BATCH / elapsed if elapsed > 0 else 0
            print(f"  batch {bi}/{n_batches}  "
                  f"({len(all_entropies):,} users, ~{rate:.0f} DIDs/s)",
                  file=sys.stderr)

    print(f"\n  → {len(all_entropies):,} users written to DB "
          f"({time_mod.time() - t1:.0f}s)", file=sys.stderr)

    # -------------------------------------------------------------------
    # 4. Detect threshold from accumulated entropies
    # -------------------------------------------------------------------
    if not all_entropies:
        print("ERROR: no users with sufficient gaps found.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    entropies_arr = np.array(all_entropies)
    print(f"\nEntropy stats ({len(entropies_arr):,} users):", file=sys.stderr)
    for pct, label in [(0, "Min"), (1, "P1"), (5, "P5"), (10, "P10"),
                        (50, "Median"), (90, "P90"), (99, "P99"), (100, "Max")]:
        if pct == 0:      val = entropies_arr.min()
        elif pct == 100:  val = entropies_arr.max()
        else:             val = np.percentile(entropies_arr, pct)
        print(f"    {label:>6}: {val:.4f}", file=sys.stderr)

    print("\nDetecting automated/human threshold (Kneedle) ...", file=sys.stderr)
    edf = pl.DataFrame({"entropy_bits": entropies_arr})
    threshold = find_entropy_threshold(edf)
    n_auto = int((entropies_arr < threshold).sum())
    print(f"  →  Threshold: {threshold:.4f} bits", file=sys.stderr)
    print(f"  →  Automated: {n_auto:,} / {len(entropies_arr):,}  "
          f"({100*n_auto/len(entropies_arr):.2f}%)", file=sys.stderr)

    # -------------------------------------------------------------------
    # 5. Report (UPDATE not supported on DUPLICATE KEY tables)
    # -------------------------------------------------------------------
    print(f"\nTo find automated users, run:", file=sys.stderr)
    print(f"  SELECT * FROM pau_db.user_time_entropy WHERE entropy_bits < {threshold:.4f};", file=sys.stderr)

    conn.close()

    # -------------------------------------------------------------------
    # 6. Plot (optional)
    # -------------------------------------------------------------------
    if args.plot:
        label = f"all_{len(entropies_arr)}" if args.sample == 0 else str(args.sample)
        output_path = Path(args.output) if args.output else (
            OUT_DIR / f"user_entropy_{label}.png"
        )
        print("\nPlotting ...", file=sys.stderr)
        plot_entropy_distribution(edf, threshold, len(entropies_arr), output_path)

    total_elapsed = time_mod.time() - t0
    print(f"\n{'='*65}", file=sys.stderr)
    print(f"  ENTROPY THRESHOLD:  {threshold:.4f} bits", file=sys.stderr)
    print(f"  Automated users:    {n_auto:,}  ({100*n_auto/len(entropies_arr):.2f}%)", file=sys.stderr)
    print(f"  Human users:        {len(entropies_arr) - n_auto:,}", file=sys.stderr)
    print(f"  Total time:         {total_elapsed:.0f}s", file=sys.stderr)
    print(f"  Table:              pau_db.user_time_entropy", file=sys.stderr)
    print(f"{'='*65}", file=sys.stderr)


if __name__ == "__main__":
    main()
