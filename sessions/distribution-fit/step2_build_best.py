"""Step 2: per-user best distribution + sampleable parameters.

Selection: min AIC per (did, col). pareto/lomax/genpareto are reported both
concretely (distribution — what you sample from) and grouped (family
"power_tail" — the near-identical siblings for headline numbers).

Outputs (results/):
  best_per_user.tsv   did, col, n_obs, distribution, family, aic, ad
  best_params.tsv     did, col, distribution, family, param, value
  family_summary.tsv  col, family, n_users, pct

Usage:
    uv run distribution-fit/step2_build_best.py
"""

import sys
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
R = HERE / "results"

POWER_TAIL = {"pareto", "lomax", "genpareto"}


def family(d):
    return "power_tail" if d in POWER_TAIL else d


def main():
    gof = pl.concat([pl.read_csv(p, separator="\t",
                                 schema_overrides={"did": pl.String})
                     for p in sorted(R.glob("gof__chunk*.tsv"))])
    print(f"gof rows: {gof.height:,}", file=sys.stderr)

    # AIC winner per (did, col); tie-break by lowest AD then name (deterministic)
    best = (gof.sort(["did", "col", "aic", "ad", "distribution"])
               .group_by(["did", "col"], maintain_order=True)
               .first()
               .select(["did", "col", "distribution", "n_obs", "aic", "ad"])
               .with_columns(
                   pl.col("distribution").map_elements(family, return_dtype=pl.String)
                     .alias("family")))

    # AD winner agreement (thesis number)
    ad_best = (gof.sort(["did", "col", "ad", "aic", "distribution"])
                  .group_by(["did", "col"], maintain_order=True).first()
                  .select(["did", "col", pl.col("distribution").alias("ad_distribution")]))
    best = best.join(ad_best, on=["did", "col"])
    agree = best["distribution"].eq(best["ad_distribution"]).mean()
    print(f"AIC-best == AD-best: {100 * agree:.1f}% of units", file=sys.stderr)
    best = best.drop("ad_distribution")

    best.write_csv(R / "best_per_user.tsv", separator="\t")
    print(f"→ best_per_user.tsv ({best.height:,} rows)", file=sys.stderr)

    # Parameters of the winning distribution only
    params = pl.concat([pl.read_csv(p, separator="\t",
                                    schema_overrides={"did": pl.String})
                        for p in sorted(R.glob("params__chunk*.tsv"))])
    best_params = (best.select(["did", "col", "distribution", "family"])
                       .join(params, on=["did", "col", "distribution"], how="left"))
    best_params.write_csv(R / "best_params.tsv", separator="\t")
    print(f"→ best_params.tsv ({best_params.height:,} rows)", file=sys.stderr)

    # Family summary
    summ = (best.group_by(["col", "family"]).len(name="n_users")
               .with_columns((100 * pl.col("n_users") / pl.col("n_users").sum().over("col"))
                             .round(2).alias("pct"))
               .sort(["col", "n_users"], descending=[False, True]))
    summ.write_csv(R / "family_summary.tsv", separator="\t")
    print(summ, file=sys.stderr)


if __name__ == "__main__":
    main()
