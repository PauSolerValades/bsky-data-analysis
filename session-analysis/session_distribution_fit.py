#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "polars",
#     "numpy",
#     "scipy",
#     "powerlaw",
# ]
# ///
"""
Per-user distribution fitting for session durations and inter-session gaps.

For each sampled user, fits five candidate distributions to both quantities:
  - power-law (with KS-based xmin estimation)
  - exponential
  - log-normal
  - Weibull
  - gamma

Compares via log-likelihood ratio tests (powerlaw vs each) and AIC, then
reports what fraction of users follow each distribution type, plus parameter
distributions for the dominant fits.

Uses both sessions_threshold_total (fixed 265s) and sessions_tukey (adaptive).

Usage:
    uv run session-analysis/session_distribution_fit.py --sample 50000 --output-dir results/
"""

import argparse
import json
import os
import sys
import time as time_mod
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl
import pymysql
from scipy import stats

# ---------------------------------------------------------------------------
# Config
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
    "database": _env("DATABASE_NAME", "bsky"),
    "charset": "utf8mb4",
}

MIN_DATA_POINTS = 10  # minimum sessions/gaps per user to attempt fitting
SIGNIFICANCE = 0.05    # p-value threshold for LLR tests

DISTRIBUTION_NAMES = ["powerlaw", "exponential", "lognormal", "weibull", "gamma"]


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def sample_dids(conn: pymysql.Connection, table: str, n: int) -> list[str]:
    """Sample n random DIDs from a sessions table."""
    sql = f"SELECT DISTINCT did FROM pau_db.{table} ORDER BY RAND() LIMIT %s"
    with conn.cursor() as cur:
        cur.execute(sql, (n,))
        return [row[0] for row in cur]


def fetch_durations(conn: pymysql.Connection, table: str, dids: list[str]) -> pl.DataFrame:
    """Fetch per-user session durations."""
    if not dids:
        return pl.DataFrame(schema={"did": pl.Utf8, "duration_s": pl.Float64})
    placeholders = ",".join(["%s"] * len(dids))
    sql = f"""
        SELECT did, duration_s
        FROM pau_db.{table}
        WHERE did IN ({placeholders})
        ORDER BY did, session_start
    """
    with conn.cursor() as cur:
        cur.execute(sql, dids)
        rows = cur.fetchall()
    return pl.DataFrame(rows, schema=["did", "duration_s"], orient="row")


def fetch_gaps(conn: pymysql.Connection, table: str, dids: list[str]) -> pl.DataFrame:
    """Fetch per-user inter-session gaps (seconds)."""
    if not dids:
        return pl.DataFrame(schema={"did": pl.Utf8, "gap_s": pl.Float64})
    placeholders = ",".join(["%s"] * len(dids))
    sql = f"""
        SELECT did, (next_session_start - session_end) / 1000000.0 AS gap_s
        FROM pau_db.{table}
        WHERE did IN ({placeholders})
          AND next_session_start IS NOT NULL
          AND next_session_start > session_end
        ORDER BY did, session_start
    """
    with conn.cursor() as cur:
        cur.execute(sql, dids)
        rows = cur.fetchall()
    return pl.DataFrame(rows, schema=["did", "gap_s"], orient="row")


# ---------------------------------------------------------------------------
# Distribution fitting
# ---------------------------------------------------------------------------

