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
Phase 1 — Power-law fit on engagement counts.

Fits a discrete power-law distribution to total_reposts, total_likes,
total_replies, and total_engagement across all top-level posts.
Uses MLE for α estimation with KS-minimisation for x_min selection,
then compares against lognormal, Weibull, and exponential alternatives.

Output:
  - Console: α, x_min, σ, p-value (vs power-law null), LLR vs alternatives
  - eda/results/powerlaw_counts_ccdf.png     (CCDF with fit overlay)
  - eda/results/powerlaw_counts_compare.png  (alternative distributions)

Usage:
    uv run post-lifetime/eda/fit_powerlaw_counts.py
"""

import os
import sys
from pathlib import Path

import numpy as np
import pymysql
from scipy.special import zeta as _zeta
from scipy.stats import kstest, lognorm, expon, weibull_min

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_ENV = {}
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            _ENV[key] = val.strip().strip('"').strip("'")

DB_CONFIG = {
    "host": _ENV.get("DATABASE_HOST", "10.18.74.14"),
    "port": int(_ENV.get("DATABASE_PORT", "9030")),
    "user": _ENV.get("DATABASE_USER", "pau"),
    "password": _ENV.get("PAU_PASSWORD", ""),
    "database": "pau_db",
    "charset": "utf8mb4",
}

RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# Discrete power-law fitting
# ===========================================================================

def _hurwitz_zeta(s, q, terms=2000):
    """Approximate Hurwitz zeta: Σ_{n=0}^{∞} 1/(n+q)^s.
    q can be a scalar or 1-D array. Uses 2000 terms (accurate for α > 1.2)."""
    q = np.atleast_1d(np.asarray(q, dtype=np.float64))
    n = np.arange(terms, dtype=np.float64).reshape(-1, 1)
    q = q.reshape(1, -1)
    return np.sum(1.0 / (n + q) ** s, axis=0).squeeze()


def _ccdf_approx(x, alpha, xmin):
    """Fast continuous approximation of discrete PL CCDF: (x/xmin)^(1-α)."""
    xf = np.array(x, dtype=np.float64)
    result = np.ones_like(xf)
    mask = xf >= xmin
    result[mask] = (xf[mask] / xmin) ** (1.0 - alpha)
    return result


def discrete_pl_ccdf(x, alpha, xmin):
    """P(X ≥ x) for discrete power-law (x ≥ xmin)."""
    norm = _hurwitz_zeta(alpha, xmin)
    xf = np.array(x, dtype=np.float64)
    result = np.ones_like(xf)
    mask = xf >= xmin
    result[mask] = _hurwitz_zeta(alpha, xf[mask]) / norm
    return result


def discrete_pl_loglik(data, alpha, xmin):
    """Log-likelihood of discrete power-law given α and x_min."""
    d = np.array(data, dtype=np.float64)
    d = d[d >= xmin]
    if len(d) == 0:
        return -np.inf
    norm = _hurwitz_zeta(alpha, xmin)
    return -alpha * np.sum(np.log(d)) - len(d) * np.log(norm)


def fit_discrete_powerlaw(data, xmin_candidates=None, n_bootstrap=50):
    """
    Fit discrete power-law to integer data.
    Uses fast continuous CCDF approximation for KS, exact discrete for final.
    Returns (alpha, xmin, ks_stat, ks_pvalue).
    """
    data = np.array(data, dtype=np.int64)
    data = data[data > 0]
    data = np.sort(data)

    if xmin_candidates is None:
        uniq = np.unique(data)
        if len(uniq) > 80:
            # Log-spaced candidates for better coverage
            idx = np.unique(np.logspace(0, np.log10(len(uniq) - 1), 80, dtype=int))
            xmin_candidates = uniq[idx]
        else:
            xmin_candidates = uniq
    else:
        xmin_candidates = np.array(xmin_candidates)

    xmin_candidates = np.unique(xmin_candidates[xmin_candidates <= data[-1]])

    best_ks = np.inf
    best_alpha = None
    best_xmin = None

    for xm in xmin_candidates:
        mask = data >= xm
        tail = data[mask]
        n_tail = len(tail)
        if n_tail < 10:
            continue

        # MLE for α given xmin (discrete estimator)
        alpha_mle = 1.0 + n_tail / np.sum(np.log(tail / (xm - 0.5)))

        # KS test using FAST continuous CCDF approximation
        # Sub-sample tail for KS if very large
        if n_tail > 50000:
            step = n_tail // 50000
            ks_tail = tail[::step]
            empirical = np.arange(1, len(ks_tail) + 1) / len(ks_tail)
            theo = 1.0 - _ccdf_approx(ks_tail, alpha_mle, xm)
        else:
            empirical = np.arange(1, n_tail + 1) / n_tail
            theo = 1.0 - _ccdf_approx(tail, alpha_mle, xm)

        ks = np.max(np.abs(empirical - theo))
        if ks < best_ks:
            best_ks = ks
            best_alpha = alpha_mle
            best_xmin = int(xm)

    # p-value via bootstrap (reduced iterations, sub-sampled)
    if best_alpha is not None:
        tail = data[data >= best_xmin]
        n_tail = len(tail)
        boot_n = min(n_tail, 20000)
        boot_ks_vals = []
        for _ in range(n_bootstrap):
            r = np.random.uniform(size=boot_n)
            synth = np.floor(best_xmin * (1 - r) ** (-1.0 / (best_alpha - 1.0)) + 0.5)
            synth = synth[synth >= best_xmin]
            if len(synth) < 10:
                continue
            alpha_b = 1.0 + len(synth) / np.sum(np.log(synth / (best_xmin - 0.5)))
            emp_b = np.arange(1, len(synth) + 1) / len(synth)
            theo_b = 1.0 - _ccdf_approx(synth, alpha_b, best_xmin)
            boot_ks_vals.append(np.max(np.abs(emp_b - theo_b)))

        pval = np.mean(np.array(boot_ks_vals) >= best_ks) if boot_ks_vals else 0.0
    else:
        pval = 0.0

    return best_alpha, best_xmin, best_ks, pval


# ===========================================================================
# Log-likelihood ratio: power-law vs alternative
# ===========================================================================

def llr_test(data, alpha_pl, xmin_pl, dist_name, dist_fit_fn):
    """
    Vuong log-likelihood ratio test: power-law vs alternative.
    Returns (R, p) where R>0 favours power-law, R<0 favours alternative.
    p is the two-sided p-value.
    """
    tail = np.array(data, dtype=np.float64)
    tail = tail[tail >= xmin_pl]
    n = len(tail)

    # Power-law log-likelihood
    ll_pl = discrete_pl_loglik(tail, alpha_pl, xmin_pl)

    # Alternative log-likelihood
    params = dist_fit_fn(tail)
    if dist_name == "lognormal":
        ll_alt = np.sum(lognorm.logpdf(tail, *params))
    elif dist_name == "exponential":
        ll_alt = np.sum(expon.logpdf(tail, *params))
    elif dist_name == "weibull":
        ll_alt = np.sum(weibull_min.logpdf(tail, *params))
    else:
        raise ValueError(dist_name)

    R = ll_pl - ll_alt
    # Vuong's test statistic
    lr = np.log(
        np.exp(ll_pl - ll_alt)
        / np.ones(n)
    )
    # Actually compute per-observation log-likelihood ratio
    # For discrete PL: log(p_pl(x)) = -alpha * log(x) - log(zeta(alpha, xmin))
    log_p_pl = -alpha_pl * np.log(tail) - np.log(_hurwitz_zeta(alpha_pl, xmin_pl))
    if dist_name == "lognormal":
        log_p_alt = lognorm.logpdf(tail, *params)
    elif dist_name == "exponential":
        log_p_alt = expon.logpdf(tail, *params)
    elif dist_name == "weibull":
        log_p_alt = weibull_min.logpdf(tail, *params)
    else:
        raise ValueError

    lr_i = log_p_pl - log_p_alt
    R = np.sum(lr_i)
    sigma = np.std(lr_i, ddof=1) * np.sqrt(n)
    if sigma == 0:
        return R, 1.0
    z = R / sigma
    from scipy.stats import norm
    p = 2.0 * norm.sf(abs(z))
    return R, p


# ===========================================================================
# Plotting
# ===========================================================================

def plot_ccdf(data, label, color, alpha, xmin, output_path):
    """CCDF with power-law fit overlay."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = np.sort(data[data > 0])
    ccdf = 1.0 - np.arange(len(data)) / len(data)

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.loglog(data, ccdf, ".", markersize=1.5, color=color, alpha=0.5,
              label=f"{label} (n={len(data):,})")

    # Power-law fit
    x_fit = np.logspace(np.log10(xmin), np.log10(data.max()), 200)
    y_fit = discrete_pl_ccdf(x_fit, alpha, xmin)
    ax.loglog(x_fit, y_fit, "-", color="black", linewidth=2,
              label=f"PL fit: α={alpha:.2f}, xᵣ={xmin}")

    ax.axvline(xmin, color="red", linestyle="--", alpha=0.5,
               label=f"x_min = {xmin}")

    ax.set_xlabel("Engagement count")
    ax.set_ylabel("P(X ≥ x)")
    ax.set_title(f"CCDF of {label}")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    textstr = f"α = {alpha:.3f}\nx_min = {xmin}\nn_tail = {np.sum(data >= xmin):,}"
    ax.text(0.95, 0.95, textstr, transform=ax.transAxes, fontsize=9,
            verticalalignment="top", horizontalalignment="right",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.9))

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ {output_path}")


