#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "numpy",
#     "scipy",
#     "matplotlib",
#     "pymysql",
# ]
# ///
"""
Phase 2a — Power-law fit on post lifetimes.

Fits continuous distributions (power-law/Pareto, lognormal, Weibull,
exponential) to the combined post lifetime (last_engagement_us − created_at).
Compares fits via log-likelihood and KS statistic.
Shows per-type lifetime distributions too.

Output:
  - Console: best-fit parameters, distribution comparison
  - eda/results/powerlaw_lifetimes_ccdf.png     (CCDF with best fits)
  - eda/results/powerlaw_lifetimes_by_type.png  (per-type lifetime CCDFs)

Usage:
    uv run post-lifetime/eda/fit_powerlaw_lifetimes.py
"""

import os
from pathlib import Path

import numpy as np
import pymysql
from scipy.stats import (
    pareto, lognorm, expon, weibull_min, kstest
)
from scipy.optimize import minimize_scalar

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def _load_env_file():
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for f in candidates:
        if f.exists():
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
            return

_load_env_file()

def _env(k, d=""):
    return os.environ.get(k, d)

DB_CONFIG = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": "pau_db",
    "charset": "utf8mb4",
}

RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

SEC_PER_HOUR = 3600.0

# ===========================================================================
# Continuous power-law (Pareto) fitting with x_min selection
# ===========================================================================

def fit_pareto_mle(data, xmin_candidates=None):
    """
    Fit Pareto distribution with x_min selection via KS minimisation.
    Returns (alpha, xmin, ks_stat).
    Pareto pdf: f(x) = (α-1)/xmin * (x/xmin)^(-α),  x ≥ xmin > 0
    """
    data = np.array(data, dtype=np.float64)
    data = data[data > 0]
    data = np.sort(data)

    if xmin_candidates is None:
        uniq = np.unique(data)
        if len(uniq) > 200:
            idx = np.linspace(0, len(uniq) - 1, 200, dtype=int)
            xmin_candidates = uniq[idx]
        else:
            xmin_candidates = uniq

    best_ks = np.inf
    best_alpha = None
    best_xmin = None

    for xm in xmin_candidates:
        tail = data[data >= xm]
        if len(tail) < 10:
            continue
        # MLE for Pareto α given xmin
        alpha = 1.0 + len(tail) / np.sum(np.log(tail / xm))
        # KS test
        cdf_theo = pareto.cdf(tail, alpha - 1, scale=xm)
        cdf_emp = np.arange(1, len(tail) + 1) / len(tail)
        ks = np.max(np.abs(cdf_emp - cdf_theo))
        if ks < best_ks:
            best_ks = ks
            best_alpha = alpha
            best_xmin = xm

    return best_alpha, best_xmin, best_ks


def fit_distributions(data, dists=None):
    """
    Fit multiple distributions to data and return sorted by log-likelihood.
    Returns list of (name, params, loglik, ks_stat).
    """
    if dists is None:
        dists = ["pareto", "lognormal", "weibull", "exponential"]

    data = np.array(data, dtype=np.float64)
    data = data[data > 0]
    results = []

    for name in dists:
        try:
            if name == "pareto":
                alpha, xmin, ks = fit_pareto_mle(data)
                tail = data[data >= xmin]
                loglik = np.sum(pareto.logpdf(tail, alpha - 1, scale=xmin))
                results.append((name, (alpha, xmin), loglik, ks, len(tail)))
            elif name == "lognormal":
                s, loc, scale = lognorm.fit(data, floc=0)
                loglik = np.sum(lognorm.logpdf(data, s, loc, scale))
                ks = kstest(data, lambda x: lognorm.cdf(x, s, loc, scale)).statistic
                results.append((name, (s, loc, scale), loglik, ks, len(data)))
            elif name == "weibull":
                c, loc, scale = weibull_min.fit(data)
                loglik = np.sum(weibull_min.logpdf(data, c, loc, scale))
                ks = kstest(data, lambda x: weibull_min.cdf(x, c, loc, scale)).statistic
                results.append((name, (c, loc, scale), loglik, ks, len(data)))
            elif name == "exponential":
                loc, scale = expon.fit(data, floc=0)
                loglik = np.sum(expon.logpdf(data, loc, scale))
                ks = kstest(data, lambda x: expon.cdf(x, loc, scale)).statistic
                results.append((name, (loc, scale), loglik, ks, len(data)))
        except Exception as e:
            print(f"  ⚠ {name}: fit failed — {e}")

    results.sort(key=lambda r: r[2], reverse=True)  # best log-lik first
    return results


# ===========================================================================
# Plotting
# ===========================================================================