def fit_distributions(data: np.ndarray) -> dict:
    """Fit powerlaw, exponential, lognormal, weibull, gamma to a data vector.

    Returns a dict with fitting results for each distribution.
    """
    if len(data) < MIN_DATA_POINTS or np.all(data <= 0):
        return {}

    result = {"n": len(data)}

    # --- Power-law (with xmin estimation via KS) ---
    try:
        fit = powerlaw.Fit(data, discrete=False, xmin=None, verbose=False)
        result["powerlaw"] = {
            "alpha": fit.alpha,
            "xmin": fit.xmin,
            "sigma": fit.sigma,
            "loglik": fit.power_law.loglikelihoods(data[data >= fit.xmin], noisy=False),
            "n_tail": int(np.sum(data >= fit.xmin)),
            "D": fit.D,
        }
    except Exception:
        result["powerlaw"] = None

    # --- Get tail data (above powerlaw xmin) for comparison ---
    if result.get("powerlaw"):
        xmin = result["powerlaw"]["xmin"]
        tail = data[data >= xmin]
    else:
        tail = data

    if len(tail) < 3:
        return result

    # --- Exponential (MLE: lambda = 1/mean, shifted by xmin) ---
    try:
        shifted = tail - tail.min()
        loc, scale = stats.expon.fit(shifted, floc=0)
        loglik = np.sum(stats.expon.logpdf(shifted, loc=0, scale=scale))
        result["exponential"] = {
            "loc": float(tail.min()),
            "scale": float(scale),
            "loglik": float(loglik),
            "n": len(tail),
        }
    except Exception:
        result["exponential"] = None

    # --- Log-normal (MLE) ---
    try:
        shape, loc, scale = stats.lognorm.fit(tail, floc=0)
        loglik = np.sum(stats.lognorm.logpdf(tail, shape, loc=0, scale=scale))
        result["lognormal"] = {
            "shape": float(shape),
            "loc": 0.0,
            "scale": float(scale),
            "loglik": float(loglik),
            "n": len(tail),
        }
    except Exception:
        result["lognormal"] = None

    # --- Weibull (MLE) ---
    try:
        shape, loc, scale = stats.weibull_min.fit(tail, floc=0)
        loglik = np.sum(stats.weibull_min.logpdf(tail, shape, loc=0, scale=scale))
        result["weibull"] = {
            "shape": float(shape),
            "loc": 0.0,
            "scale": float(scale),
            "loglik": float(loglik),
            "n": len(tail),
        }
    except Exception:
        result["weibull"] = None

    # --- Gamma (MLE) ---
    try:
        shape, loc, scale = stats.gamma.fit(tail, floc=0)
        loglik = np.sum(stats.gamma.logpdf(tail, shape, loc=0, scale=scale))
        result["gamma"] = {
            "shape": float(shape),
            "loc": 0.0,
            "scale": float(scale),
            "loglik": float(loglik),
            "n": len(tail),
        }
    except Exception:
        result["gamma"] = None

    # --- Log-likelihood ratio tests: powerlaw vs each alternative ---
    if result.get("powerlaw") and all(
        result.get(d) is not None for d in ["exponential", "lognormal", "weibull", "gamma"]
    ):
        try:
            fit_pl = fit

            # Use powerlaw package's built-in comparison where available
            for alt_name, alt_class in [
                ("exponential", powerlaw.Exponential),
                ("lognormal", powerlaw.Lognormal),
            ]:
                try:
                    R, p = powerlaw.distribution_compare(
                        fit_pl.power_law, alt_class, normalized_ratio=True
                    )
                    result[f"llr_{alt_name}"] = {"R": float(R), "p": float(p)}
                except Exception:
                    result[f"llr_{alt_name}"] = None

            # For weibull and gamma: manual LLR test
            for alt_name in ["weibull", "gamma"]:
                try:
                    ll_pl = result["powerlaw"]["loglik"]
                    ll_alt = result[alt_name]["loglik"]
                    R = ll_pl - ll_alt  # positive → powerlaw favored
                    # Vuong's test approx: R / sqrt(n * variance_of_logratio)
                    log_ratio = np.log(
                        fit_pl.power_law.pdf(tail) /
                        _pdf(tail, alt_name, result[alt_name])
                    )
                    var = np.var(log_ratio, ddof=1)
                    n = len(tail)
                    if var > 0 and n > 1:
                        z = R / np.sqrt(n * var)
                        # Two-sided p-value from normal approx
                        p = 2 * stats.norm.sf(abs(z))
                    else:
                        z = float("inf") if R > 0 else float("-inf")
                        p = 0.0
                    result[f"llr_{alt_name}"] = {"R": float(R), "z": float(z), "p": float(p)}
                except Exception:
                    result[f"llr_{alt_name}"] = None
    else:
        for alt_name in ["exponential", "lognormal", "weibull", "gamma"]:
            result.setdefault(f"llr_{alt_name}", None)

    return result


def _pdf(x: np.ndarray, dist_name: str, params: dict) -> np.ndarray:
    """PDF for a distribution given its params dict (from fit_distributions)."""
    if dist_name == "weibull":
        return stats.weibull_min.pdf(x, params["shape"], loc=0, scale=params["scale"])
    elif dist_name == "gamma":
        return stats.gamma.pdf(x, params["shape"], loc=0, scale=params["scale"])
    elif dist_name == "exponential":
        return stats.expon.pdf(x - params["loc"], loc=0, scale=params["scale"])
    elif dist_name == "lognormal":
        return stats.lognorm.pdf(x, params["shape"], loc=0, scale=params["scale"])
    else:
        return np.ones_like(x)


