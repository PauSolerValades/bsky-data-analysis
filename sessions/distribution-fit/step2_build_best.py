"""Step 2: per-user best distribution + sampleable parameters.

Selection: min AIC per (did, col). pareto/lomax/genpareto are reported both
concretely (distribution — what you sample from) and grouped (family
"pareto" — the near-identical siblings for headline numbers).

Activity threshold enforced HERE for the whole pipeline: only users with
>= 30 duration obs AND >= 30 gap obs are kept (fits are per-user, so this is
pure selection — no re-fitting). Downstream scripts can trust the input.

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

POWER_TAIL = {"pareto_i", "lomax", "genpareto"}
MIN_OBS = 30  # per column; user must clear it on BOTH duration and gap


def family(d):
    return "pareto" if d in POWER_TAIL else d


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

    # Pairwise activity threshold: both cols present with n_obs >= MIN_OBS
    before = best.height
    both_ok = (best.filter(pl.col("n_obs") >= MIN_OBS)
                   .group_by("did").len()
                   .filter(pl.col("len") == 2)
                   .select("did"))
    best = best.join(both_ok, on="did")
    print(f"activity filter (both cols n_obs>={MIN_OBS}): "
          f"{before:,} → {best.height:,} rows", file=sys.stderr)

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