def plot_lifetime_ccdf(data_dict, dist_results, output_path):
    """
    CCDF of combined lifetime with best-fit overlays.
    data_dict: {'combined': array_seconds, 'repost': ..., 'like': ..., 'reply': ...}
    dist_results: output of fit_distributions for 'combined'
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    # Left: combined lifetime with best fits
    data = data_dict["combined"]
    data_h = np.sort(data[data > 0]) / SEC_PER_HOUR
    ccdf = 1.0 - np.arange(len(data_h)) / len(data_h)

    ax1.loglog(data_h, ccdf, ".", markersize=1.2, color="#794bc4", alpha=0.4,
               label=f"Combined (n={len(data_h):,})")

    colors = ["black", "red", "blue", "green"]
    for i, (name, params, ll, ks, n) in enumerate(dist_results[:3]):
        x_plot = np.logspace(np.log10(data_h.min()), np.log10(data_h.max()), 300)
        try:
            if name == "pareto":
                alpha, xmin = params
                x_plot = x_plot[x_plot >= xmin / SEC_PER_HOUR]
                y = 1.0 - pareto.cdf(x_plot * SEC_PER_HOUR, alpha - 1,
                                     scale=xmin)
            elif name == "lognormal":
                s, loc, scale = params
                y = 1.0 - lognorm.cdf(x_plot * SEC_PER_HOUR, s, loc, scale)
            elif name == "weibull":
                c, loc, scale = params
                y = 1.0 - weibull_min.cdf(x_plot * SEC_PER_HOUR, c, loc, scale)
            elif name == "exponential":
                loc, scale = params
                y = 1.0 - expon.cdf(x_plot * SEC_PER_HOUR, loc, scale)
            else:
                continue
            ax1.loglog(x_plot, y, "-", color=colors[i], linewidth=1.5,
                       label=f"{name} (ll={ll/1e6:.1f}M)")
        except Exception:
            pass

    ax1.set_xlabel("Lifetime (hours)")
    ax1.set_ylabel("P(T ≥ t)")
    ax1.set_title("Combined lifetime CCDF with distribution fits")
    ax1.legend(fontsize=7)
    ax1.grid(True, alpha=0.3)

    # Right: per-type lifetime CCDFs
    type_colors = {"repost": "#1d9bf0", "like": "#e0245e", "reply": "#17bf63",
                   "combined": "#794bc4"}
    for label, arr in data_dict.items():
        arr_h = np.sort(arr[arr > 0]) / SEC_PER_HOUR
        ccdf_t = 1.0 - np.arange(len(arr_h)) / len(arr_h)
        ax2.loglog(arr_h, ccdf_t, ".", markersize=1.0,
                   color=type_colors.get(label, "gray"), alpha=0.35,
                   label=f"{label} (n={len(arr_h):,})")

    ax2.set_xlabel("Lifetime (hours)")
    ax2.set_ylabel("P(T ≥ t)")
    ax2.set_title("Per-type lifetime CCDFs")
    ax2.legend(fontsize=7)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ {output_path}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    print("=" * 65)
    print("  Phase 2a — Power-law fit on post lifetimes")
    print("=" * 65)
    print()

    conn = pymysql.connect(**DB_CONFIG)
    try:
        # ── Fetch lifetime data (seconds) ───────────────────────────────
        print("Fetching lifetime data …")
        sql = """
            SELECT
                (last_reposted_us   - UNIX_TIMESTAMP(created_at)*1000000)/1000000.0,
                (last_liked_us      - UNIX_TIMESTAMP(created_at)*1000000)/1000000.0,
                (last_replied_us    - UNIX_TIMESTAMP(created_at)*1000000)/1000000.0,
                (last_engagement_us - UNIX_TIMESTAMP(created_at)*1000000)/1000000.0
            FROM post_lifetime
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

        data_dict = {"repost": [], "like": [], "reply": [], "combined": []}
        keys = ["repost", "like", "reply", "combined"]
        for row in rows:
            for i, key in enumerate(keys):
                v = row[i]
                if v is not None and v > 0:
                    data_dict[key].append(float(v))

        for key in keys:
            data_dict[key] = np.array(data_dict[key])
            print(f"  {key:<10s}: {len(data_dict[key]):>10,} posts with lifetime > 0")
        print()

        # ── Fit distributions to combined lifetime ──────────────────────
        print("Fitting distributions to combined lifetime …")
        combined = data_dict["combined"]
        # Filter out outliers: keep 0.1–99.9 percentile
        lo, hi = np.percentile(combined, [0.1, 99.9])
        combined_clean = combined[(combined >= lo) & (combined <= hi)]
        print(f"  Raw: {len(combined):,}  |  Trimmed (p0.1–p99.9): {len(combined_clean):,}")
        print()

        results = fit_distributions(combined_clean)
        print("  Distribution comparison (best first):")
        print(f"  {'Rank':<5s} {'Distribution':<14s} {'LogLik':>12s} {'KS':>8s} {'n':>10s}")
        print(f"  {'─'*5} {'─'*14} {'─'*12} {'─'*8} {'─'*10}")
        for i, (name, params, ll, ks, n) in enumerate(results):
            marker = " ← BEST" if i == 0 else ""
            print(f"  {i+1:<5d} {name:<14s} {ll:>12.1f} {ks:>8.5f} {n:>10,d}{marker}")

            if name == "pareto":
                alpha, xmin = params
                print(f"         α={alpha:.3f}, x_min={xmin/SEC_PER_HOUR:.1f}h")
            elif name == "lognormal":
                s, loc, scale = params
                print(f"         σ={s:.3f}, μ={np.log(scale):.3f}, scale={scale/SEC_PER_HOUR:.1f}h")
            elif name == "weibull":
                c, loc, scale = params
                print(f"         shape={c:.3f}, scale={scale/SEC_PER_HOUR:.1f}h")
            elif name == "exponential":
                loc, scale = params
                print(f"         scale={scale/SEC_PER_HOUR:.1f}h")
        print()

        # ── Plot ────────────────────────────────────────────────────────
        print("Generating plots …")
        plot_lifetime_ccdf(data_dict, results,
                           RESULTS / "powerlaw_lifetimes_ccdf.png")
        print()

        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