def plot_compare_ccdf(data_dict, output_path):
    """CCDF of multiple engagement types on same plot."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"reposts": "#1d9bf0", "likes": "#e0245e", "replies": "#17bf63",
              "engagement": "#794bc4"}

    fig, ax = plt.subplots(figsize=(11, 7))
    for label, arr in data_dict.items():
        arr = np.sort(arr[arr > 0])
        ccdf = 1.0 - np.arange(len(arr)) / len(arr)
        ax.loglog(arr, ccdf, ".", markersize=1.2, color=colors.get(label, "gray"),
                  alpha=0.4, label=f"{label} (n={len(arr):,})")

    ax.set_xlabel("Count")
    ax.set_ylabel("P(X ≥ x)")
    ax.set_title("Engagement count distributions (top-level posts)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ {output_path}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    print("=" * 65)
    print("  Phase 1 — Power-law fit on engagement counts")
    print("=" * 65)
    print()

    conn = pymysql.connect(**DB_CONFIG)
    try:
        # ── Fetch data ──────────────────────────────────────────────────
        print("Fetching engagement counts …")
        sql = """
            SELECT total_reposts, total_likes, total_replies, total_engagement
            FROM post_lifetime
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

        total = len(rows)
        reposts  = np.array([r[0] for r in rows if r[0] > 0], dtype=np.int64)
        likes    = np.array([r[1] for r in rows if r[1] > 0], dtype=np.int64)
        replies  = np.array([r[2] for r in rows if r[2] > 0], dtype=np.int64)
        combined = np.array([r[3] for r in rows if r[3] > 0], dtype=np.int64)

        print(f"  Total posts: {total:,}")
        print(f"  With reposts:  {len(reposts):>10,}  ({100*len(reposts)/total:.1f}%)")
        print(f"  With likes:    {len(likes):>10,}  ({100*len(likes)/total:.1f}%)")
        print(f"  With replies:  {len(replies):>10,}  ({100*len(replies)/total:.1f}%)")
        print(f"  With any:      {len(combined):>10,}  ({100*len(combined)/total:.1f}%)")
        print()

        # ── Fit discrete power-law to each ──────────────────────────────
        datasets = [
            ("reposts", reposts),
            ("likes", likes),
            ("replies", replies),
            ("engagement", combined),
        ]
        colors = {"reposts": "#1d9bf0", "likes": "#e0245e", "replies": "#17bf63",
                  "engagement": "#794bc4"}

        for label, data in datasets:
            print(f"── Fitting {label} ──")
            alpha, xmin, ks, pval = fit_discrete_powerlaw(data)
            tail_n = np.sum(data >= xmin)

            print(f"  α       = {alpha:.4f}")
            print(f"  x_min   = {xmin}")
            print(f"  n_tail  = {tail_n:,} ({100*tail_n/len(data):.1f}% of engaged)")
            print(f"  KS stat = {ks:.5f}")
            print(f"  p-value = {pval:.4f}  {'✓ plausible PL' if pval > 0.1 else '✗ reject PL'}")

            # Compare vs alternatives
            for alt in ["lognormal", "exponential", "weibull"]:
                try:
                    R, p_llr = llr_test(data, alpha, xmin, alt,
                                        lambda d, a=alt: _fit_alt(d, a))
                    direction = "PL favoured" if R > 0 else f"{alt} favoured"
                    print(f"    vs {alt:<12s}: R={R:+.1f}, p={p_llr:.3f}  ({direction})")
                except Exception as e:
                    print(f"    vs {alt:<12s}: fit failed ({e})")

            print()

            # Plot individual CCDF
            plot_ccdf(data, label, colors.get(label, "gray"), alpha, xmin,
                      RESULTS / f"powerlaw_ccdf_{label}.png")

        # ── Comparison plot ─────────────────────────────────────────────
        plot_compare_ccdf(
            {"reposts": reposts, "likes": likes, "replies": replies},
            RESULTS / "powerlaw_counts_compare.png"
        )

        print("Done.")
    finally:
        conn.close()


def _fit_alt(data, dist_name):
    """Fit alternative distribution, return (params)."""
    if dist_name == "lognormal":
        s, loc, scale = lognorm.fit(data, floc=0)
        return (s, loc, scale)
    elif dist_name == "exponential":
        loc, scale = expon.fit(data, floc=0)
        return (loc, scale)
    elif dist_name == "weibull":
        c, loc, scale = weibull_min.fit(data, floc=0)
        return (c, loc, scale)
    else:
        raise ValueError(dist_name)


if __name__ == "__main__":
    main()
