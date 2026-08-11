"""GPD tail-type breakdown of the Pareto-family shape parameter (xi).

Reads params/params__*.tsv (one file per family pair) and reports, for every
column carrying a GPD (duration/gap), the share of users whose canonical GPD
shape parameter falls in each EVT tail regime:

  xi < -eps   bounded tail (Weibull-type GPD, finite upper endpoint)
  |xi| <= eps exponential tail (Gumbel-type)
  xi >  eps   heavy tail (Frechet-type, Pareto-like)

Also prints column totals. Eps defaults to 0.1 (--eps to change).

Output: results/xi_tail_breakdown.tsv  (file, side, n, pct_lt, pct_around, pct_gt)

Usage:
    uv run distribution-fit/xi_tail_analysis.py [--eps 0.1]
"""

import argparse
import glob
import sys
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
PARAMS_DIR = HERE.parent / "params"
OUT = HERE / "results" / "xi_tail_breakdown.tsv"


def breakdown(series: pl.Series, eps: float) -> tuple:
    n = len(series)
    return (n,
            100.0 * (series < -eps).sum() / n,
            100.0 * (series.abs() <= eps).sum() / n,
            100.0 * (series > eps).sum() / n)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eps", type=float, default=0.1,
                    help="'around 0' half-width for xi (default 0.1)")
    args = ap.parse_args()
    eps = args.eps

    rows = []
    for f in sorted(glob.glob(str(PARAMS_DIR / "params__*.tsv"))):
        pair = Path(f).stem.removeprefix("params__")
        df = pl.read_csv(f, separator="\t")
        for side in ("duration", "gap"):
            col = f"{side}_xi"
            if col in df.columns:
                v = df[col].drop_nulls()
                if v.len():
                    n, lt, ar, gt = breakdown(v, eps)
                    rows.append({"file": pair, "side": side, "n": n,
                                 "pct_xi_lt": round(lt, 2), "pct_xi_around": round(ar, 2),
                                 "pct_xi_gt": round(gt, 2)})

    out = pl.DataFrame(rows)
    out.write_csv(OUT, separator="\t")

    e = f"{eps:g}"
    print(f"GPD tail-type breakdown (xi vs {eps=}; n_obs>=30 users, both cols):\n")
    print(f"{'pair':<24} {'side':<9} {'n':>7} {'xi<-' + e:>9} {'|xi|<=' + e:>10} {'xi>' + e:>8}")
    for row in out.iter_rows(named=True):
        print(f"{row['file']:<24} {row['side']:<9} {row['n']:>7,} "
              f"{row['pct_xi_lt']:>8.2f}% {row['pct_xi_around']:>9.2f}% {row['pct_xi_gt']:>7.2f}%")

    print("\ncolumn totals:")
    for side in ("duration", "gap"):
        sub = out.filter(pl.col("side") == side)
        n = sub["n"].sum()
        lt = (sub["pct_xi_lt"] * sub["n"]).sum() / n
        ar = (sub["pct_xi_around"] * sub["n"]).sum() / n
        gt = (sub["pct_xi_gt"] * sub["n"]).sum() / n
        print(f"  {side:<9} n={n:>7,}  xi<-{eps}: {lt:>6.2f}%  "
              f"|xi|<={eps}: {ar:>6.2f}%  xi>{eps}: {gt:>6.2f}%")

    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()
