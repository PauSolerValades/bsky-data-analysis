"""Pair per-user best fits into wide parameter tables.

Two output modes, both under <params-dir>/ (default <repo>/params/, outside
results/):

1. Default: one file per column pair — params__{col1}__{col2}.tsv, one row per
   user with the parameter columns of both columns (union over all families,
   blank where a family has no such parameter).

2. --by-family-pair: one file per (family1, family2) pair, mirroring the ECDF
   exports (within_ecdf__{f1}__{f2}.txt): params__{f1}__{f2}.tsv with one row
   per user in that pair and exactly the parameter columns of the two
   families. Pairs kept at >= MIN_PAIR_SHARE of users (same as export_by_pair).

Selection: AIC-best per (did, col) from the fit outputs (gof__chunk*.tsv +
params__chunk*.tsv). Every Pareto-family winner is converted to canonical GPD
(xi, sigma, mu) and grouped under the single family "pareto".

Canonical GPD conversions (exact reparametrizations, CRAN-verified):
  genpareto (evd gpd, loc=0):     xi = shape,       sigma = scale,        mu = 0
  lomax (actuar pareto2, min=0):  xi = 1/shape,     sigma = scale/shape,  mu = 0
  pareto_i (actuar pareto1):      xi = 1/shape,     sigma = min/shape,    mu = min
  pareto (legacy actuar::pareto = Pareto II, mu=0): same as lomax

fisk is not fitted (removed from fit_lib.R); legacy fisk rows are dropped.

Usage:
    uv run distribution-fit/pair_params.py duration gap
    uv run distribution-fit/pair_params.py duration gap --by-family-pair
"""

import argparse
import sys
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent

POWER_TAIL = {"pareto", "pareto_i", "lomax", "genpareto"}
PARAMS = ["shape", "scale", "rate", "meanlog", "sdlog", "xi", "sigma", "mu"]
FAMILY_PARAMS = {  # parameters carried by each family, in order
    "expon": ["rate"],
    "gamma": ["shape", "rate"],
    "lognorm": ["meanlog", "sdlog"],
    "weibull_min": ["shape", "scale"],
    "pareto": ["xi", "sigma", "mu"],
}
MIN_PAIR_SHARE = 0.01  # same pair-keeping rule as inter-post-creation/3-ecdf/export_by_pair.py


def to_gpd(dist: str, shape: float, scale: float, min_: float) -> tuple:
    """(xi, sigma, mu) for a Pareto-family winner."""
    if dist == "genpareto":
        return float(shape), float(scale), 0.0
    if dist == "lomax" or dist == "pareto":  # Pareto II, mu=0 (legacy 'pareto' too)
        return 1.0 / shape, scale / shape, 0.0
    return 1.0 / shape, min_ / shape, float(min_)  # pareto_i


def select_best(gof: pl.DataFrame, min_obs: int) -> pl.DataFrame:
    """AIC winner per (did, col); tie-break by AD then name; pairwise n_obs filter."""
    gof = gof.filter(~pl.col("distribution").eq("fisk"))  # legacy rows, battery no longer fits it
    best = (gof.sort(["did", "col", "aic", "ad", "distribution"])
               .group_by(["did", "col"], maintain_order=True)
               .first()
               .select(["did", "col", "distribution", "n_obs"]))
    ok = (best.filter(pl.col("n_obs") >= min_obs)
              .group_by("did").len()
              .filter(pl.col("len") == 2)
              .select("did"))
    return best.join(ok, on="did")


