"""Step 2 for inter-post gaps: AIC-best distribution per (did, col) + summaries.

Differs from distribution-fit/step2_build_best.py in filtering: each column
(interpost_within / interpost_global) is filtered at n_obs >= 30
INDEPENDENTLY — requiring both would restrict global to heavy posters and
bias its composition. The paired subset (both cols >= 30) is computed
separately for the within-vs-global family comparison.

Outputs (results/):
  best_per_user.tsv   did, col, n_obs, distribution, family, aic, ad
  best_params.tsv     did, col, distribution, family, param, value
  family_summary.tsv  col, min_obs, family, n_users, pct
  pair_summary.tsv    within_family, global_family, n_users, pct  (paired subset)

Usage:
    uv run inter-post-creation/build_best.py
"""

import sys
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
R = HERE / "results"

POWER_TAIL = {"pareto_i", "lomax", "genpareto"}
MIN_OBS = 30


def family(d):
    return "pareto" if d in POWER_TAIL else d


def main():
    gof = pl.concat([pl.read_csv(p, separator="\t",
                                 schema_overrides={"did": pl.String})
                     for p in sorted(R.glob("gof__chunk*.tsv"))])
    print(f"gof rows: {gof.height:,}", file=sys.stderr)

    best = (gof.sort(["did", "col", "aic", "ad", "distribution"])
               .group_by(["did", "col"], maintain_order=True)
               .first()
               .select(["did", "col", "distribution", "n_obs", "aic", "ad"])
               .with_columns(
                   pl.col("distribution").map_elements(family, return_dtype=pl.String)
                     .alias("family")))

    # AIC vs AD winner agreement
    ad_best = (gof.sort(["did", "col", "ad", "aic", "distribution"])
                  .group_by(["did", "col"], maintain_order=True).first()
                  .select(["did", "col", pl.col("distribution").alias("ad_distribution")]))
    best = best.join(ad_best, on=["did", "col"])
    agree = best["distribution"].eq(best["ad_distribution"]).mean()
    print(f"AIC-best == AD-best: {100 * agree:.1f}% of units", file=sys.stderr)
    best = best.drop("ad_distribution")

    best.write_csv(R / "best_per_user.tsv", separator="\t")
    print(f"→ best_per_user.tsv ({best.height:,} rows)", file=sys.stderr)

    params = pl.concat([pl.read_csv(p, separator="\t",
                                    schema_overrides={"did": pl.String})
                        for p in sorted(R.glob("params__chunk*.tsv"))])
    best_params = (best.select(["did", "col", "distribution", "family"])
                       .join(params, on=["did", "col", "distribution"], how="left"))
    best_params.write_csv(R / "best_params.tsv", separator="\t")
    print(f"→ best_params.tsv ({best_params.height:,} rows)", file=sys.stderr)

    # Family composition per col, at n_obs > 0 and >= MIN_OBS (independent per col)
    rows = []
    for col in ("interpost_within", "interpost_global"):
        sub = best.filter(pl.col("col") == col)
        for label, s in (("all", sub), (f"n>={MIN_OBS}", sub.filter(pl.col("n_obs") >= MIN_OBS))):
            comp = (s.group_by("family").len(name="n_users")
                     .with_columns((100 * pl.col("n_users") / pl.col("n_users").sum())
                                   .round(2).alias("pct"))
                     .sort("n_users", descending=True))
            rows.extend((col, label, r[0], r[1], r[2]) for r in comp.iter_rows())
    summ = pl.DataFrame(rows, schema=["col", "min_obs", "family", "n_users", "pct"],
                        orient="row")
    summ.write_csv(R / "family_summary.tsv", separator="\t")
    print(summ, file=sys.stderr)

    # Paired subset: users with >= MIN_OBS on BOTH cols — within x global crosstab
    wide = (best.filter(pl.col("n_obs") >= MIN_OBS)
                .pivot(values="family", index="did", on="col",
                       aggregate_function="first")
                .drop_nulls())
    pairs = (wide.group_by(["interpost_within", "interpost_global"]).len(name="n_users")
                 .with_columns((100 * pl.col("n_users") / pl.col("n_users").sum())
                               .round(2).alias("pct"))
                 .sort("n_users", descending=True))
    pairs.write_csv(R / "pair_summary.tsv", separator="\t")
    print(f"\npaired subset (both cols n>={MIN_OBS}): {wide.height:,} users", file=sys.stderr)
    print(pairs, file=sys.stderr)
    same = wide.filter(pl.col("interpost_within") == pl.col("interpost_global")).height
    print(f"same family both cols: {100 * same / wide.height:.1f}%", file=sys.stderr)


if __name__ == "__main__":
    main()
