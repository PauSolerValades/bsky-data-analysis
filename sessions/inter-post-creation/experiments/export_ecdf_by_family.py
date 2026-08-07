"""Per-global-family within-session ECDFs for the simulator.

Hypothesis: within-session posting cadence differs by the user's global
inter-post family (a "weibull user" posts differently inside sessions than
an "expon user"). Pool within gaps of users whose GLOBAL fit (n_obs >= 30)
is each family, report quantiles per family, and export one file per family:
results/within_ecdf__<family>.txt (same one-gap-per-line format).

Usage:
    uv run inter-post-creation/export_ecdf_by_family.py [--max-lines 250000]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
MIN_OBS = 30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-lines", type=int, default=250_000)
    args = ap.parse_args()

    best = pl.read_csv(HERE / "results/best_per_user.tsv", separator="\t")
    fam = (best.filter((pl.col("col") == "interpost_global")
                       & (pl.col("n_obs") >= MIN_OBS))
               .select("did", "family"))
    gaps = (pl.scan_parquet(str(HERE.parent / "data/chunk*.parquet"))
              .filter((pl.col("col") == "interpost_within") & (pl.col("value") > 0))
              .collect())
    joined = gaps.join(fam, on="did")
    print(f"within gaps with a trusted global family: {joined.height:,} / {gaps.height:,}",
          file=sys.stderr)

    rng = np.random.default_rng(42)
    print(f"\n{'family':<13}{'users':>8}{'gaps':>10}{'p25':>7}{'p50':>7}{'p75':>7}"
          f"{'p90':>7}{'p99':>8}{'mean':>8}", file=sys.stderr)
    for family in ("expon", "gamma", "lognorm", "weibull_min", "fisk", "power_tail"):
        sub = joined.filter(pl.col("family") == family)
        if sub.height == 0:
            continue
        v = sub["value"].to_numpy()
        n_users = sub["did"].n_unique()
        q = np.quantile(v, [0.25, 0.5, 0.75, 0.9, 0.99])
        print(f"{family:<13}{n_users:>8,}{len(v):>10,}"
              + "".join(f"{x:>7.0f}" for x in q[:4])
              + f"{q[4]:>8.0f}{v.mean():>8.0f}", file=sys.stderr)

        out = HERE / "results" / f"within_ecdf__{family}.txt"
        idx = rng.choice(len(v), size=min(args.max_lines, len(v)), replace=False)
        with open(out, "w") as f:
            f.writelines(f"{x:.6f}\n" for x in v[idx])
        back = np.loadtxt(out)
        assert len(back) == min(args.max_lines, len(v))
        print(f"  → {out.name} ({len(back):,} lines)", file=sys.stderr)


if __name__ == "__main__":
    main()
