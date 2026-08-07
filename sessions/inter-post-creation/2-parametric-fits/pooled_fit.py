"""Pooled (homogeneous) parametric fit of within-session inter-post gaps.

Question: instead of per-user fits (one parameter set per user), can ONE
distribution with ONE parameter set for ALL users describe the pooled
within-session gaps? Fits the same 8-candidate MLE battery as
distribution-fit/fit_lib.R on the pooled 1M subsample
(../3-ecdf/results/within_interpost_ecdf.txt), ranks by AIC and closed-form
KS/CvM/AD, and overlays the ECDF with the fitted CDFs.

Usage:
    uv run inter-post-creation/pooled_fit.py
"""

import sys
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent

# same candidates as fit_lib.R (fisk = log-logistic = scipy fisk;
# pareto = Pareto I, support [scale, inf); lomax = Pareto II with min 0)
DISTS = {
    "expon": (stats.expon, {}),
    "gamma": (stats.gamma, {"floc": 0}),
    "lognorm": (stats.lognorm, {"floc": 0}),
    "weibull_min": (stats.weibull_min, {"floc": 0}),
    "fisk": (stats.fisk, {"floc": 0}),
    "pareto": (stats.pareto, {"floc": 0}),
    "lomax": (stats.lomax, {"floc": 0}),
    "genpareto": (stats.genpareto, {"floc": 0}),
}


def gof(x, dist, params):
    """Closed-form KS / CvM / AD, identical formulas to fit_lib.R gof_stats."""
    xs = np.sort(x)
    n = len(xs)
    F = np.clip(dist.cdf(xs, *params), 1e-300, 1 - 1e-16)
    i = np.arange(1, n + 1)
    ks = max(np.abs(F - (i - 1) / n).max(), np.abs(F - i / n).max())
    cvm = ((F - (2 * i - 1) / (2 * n)) ** 2).sum() + 1 / (12 * n)
    ad = -n - ((2 * i - 1) * (np.log(F) + np.log(1 - F[::-1]))).sum() / n
    return ks, cvm, ad


def fit(x):
    rows = []
    for name, (dist, kw) in DISTS.items():
        try:
            params = dist.fit(x, **kw)
        except Exception as e:
            print(f"{name}: fit failed ({e})", file=sys.stderr)
            continue
        loglik = dist.logpdf(x, *params).sum()
        k = len(params) - (1 if "floc" in kw else 0)
        aic = 2 * k - 2 * loglik
        ks, cvm, ad = gof(x, dist, params)
        rows.append((name, k, loglik, aic, ks, cvm, ad, params))
    rows.sort(key=lambda r: r[3])
    return rows


def report(rows):
    print(f"{'family':<12} {'k':>2} {'AIC':>16} {'dAIC':>10} {'KS':>8} "
          f"{'CvM':>10} {'AD':>12}  params")
    aic_best = rows[0][3]
    for name, k, ll, aic, ks, cvm, ad, params in rows:
        pstr = ", ".join(f"{p:.6g}" for p in params)
        print(f"{name:<12} {k:>2} {aic:>16.1f} {aic - aic_best:>10.1f} "
              f"{ks:>8.4f} {cvm:>10.2f} {ad:>12.1f}  {pstr}")


def main():
    files = ([Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1
             else [HERE.parent / "3-ecdf" / "results" / "within_interpost_ecdf.txt"])
    for f in files:
        x = np.loadtxt(f)
        x = x[x > 0]
        print(f"\n== {f.name}  ({len(x):,} gaps)")
        rows = fit(x)
        report(rows)
        if f.name == "within_interpost_ecdf.txt":
            np.savez(HERE / "results/pooled_fit.npz", x=x,
                     names=np.array([r[0] for r in rows]),
                     params=np.array([r[7] for r in rows], dtype=object),
                     allow_pickle=True)


if __name__ == "__main__":
    main()
