"""Step 3: canonical GPD(xi, sigma, mu) parameters for pareto winners.

Conversions (exact reparametrizations):
  genpareto (evd gpd, loc=0):   xi=shape, sigma=scale, mu=0
  lomax (actuar pareto2):       xi=1/shape, sigma=scale/shape, mu=0
  pareto_i (actuar pareto1):    xi=1/shape, sigma=scale/shape, mu=scale

pareto_i is the Single Parameter Pareto / Pareto I: support x > theta,
so its canonical GPD has mu = theta = scale. (actuar::pareto is a DIFFERENT
function — the mu=0 Pareto II / Lomax — which is why the old battery's
"pareto" entry converted like lomax. With the fit now using pareto1, the
support-bound conversion is correct.)

Output: results/pareto_canonical.tsv  (did, col, xi, sigma, mu, source)

Usage:
    uv run distribution-fit/step3_powerlaw_canonical.py
"""

import sys

import numpy as np
import polars as pl
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = HERE / "results"


def main():
    best = pl.read_csv(R / "best_per_user.tsv", separator="\t")
    params = pl.read_csv(R / "best_params.tsv", separator="\t")

    pt = params.filter(pl.col("family") == "pareto")
    wide = (pt.pivot(values="value", index=["did", "col", "distribution"],
                     on="param", aggregate_function="first"))
    print(f"pareto users: {wide.height:,}", file=sys.stderr)

    # actuar pareto2 also has 'min' (fixed 0), evd gpd has no loc column (fixed 0)
    for c in ("shape", "scale", "min"):
        assert c in wide.columns, f"missing param {c}: {wide.columns}"

    # pareto_i's support bound theta = fitted min (threshold fixed at min(x) in fit_lib.R)
    theta = np.where(wide["distribution"] == "pareto_i", wide["min"], wide["scale"])
    xi = np.where(wide["distribution"] == "genpareto",
                  wide["shape"], 1.0 / wide["shape"])
    sigma = np.where(wide["distribution"] == "genpareto",
                     wide["scale"], theta / wide["shape"])
    # genpareto loc=0; lomax (pareto2, min=0) and pareto_i (pareto1) both have
    # support bound at theta, but lomax's is a pure location=0 form while
    # pareto_i's support genuinely starts at theta=min -> mu=theta.
    mu = np.where(wide["distribution"] == "pareto_i", theta, 0.0)

    out = pl.DataFrame({
        "did": wide["did"], "col": wide["col"],
        "xi": xi, "sigma": sigma, "mu": mu,
        "source": wide["distribution"],
    })

    # ── Numerical check: converted GPD CDF ≡ source CDF at a few quantiles ──
    rng = np.random.default_rng(42)
    idx = [int(i) for i in rng.choice(wide.height, size=min(200, wide.height), replace=False)]
    qs = np.array([0.5, 0.9, 0.99])
    worst = 0.0
    for i in idx:
        dist, shape, scale = wide["distribution"][i], wide["shape"][i], wide["scale"][i]
        th = theta[i]
        x0 = 1.0
        xs = x0 + qs * 10 * th
        if dist == "genpareto":
            F_src = 1 - np.maximum(1 + shape * xs / scale, 1e-300) ** (-1 / shape)
            xig, sig, mug = shape, scale, 0.0
        elif dist == "lomax":
            F_src = 1 - (1 + xs / scale) ** (-shape)
            xig, sig, mug = 1 / shape, scale / shape, 0.0
        else:  # pareto_i: support starts at theta = fitted min
            F_src = np.where(xs >= th, 1 - (xs / th) ** (-shape), 0.0)
            xig, sig, mug = 1 / shape, th / shape, th
        F_gpd = 1 - np.maximum(1 + xig * (xs - mug) / sig, 1e-300) ** (-1 / xig)
        worst = max(worst, float(np.max(np.abs(F_src - F_gpd))))
    print(f"conversion check: max |F_src - F_gpd| over 200 users × 3 quantiles = {worst:.2e}",
          file=sys.stderr)

    out.write_csv(R / "pareto_canonical.tsv", separator="\t")
    print(f"→ pareto_canonical.tsv ({out.height:,} rows)", file=sys.stderr)
    print(out.group_by(["col", "source"]).len().sort(["col", "source"]), file=sys.stderr)


if __name__ == "__main__":
    main()