def to_wide(best: pl.DataFrame, params: pl.DataFrame) -> pl.DataFrame:
    """One row per (did, col); Pareto-family winners get canonical GPD xi/sigma/mu."""
    wp = (best.select(["did", "col", "distribution"])
              .join(params, on=["did", "col", "distribution"]))
    wide = wp.pivot(values="value", index=["did", "col", "distribution"],
                    on="param", aggregate_function="first")
    # align to the full column set (pivot only creates params that exist in data)
    wide = wide.select(pl.col("did"), pl.col("col"), pl.col("distribution"),
                       *[pl.col(p) if p in wide.columns else pl.lit(None).alias(p)
                         for p in PARAMS + ["min"]])
    pt = pl.col("distribution").is_in(POWER_TAIL)
    wide = (wide.with_columns([
                pl.when(pt)
                  .then(pl.when(pl.col("distribution") == "genpareto")
                        .then(pl.col("shape")).otherwise(1.0 / pl.col("shape")))
                  .alias("xi"),
                pl.when(pt)
                  .then(pl.when(pl.col("distribution") == "genpareto")
                        .then(pl.col("scale"))
                        .otherwise(pl.when(pl.col("distribution") == "pareto_i")
                                   .then(pl.col("min") / pl.col("shape"))
                                   .otherwise(pl.col("scale") / pl.col("shape"))))
                  .alias("sigma"),
                pl.when(pt)
                  .then(pl.when(pl.col("distribution") == "pareto_i")
                        .then(pl.col("min")).otherwise(0.0))
                  .alias("mu"),
            ])
            .drop("min"))
    return wide.select(["did", "col"] + PARAMS)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cols", nargs=2, help="the two column names to pair")
    ap.add_argument("--results-dir", default=str(HERE / "results"))
    ap.add_argument("--out-dir", default=str(HERE.parent / "params"))
    ap.add_argument("--min-obs", type=int, default=30)
    ap.add_argument("--by-family-pair", action="store_true",
                    help="one params__{f1}__{f2}.tsv per (family, family) pair, like the ECDFs")
    args = ap.parse_args()
    c1, c2 = args.cols
    R = Path(args.results_dir)

    gof = pl.concat([pl.read_csv(p, separator="\t", schema_overrides={"did": pl.String})
                     for p in sorted(R.glob("gof__chunk*.tsv"))])
    params = pl.concat([pl.read_csv(p, separator="\t", schema_overrides={"did": pl.String})
                        for p in sorted(R.glob("params__chunk*.tsv"))])
    n0 = gof.height
    gof = gof.filter(~pl.col("distribution").eq("fisk"))
    print(f"gof rows: {n0:,} (dropped {n0 - gof.height:,} legacy fisk)", file=sys.stderr)

    best = select_best(gof, args.min_obs)
    n_users = best.height // 2
    print(f"kept {n_users:,} users (both cols n_obs >= {args.min_obs})", file=sys.stderr)

    fam = (best.select(["did", "col", "distribution"])
               .with_columns(pl.col("distribution")
                             .map_elements(lambda d: "pareto" if d in POWER_TAIL else d,
                                           return_dtype=pl.String).alias("family")))
    print(fam.group_by(["col", "family"]).len().sort(["col", "len"]), file=sys.stderr)

    wide = (to_wide(best, params)
            .pivot(values=PARAMS, index="did", on="col", aggregate_function="first")
            .rename({f"{p}_{c}": f"{c}_{p}" for c in (c1, c2) for p in PARAMS}))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.by_family_pair:
        pair_fam = fam.pivot(values="family", index="did", on="col", aggregate_function="first")
        merged = wide.join(pair_fam, on="did")
        keep = (merged.group_by([c1, c2]).len()
                      .filter(pl.col("len") >= MIN_PAIR_SHARE * merged.height))
        print(f"pairs kept (>= {100*MIN_PAIR_SHARE:.0f}% of users): {keep.height}", file=sys.stderr)
        for row in keep.sort([c1, c2]).iter_rows(named=True):
            dfam, gfam = row[c1], row[c2]
            sub = merged.filter((pl.col(c1) == dfam) & (pl.col(c2) == gfam))
            cols = (["did"]
                    + [f"{c1}_{p}" for p in FAMILY_PARAMS[dfam]]
                    + [f"{c2}_{p}" for p in FAMILY_PARAMS[gfam]])
            dest = out_dir / f"params__{dfam}__{gfam}.tsv"
            sub.select(cols).sort("did").write_csv(dest, separator="\t")
            print(f"→ {dest.name} ({sub.height:,} users)", file=sys.stderr)
        return

    out = wide.select(["did"]
                      + [f"{c1}_{p}" for p in PARAMS]
                      + [f"{c2}_{p}" for p in PARAMS])
    dest = out_dir / f"params__{c1}__{c2}.tsv"
    out.write_csv(dest, separator="\t")
    print(f"→ {dest} ({out.height:,} users, {out.width} cols)", file=sys.stderr)


