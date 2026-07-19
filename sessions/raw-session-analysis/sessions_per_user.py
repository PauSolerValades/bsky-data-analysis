"""
§1 — Sessions per user.

Histogram, log-log, CCDF, PDF.  One file per plot, per source.
"""

import sys

import numpy as np
import powerlaw

from _common import (
    Source,
    fetch_per_user_stats,
    get_connection,
    print_percentiles,
    save_ccdf,
    save_hist,
    save_loglog,
    save_pdf,
    set_subdir,
)


def run(source: Source):
    """Produce all §1 plots for a single source."""
    set_subdir("sessions_per_user")
    print(f"\n── §1: Sessions per user — {source.value} ──", file=sys.stderr)

    conn = get_connection()
    stats = fetch_per_user_stats(conn, source.table)
    conn.close()

    n = stats["n_sessions"]
    print_percentiles(n, f"sessions/user ({source.value})")

    # Power-law fit
    fit = powerlaw.Fit(n, discrete=True, verbose=False)
    print(f"  Power-law: xmin={fit.xmin:.0f}, α={fit.alpha:.3f}, "
          f"σ={fit.sigma:.3f}, n_tail={np.sum(n >= fit.xmin)}", file=sys.stderr)
    R, p = fit.distribution_compare("power_law", "lognormal")
    print(f"  Power-law vs lognormal: R={R:.3f}, p={p:.4f}", file=sys.stderr)

    # One file per plot type
    save_hist(n, source, "01", "Sessions per user (hist)",
              xlabel="Sessions per user")
    save_loglog(n, source, "01", "Sessions per user (log-log)",
                xlabel="Sessions per user")
    save_pdf(n, source, "01", "Sessions per user (PDF)",
             xlabel="Sessions per user")
    save_ccdf(n, source, "01", "Sessions per user (CCDF)",
              xlabel="Sessions per user")