def pick_best_distribution(result: dict) -> Optional[str]:
    """Pick the best-fitting distribution using LLR tests + AIC fallback.

    Priority:
    1. If powerlaw is significantly favored over ALL alternatives → powerlaw
    2. If powerlaw is significantly rejected by any alternative → that alternative
    3. Fallback: lowest AIC
    """
    available = [d for d in DISTRIBUTION_NAMES if result.get(d) is not None]
    if not available:
        return None

    # Compute AIC for all available distributions
    aic = {}
    for d in available:
        n_params = 2 if d in ("powerlaw", "lognormal", "weibull", "gamma") else 1
        if d == "powerlaw":
            n_params = 2  # alpha + xmin (estimated)
            # Only count tail data points
            ll = result.get(d, {}).get("loglik", -np.inf)
            k = 2
            n = result.get(d, {}).get("n_tail", result["n"])
        else:
            ll = result.get(d, {}).get("loglik", -np.inf)
            k = n_params
            n = result.get(d, {}).get("n", result["n"])
        aic[d] = 2 * k - 2 * ll if n > 0 else np.inf

    # Check LLR tests: powerlaw vs each alternative
    if result.get("powerlaw") is not None:
        pl_favored = []
        pl_rejected = []
        for alt in ["exponential", "lognormal", "weibull", "gamma"]:
            llr = result.get(f"llr_{alt}")
            if llr is not None and llr.get("p") is not None:
                if llr["R"] > 0 and llr["p"] < SIGNIFICANCE:
                    pl_favored.append(alt)
                elif llr["R"] < 0 and llr["p"] < SIGNIFICANCE:
                    pl_rejected.append(alt)

        # If powerlaw significantly favored over ALL tested alternatives
        if pl_favored and not pl_rejected and len(pl_favored) >= len([a for a in ["exponential", "lognormal", "weibull", "gamma"] if result.get(a) is not None]):
            return "powerlaw"

        # If powerlaw rejected by something, pick the one that rejected it
        if pl_rejected and result.get(pl_rejected[0]) is not None:
            return pl_rejected[0]

    # Fallback: lowest AIC
    if aic:
        return min(aic, key=aic.get)
    return available[0]


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def process_table(
    conn: pymysql.Connection,
    table: str,
    dids: list[str],
    label: str,
) -> pl.DataFrame:
    """Fetch durations + gaps, fit distributions per user, return results DataFrame."""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Processing {label} ({table}) — {len(dids):,} users", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    t0 = time_mod.time()

    # Fetch
    print("  Fetching durations ...", file=sys.stderr)
    dur_df = fetch_durations(conn, table, dids)
    print(f"    → {len(dur_df):,} duration rows", file=sys.stderr)

    print("  Fetching inter-session gaps ...", file=sys.stderr)
    gap_df = fetch_gaps(conn, table, dids)
    print(f"    → {len(gap_df):,} gap rows", file=sys.stderr)

    # Group into per-user arrays
    dur_groups = dur_df.group_by("did").agg(pl.col("duration_s"))
    gap_groups = gap_df.group_by("did").agg(pl.col("gap_s"))

    # Merge
    joined = dur_groups.join(gap_groups, on="did", how="outer")

    results = []
    total = len(joined)
    n_fitted_dur = 0
    n_fitted_gap = 0

    for idx, row in enumerate(joined.iter_rows(named=True)):
        did = row["did"]
        dur_arr = np.array(row["duration_s"], dtype=float) if row["duration_s"] is not None else np.array([])
        gap_arr = np.array(row["gap_s"], dtype=float) if row["gap_s"] is not None else np.array([])

        # Remove zeros / negatives
        dur_arr = dur_arr[dur_arr > 0]
        gap_arr = gap_arr[gap_arr > 0]

        # Fit durations
        dur_result = fit_distributions(dur_arr)
        dur_best = pick_best_distribution(dur_result)
        if dur_best:
            n_fitted_dur += 1

        # Fit gaps
        gap_result = fit_distributions(gap_arr)
        gap_best = pick_best_distribution(gap_result)
        if gap_best:
            n_fitted_gap += 1

        results.append({
            "did": did,
            "table": table,
            "n_sessions": len(dur_arr),
            "n_gaps": len(gap_arr),
            "dur_best": dur_best or "",
            "dur_powerlaw_alpha": dur_result.get("powerlaw", {}).get("alpha") if dur_result.get("powerlaw") else None,
            "dur_powerlaw_xmin": dur_result.get("powerlaw", {}).get("xmin") if dur_result.get("powerlaw") else None,
            "dur_exp_scale": dur_result.get("exponential", {}).get("scale") if dur_result.get("exponential") else None,
            "dur_lognormal_shape": dur_result.get("lognormal", {}).get("shape") if dur_result.get("lognormal") else None,
            "dur_lognormal_scale": dur_result.get("lognormal", {}).get("scale") if dur_result.get("lognormal") else None,
            "dur_weibull_shape": dur_result.get("weibull", {}).get("shape") if dur_result.get("weibull") else None,
            "dur_weibull_scale": dur_result.get("weibull", {}).get("scale") if dur_result.get("weibull") else None,
            "dur_gamma_shape": dur_result.get("gamma", {}).get("shape") if dur_result.get("gamma") else None,
            "dur_gamma_scale": dur_result.get("gamma", {}).get("scale") if dur_result.get("gamma") else None,
            "gap_best": gap_best or "",
            "gap_powerlaw_alpha": gap_result.get("powerlaw", {}).get("alpha") if gap_result.get("powerlaw") else None,
            "gap_powerlaw_xmin": gap_result.get("powerlaw", {}).get("xmin") if gap_result.get("powerlaw") else None,
            "gap_exp_scale": gap_result.get("exponential", {}).get("scale") if gap_result.get("exponential") else None,
            "gap_lognormal_shape": gap_result.get("lognormal", {}).get("shape") if gap_result.get("lognormal") else None,
            "gap_lognormal_scale": gap_result.get("lognormal", {}).get("scale") if gap_result.get("lognormal") else None,
            "gap_weibull_shape": gap_result.get("weibull", {}).get("shape") if gap_result.get("weibull") else None,
            "gap_weibull_scale": gap_result.get("weibull", {}).get("scale") if gap_result.get("weibull") else None,
            "gap_gamma_shape": gap_result.get("gamma", {}).get("shape") if gap_result.get("gamma") else None,
            "gap_gamma_scale": gap_result.get("gamma", {}).get("scale") if gap_result.get("gamma") else None,
        })

        if (idx + 1) % 500 == 0:
            elapsed = time_mod.time() - t0
            rate = (idx + 1) / elapsed
            print(f"    [{idx + 1}/{total}] {rate:.0f} users/s | "
                  f"dur fits: {n_fitted_dur} | gap fits: {n_fitted_gap}", file=sys.stderr)

    elapsed = time_mod.time() - t0
    print(f"  Done in {elapsed:.0f}s — {n_fitted_dur} duration fits, {n_fitted_gap} gap fits", file=sys.stderr)

    return pl.DataFrame(results)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(df: pl.DataFrame, output_dir: Path):
    """Print and save aggregate summary: what % of users follow each distribution."""
    print(f"\n{'='*70}", file=sys.stderr)
    print(f"  DISTRIBUTION FITTING SUMMARY  —  {len(df):,} users", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)

    summary_rows = []

    for table_name in df["table"].unique():
        tdf = df.filter(pl.col("table") == table_name)
        print(f"\n  Table: {table_name}  ({len(tdf):,} users)", file=sys.stderr)

        for quantity, col in [("Session duration", "dur_best"), ("Inter-session gap", "gap_best")]:
            counts = tdf.group_by(col).len().sort("len", descending=True)
            total_with_fit = counts.filter(pl.col(col) != "").select(pl.sum("len")).item()
            total_all = len(tdf)

            print(f"\n    {quantity} distribution  "
                  f"({total_with_fit:,}/{total_all:,} users with fits):", file=sys.stderr)
            print(f"    {'Distribution':<16} {'Users':>8}  {'%':>6}", file=sys.stderr)
            print(f"    {'-'*16} {'-'*8}  {'-'*6}", file=sys.stderr)

            for row in counts.iter_rows(named=True):
                dist = row[col] if row[col] else "(no fit)"
                n = row["len"]
                pct = 100 * n / total_all
                print(f"    {dist:<16} {n:>8,}  {pct:>5.1f}%", file=sys.stderr)
                summary_rows.append({
                    "table": table_name,
                    "quantity": quantity,
                    "distribution": dist,
                    "n_users": n,
                    "pct": round(pct, 1),
                })

            # Parameter summary for each distribution
            for dist_name in DISTRIBUTION_NAMES:
                prefix = "dur_" if quantity == "Session duration" else "gap_"
                shape_col = f"{prefix}{dist_name}_shape" if dist_name in ("lognormal", "weibull", "gamma") else None
                scale_col = f"{prefix}{dist_name}_scale"
                alpha_col = f"{prefix}powerlaw_alpha"
                xmin_col = f"{prefix}powerlaw_xmin"

                if dist_name == "powerlaw":
                    sub = tdf.filter((pl.col(col) == dist_name) & pl.col(alpha_col).is_not_null())
                    if len(sub) > 0:
                        print(f"\n      {dist_name} params (n={len(sub):,}):", file=sys.stderr)
                        print(f"        alpha:  μ={sub[alpha_col].mean():.2f}  "
                              f"med={sub[alpha_col].median():.2f}  "
                              f"σ={sub[alpha_col].std():.2f}", file=sys.stderr)
                        if xmin_col in sub.columns:
                            print(f"        xmin:   μ={sub[xmin_col].mean():.1f}  "
                                  f"med={sub[xmin_col].median():.1f}  "
                                  f"σ={sub[xmin_col].std():.1f}", file=sys.stderr)
                elif shape_col and shape_col in tdf.columns and scale_col in tdf.columns:
                    sub = tdf.filter((pl.col(col) == dist_name) & pl.col(scale_col).is_not_null())
                    if len(sub) > 0:
                        if shape_col in sub.columns:
                            print(f"\n      {dist_name} params (n={len(sub):,}):", file=sys.stderr)
                            print(f"        shape: μ={sub[shape_col].mean():.2f}  "
                                  f"med={sub[shape_col].median():.2f}  "
                                  f"σ={sub[shape_col].std():.2f}", file=sys.stderr)
                        print(f"        scale: μ={sub[scale_col].mean():.1f}  "
                              f"med={sub[scale_col].median():.1f}  "
                              f"σ={sub[scale_col].std():.1f}", file=sys.stderr)
                elif scale_col and scale_col in tdf.columns:
                    sub = tdf.filter((pl.col(col) == dist_name) & pl.col(scale_col).is_not_null())
                    if len(sub) > 0:
                        print(f"\n      {dist_name} params (n={len(sub):,}):", file=sys.stderr)
                        print(f"        scale: μ={sub[scale_col].mean():.1f}  "
                              f"med={sub[scale_col].median():.1f}  "
                              f"σ={sub[scale_col].std():.1f}", file=sys.stderr)

    # Save summary
    summary_df = pl.DataFrame(summary_rows)
    summary_path = output_dir / "distribution_fit_summary.csv"
    summary_df.write_csv(summary_path)
    print(f"\nSummary saved to {summary_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Per-user distribution fitting for session durations & inter-session gaps"
    )
    parser.add_argument(
        "--sample", type=int, default=50_000,
        help="Number of random users to sample from each table (default: 50000)",
    )
    parser.add_argument(
        "--output-dir", type=str, default="results",
        help="Output directory for results CSV (default: results/)",
    )
    parser.add_argument(
        "--tables", type=str, default="sessions_threshold_total,sessions_tukey",
        help="Comma-separated table names in pau_db (default: sessions_threshold_total,sessions_tukey)",
    )
    parser.add_argument(
        "--min-points", type=int, default=MIN_DATA_POINTS,
        help=f"Minimum data points per user to attempt fitting (default: {MIN_DATA_POINTS})",
    )
    args = parser.parse_args()

    global MIN_DATA_POINTS
    MIN_DATA_POINTS = args.min_points

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    table_names = [t.strip() for t in args.tables.split(",")]

    print(f"Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']} ...", file=sys.stderr)
    conn = pymysql.connect(**DB_CONFIG)

    all_results = []

    for tbl in table_names:
        print(f"\nSampling {args.sample:,} DIDs from pau_db.{tbl} ...", file=sys.stderr)
        dids = sample_dids(conn, tbl, args.sample)
        print(f"  → {len(dids):,} DIDs sampled", file=sys.stderr)

        result_df = process_table(conn, tbl, dids, label=tbl)
        all_results.append(result_df)

    conn.close()

    # Combine and save
    combined = pl.concat(all_results)
    csv_path = output_dir / "distribution_fit_results.csv"
    combined.write_csv(csv_path)
    print(f"\nPer-user results saved to {csv_path}  ({len(combined):,} rows)", file=sys.stderr)

    # Print summary
    print_summary(combined, output_dir)


if __name__ == "__main__":
    main()