def demo():
    """Synthetic smoke test: conversions + wide layout."""
    gof = pl.DataFrame({
        "did": ["u1", "u1", "u2", "u2", "u3", "u3", "u4", "u4"],
        "col": ["duration", "gap"] * 4,
        "distribution": ["pareto_i", "genpareto", "lomax", "weibull_min",
                         "pareto", "gamma", "expon", "lognorm"],
        "n_obs": [50, 50, 50, 50, 50, 50, 50, 50],
        "aic": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "ad": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
    })
    params = pl.DataFrame({
        "did": ["u1", "u1", "u2", "u2", "u3", "u3", "u4", "u4"],
        "col": ["duration", "gap"] * 4,
        "distribution": ["pareto_i", "genpareto", "lomax", "weibull_min",
                         "pareto", "gamma", "expon", "lognorm"],
        "param": ["shape", "shape", "shape", "shape", "shape", "shape", "rate", "meanlog"],
        "value": [2.0, 0.5, 3.0, 1.5, 4.0, 2.0, 0.01, 4.5],
    })
    params = pl.concat([params, pl.DataFrame({
        "did": ["u1", "u1", "u2", "u3", "u3", "u4"],
        "col": ["duration", "gap", "gap", "duration", "gap", "gap"],
        "distribution": ["pareto_i", "genpareto", "lomax", "pareto", "gamma", "lognorm"],
        "param": ["min", "scale", "scale", "scale", "rate", "sdlog"],
        "value": [50.0, 100.0, 120.0, 60.0, 0.01, 1.2],
    })])
    # legacy fisk rows must be dropped by select_best
    gof = pl.concat([gof, pl.DataFrame({
        "did": ["u5", "u5"], "col": ["duration", "gap"],
        "distribution": ["fisk", "fisk"], "n_obs": [50, 50],
        "aic": [1.0, 1.0], "ad": [0.1, 0.1],
    })])

    best = select_best(gof, 30)
    assert best.height == 8, best.height
    wide = to_wide(best, params).sort(["did", "col"])

    def val(did, col, p):
        return wide.filter((pl.col("did") == did) & (pl.col("col") == col))[p][0]

    # pareto_i -> xi=1/2, sigma=50/2, mu=50 ; genpareto -> xi=0.5, sigma=100, mu=0
    assert abs(val("u1", "duration", "xi") - 0.5) < 1e-12
    assert abs(val("u1", "duration", "sigma") - 25.0) < 1e-12
    assert abs(val("u1", "duration", "mu") - 50.0) < 1e-12
    assert abs(val("u1", "gap", "xi") - 0.5) < 1e-12
    assert abs(val("u1", "gap", "sigma") - 100.0) < 1e-12
    assert abs(val("u1", "gap", "mu") - 0.0) < 1e-12
    # legacy 'pareto' converted like lomax: xi=1/4, sigma=60/4, mu=0
    assert abs(val("u3", "duration", "xi") - 0.25) < 1e-12
    assert abs(val("u3", "duration", "sigma") - 15.0) < 1e-12
    assert abs(val("u3", "duration", "mu") - 0.0) < 1e-12
    # light tail untouched: gamma shape=2, rate=0.01; lognorm meanlog=4.5, sdlog=1.2
    assert abs(val("u3", "gap", "shape") - 2.0) < 1e-12
    assert abs(val("u3", "gap", "rate") - 0.01) < 1e-12
    assert abs(val("u4", "gap", "meanlog") - 4.5) < 1e-12
    assert abs(val("u4", "gap", "sdlog") - 1.2) < 1e-12
    print("demo OK")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        main()
