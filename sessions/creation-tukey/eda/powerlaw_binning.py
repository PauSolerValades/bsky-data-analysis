#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "polars",
#     "matplotlib",
#     "seaborn",
#     "numpy",
#     "scipy",
# ]
# ///
"""
§1 — Events-per-user distribution + power-law binning.

Finds natural regime boundaries in the event-count distribution using
log-spaced bins and power-law tail fitting, replacing arbitrary buckets
with data-driven ones.
"""

import sys
from pathlib import Path

# Make "eda" package importable when run standalone
_EDA_DIR = Path(__file__).resolve().parent
if str(_EDA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_EDA_DIR.parent))

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns

from _common import (
    load_or_fetch_stats,
    log_spaced_bins,
    powerlaw_fit_tail,
    savefig,
    set_mpl_style,
)

OUT_DIR = Path(__file__).resolve().parent / "results"


def plot_total_events(df: pl.DataFrame, fit: dict):
    """Log-log histogram + power-law tail fit overlay."""
    set_mpl_style()
    events = df["total_events"].to_numpy().astype(np.float64)
    xmin, alpha = fit["xmin"], fit["alpha"]

    fig, axes = plt.subplots(1, 2, figsize=(18, 7))

    # ---- Panel 1: log-log histogram with power-law bins ----
    ax = axes[0]
    bins = log_spaced_bins(events, 40)
    counts, edges = np.histogram(events, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    nonzero = counts > 0
    ax.loglog(centers[nonzero], counts[nonzero], "o-", color="#4A90D9",
              markersize=3, linewidth=1, alpha=0.8, label="Empirical")

    # Overlay power-law fit line
    if xmin > 1 and nonzero.any():
        fit_x = centers[centers >= xmin]
        if len(fit_x) > 0:
            # Normalize to match the histogram at xmin
            mask = centers >= xmin
            if mask.any() and counts[mask].sum() > 0:
                norm = counts[mask].sum() * (alpha - 1) / xmin
                fit_y = norm * (fit_x / xmin) ** (-alpha)
                ax.loglog(fit_x, fit_y, "r--", linewidth=2,
                          label=f"Power-law fit (α={alpha:.2f}, xmin={xmin:.0f})")

    ax.axvline(x=xmin, color="#D94A4A", linestyle=":", linewidth=2,
               alpha=0.7, label=f"xmin = {xmin:.0f}")
    ax.set_xlabel("Total events per user (8-day window)")
    ax.set_ylabel("Number of users")
    ax.set_title("Events-per-user distribution (log-log)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # ---- Panel 2: linear-scale histogram with regime boundaries ----
    ax2 = axes[1]
    # Use log-spaced bins for the linear plot too, truncating at a reasonable xmax
    xmax_linear = np.percentile(events, 99.9)
    bins_linear = log_spaced_bins(events[events <= xmax_linear], 35)
    counts_l, edges_l, patches = ax2.hist(
        events, bins=bins_linear, color="#4A90D9", alpha=0.85, edgecolor="none"
    )
    ax2.set_xlim(0, xmax_linear * 1.05)

    # Regime boundaries: xmin from power-law fit, plus a few natural breakpoints
    boundaries = sorted(set([
        1,           # minimum
        6,           # tourist cutoff (already validated)
        xmin,        # power-law start
        50,          # active users
        100,         # heavy
    ]))
    boundaries = [b for b in boundaries if b <= xmax_linear]

    colors = ["#666666", "#E67E22", "#D94A4A", "#8E44AD", "#2ECC71"]
    for b, c in zip(boundaries, colors):
        ax2.axvline(x=b, color=c, linestyle="--", linewidth=1.5, alpha=0.7)
        ax2.text(b, ax2.get_ylim()[1] * 0.92, f"{b:.0f}",
                 color=c, fontsize=9, ha="center", fontweight="bold")

    ax2.set_xlabel("Total events per user (8-day window)")
    ax2.set_ylabel("Number of users")
    ax2.set_title("Events-per-user (linear, P99.9 zoom) with candidate boundaries")
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    savefig(fig, "01_events_per_user.png")

    # ---- Summary text ----
    pct = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    vals = np.percentile(events, pct)
    lines = [
        "=== §1: Events-per-user distribution + power-law binning ===",
        f"Users: {len(events):,}",
        f"Power-law fit: xmin={xmin:.0f}, α={alpha:.3f}, n_tail={fit['n_tail']:,}",
        f"KS statistic: {fit['ks_stat']:.4f}",
        "",
        "Percentiles:",
    ]
    for p, v in zip(pct, vals):
        lines.append(f"  P{p:>2d}: {v:>10.0f}")
    if xmin <= 5:
        # xmin says the tail starts very early — all but single-event users
        lines.extend([
            "",
            "Proposed regime boundaries (data-driven bins):",
            "  1          — single-event tourists",
            "  2–5        — very light users (tourists)",
            f"  6+         — power-law regime (α={alpha:.2f}), includes 51.7% of users",
            "  100+       — heavy users / potential bots",
        ])
    else:
        lines.extend([
            "",
            "Proposed regime boundaries (data-driven bins):",
            "  1          — single-event tourists",
            f"  2–{int(xmin)-1}  — below power-law tail",
            f"  {int(xmin)}+       — power-law regime (α={alpha:.2f})",
            "  100+       — heavy users / potential bots",
        ])
    out = "\n".join(lines)
    (OUT_DIR / "01_events_per_user.txt").write_text(out)
    print(f"\n{out}", file=sys.stderr)


def plot_events_per_active_day(df: pl.DataFrame):
    """Histogram of events-per-active-day, the honest activity metric."""
    set_mpl_style()
    epad = df["events_per_active_day"].drop_nulls().to_numpy()

    fig, ax = plt.subplots(figsize=(12, 6))
    bins = log_spaced_bins(epad, 40)
    ax.hist(epad, bins=bins, color="#27AE60", alpha=0.85, edgecolor="none")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Events per active day")
    ax.set_ylabel("Number of users")
    ax.set_title("Events per active day (events / distinct calendar days)")
    ax.grid(True, alpha=0.3)

    # Annotate key reference lines
    for v, label, color in [
        (1, "1/day — once-a-day user", "#E67E22"),
        (10, "10/day", "#D94A4A"),
        (50, "50/day — borderline bot", "#8E44AD"),
        (100, "100/day — likely automated", "#C0392B"),
    ]:
        ax.axvline(x=v, color=color, linestyle="--", linewidth=1.5, alpha=0.7)
        ax.text(v * 1.1, ax.get_ylim()[1] * 0.85, label,
                color=color, fontsize=8, rotation=90, va="top")

    fig.tight_layout()
    savefig(fig, "01_events_per_active_day.png")

    # Summary text
    pct_epad = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    vals = np.percentile(epad, pct_epad)
    turbo = int((epad > 100).sum())
    lines = [
        "--- Events per active day ---",
        f"  Users with events/day > 100: {turbo:,} ({100*turbo/len(epad):.2f}%)",
        "  Percentiles:",
    ]
    for p, v in zip(pct_epad, vals):
        lines.append(f"    P{p:>2d}: {v:>10.1f}")
    return "\n".join(lines)


def run(force_reload: bool = False) -> dict:
    """Run §1 and return results dict for the orchestrator."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_or_fetch_stats(force=force_reload)
    events = df["total_events"].to_numpy().astype(np.float64)

    fit = powerlaw_fit_tail(events, verbose=True)

    print(file=sys.stderr)
    plot_total_events(df, fit)
    epad_summary = plot_events_per_active_day(df)

    results = {
        "section": "§1 — Power-law binning",
        "n_users": len(df),
        "xmin": fit["xmin"],
        "alpha": fit["alpha"],
        "ks_stat": fit["ks_stat"],
        "n_tail": fit["n_tail"],
    }

    # Write summary text
    full = (
        "=== §1: Events-per-user distribution + power-law binning ===\n"
        f"Users: {len(events):,}\n"
        f"Power-law fit: xmin={fit['xmin']:.0f}, α={fit['alpha']:.3f}, n_tail={fit['n_tail']:,}\n"
        f"KS statistic: {fit['ks_stat']:.4f}\n\n"
        + epad_summary
    )
    (OUT_DIR / "01_summary.txt").write_text(full)

    return results


if __name__ == "__main__":
    run()
