"""Step 3: canonical GPD(xi, sigma, mu) parameters for power_tail winners.

Conversions (exact reparametrizations):
  genpareto (evd gpd, loc=0):   xi=shape, sigma=scale, mu=0
  lomax (actuar pareto2):       xi=1/shape, sigma=scale/shape, mu=0
  pareto (actuar pareto):       xi=1/shape, sigma=scale/shape, mu=scale

Output: results/power_tail_canonical.tsv  (did, col, xi, sigma, mu, source)

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

    pt = params.filter(pl.col("family") == "power_tail")
    wide = (pt.pivot(values="value", index=["did", "col", "distribution"],
                     on="param", aggregate_function="first"))
    print(f"power_tail users: {wide.height:,}", file=sys.stderr)

    # actuar pareto2 also has 'min' (fixed 0), evd gpd has no loc column (fixed 0)
    for c in ("shape", "scale"):
        assert c in wide.columns, f"missing param {c}: {wide.columns}"

    xi = np.where(wide["distribution"] == "genpareto",
                  wide["shape"], 1.0 / wide["shape"])
    sigma = np.where(wide["distribution"] == "genpareto",
                     wide["scale"], wide["scale"] / wide["shape"])
    mu = np.where(wide["distribution"] == "pareto", wide["scale"], 0.0)

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
        x0 = 1.0 if dist != "pareto" else scale  # pareto support starts at scale
        xs = x0 + qs * 10 * scale
        if dist == "genpareto":
            F_src = 1 - np.maximum(1 + shape * xs / scale, 1e-300) ** (-1 / shape)
            xig, sig, mug = shape, scale, 0.0
        elif dist == "lomax":
            F_src = 1 - (1 + xs / scale) ** (-shape)
            xig, sig, mug = 1 / shape, scale / shape, 0.0
        else:
            F_src = np.where(xs >= scale, 1 - (xs / scale) ** (-shape), 0.0)
            xig, sig, mug = 1 / shape, scale / shape, scale
        F_gpd = 1 - np.maximum(1 + xig * (xs - mug) / sig, 1e-300) ** (-1 / xig)
        worst = max(worst, float(np.max(np.abs(F_src - F_gpd))))
    print(f"conversion check: max |F_src - F_gpd| over 200 users × 3 quantiles = {worst:.2e}",
          file=sys.stderr)

    out.write_csv(R / "power_tail_canonical.tsv", separator="\t")
    print(f"→ power_tail_canonical.tsv ({out.height:,} rows)", file=sys.stderr)
    print(out.group_by(["col", "source"]).len().sort(["col", "source"]), file=sys.stderr)


if __name__ == "__main__":
    main()
