#!/usr/bin/env python3
"""Aggregate and report on hyperparameter session duration metrics."""

import polars as pl
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent / "plots"
OUT = SCRIPT_DIR
OUT.mkdir(parents=True, exist_ok=True)


def load_all() -> pl.DataFrame:
    rows = []
    for tsv in sorted(ROOT.rglob("duration_metrics__*.tsv")):
        rel = tsv.relative_to(ROOT)
        df = pl.read_csv(tsv, separator="\t")
        df = df.with_columns(
            pl.lit(rel.parts[0]).alias("method"),
            pl.lit(rel.parts[1]).alias("params"),
        )
        rows.append(df)
    return pl.concat(rows)


def report(df: pl.DataFrame):
    methods = sorted(df["method"].unique().to_list())

    print("=" * 100)
    print("HYPERPARAMETER SESSION ANALYSIS")
    print("=" * 100)

    for method in methods:
        mdf = df.filter(pl.col("method") == method).sort("params")
        n = mdf.height
        print(f"\n{'─' * 100}")
        print(f"  METHOD: {method}  ({n} parameter sets)")
        print(f"{'─' * 100}")

        # --- Singletons & micro sessions ---
        print("\n  ▸ SINGLETONS & MICRO SESSIONS (< 1s)")
        h = f"    {'params':<25s} {'n_sessions':>12s} {'singletons':>10s} {'%singl':>7s} {'% <1s':>7s} {'% <5s':>7s}"
        print(h)
        print("    " + "─" * (len(h) - 4))
        for row in mdf.iter_rows(named=True):
            pct_lt1 = row["pct_lt_1s"]
            flag = " ⚠" if pct_lt1 > 30 else ""
            print(f"    {row['params']:<25s} {row['n_sessions']:>12,} {row['n_singletons']:>10,} {row['pct_singletons']:>6.1f}% {pct_lt1:>6.1f}% {row['pct_lt_5s']:>6.1f}%{flag}")

        # --- Tail analysis ---
        print(f"\n  ▸ TAIL BEHAVIOR — top-end durations")
        h2 = f"    {'params':<25s} {'max_s':>12s} {'max_h':>8s} {'p99_s':>10s} {'p95_s':>10s} {'mean_s':>10s} {'median_s':>10s} {'mean/med':>8s}"
        print(h2)
        print("    " + "─" * (len(h2) - 4))
        for row in mdf.iter_rows(named=True):
            max_s = row["max_s"]
            max_h = max_s / 3600
            mean_s = row["mean_s"]
            med_s = row["median_s"]
            ratio = mean_s / med_s if med_s > 0 else float("inf")
            severe = " ⚡" if ratio > 20 else ""
            print(f"    {row['params']:<25s} {max_s:>12,.0f} {max_h:>7.1f}h {row['p99_s']:>10,.0f} {row['p95_s']:>10,.0f} {mean_s:>10,.0f} {med_s:>10,.0f} {ratio:>7.1f}x{severe}")

        # --- Long-session percentages ---
        print(f"\n  ▸ LONG SESSION PREVALENCE")
        h3 = f"    {'params':<25s} {'% >1h':>7s} {'% >4h':>7s} {'% >8h':>7s}"
        print(h3)
        print("    " + "─" * (len(h3) - 4))
        for row in mdf.iter_rows(named=True):
            pct_gt_1h = row["pct_gt_1h"]
            flag = " ⚠" if pct_gt_1h > 10 else ""
            print(f"    {row['params']:<25s} {pct_gt_1h:>6.1f}% {row['pct_gt_4h']:>6.1f}% {row['pct_gt_8h']:>6.1f}%{flag}")

    # --- Cross-method summary ---
    print(f"\n\n{'═' * 100}")
    print("  CROSS-METHOD SUMMARY")
    print(f"{'═' * 100}")

    for metric, label, is_pct in [
        ("pct_lt_1s", "% sessions < 1s", True),
        ("pct_gt_1h", "% sessions > 1h", True),
        ("pct_gt_8h", "% sessions > 8h", True),
        ("median_s", "median duration (s)", False),
        ("max_s", "max duration (s)", False),
        ("mean_s", "mean duration (s)", False),
    ]:
        print(f"\n  {label}:")
        agg = df.group_by("method").agg([
            pl.col(metric).min().alias("lo"),
            pl.col(metric).median().alias("med"),
            pl.col(metric).max().alias("hi"),
        ]).sort("method")
        for row in agg.iter_rows(named=True):
            if is_pct:
                print(f"    {row['method']:<20s}  min={row['lo']:>6.1f}%  median={row['med']:>6.1f}%  max={row['hi']:>6.1f}%")
            else:
                print(f"    {row['method']:<20s}  min={row['lo']:>12,.0f}  median={row['med']:>12,.0f}  max={row['hi']:>12,.0f}")

    # --- Key observations ---
    print(f"\n{'─' * 100}")
    print("  KEY OBSERVATIONS")
    print(f"{'─' * 100}")

    for method in methods:
        mdf = df.filter(pl.col("method") == method)
        # Worst skew
        mdf = mdf.with_columns(
            (pl.col("mean_s") / pl.col("median_s").replace(0, 1)).alias("skew")
        )
        worst = mdf.sort("skew", descending=True).row(0, named=True)
        w_mean = worst["mean_s"]
        w_med = worst["median_s"]
        w_max = worst["max_s"]
        w_p99 = worst["p99_s"]
        ratio = w_mean / w_med if w_med > 0 else float("inf")
        print(f"  {method}: worst skew (mean/median = {ratio:.1f}x) at {worst['params']}")
        print(f"         max session = {w_max/3600:.1f}h, p99 = {w_p99/3600:.1f}h")
        if w_max > w_p99 * 5:
            print(f"         ⚡ EXTREME OUTLIER: max is {w_max/w_p99:.0f}x the p99 — definitely anomalous")
        elif w_max > w_p99 * 2:
            print(f"         ⚠ max is {w_max/w_p99:.0f}x the p99 — moderately anomalous tail")

    # --- Save aggregated TSV ---
    out_path = OUT / "aggregated_metrics.tsv"
    df.write_csv(out_path, separator="\t")
    print(f"\nSaved aggregated data to {out_path}")


if __name__ == "__main__":
    df = load_all()
    report(df)
