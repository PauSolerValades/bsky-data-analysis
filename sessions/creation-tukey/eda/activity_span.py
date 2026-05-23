#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "polars",
#     "matplotlib",
#     "seaborn",
#     "numpy",
# ]
# ///
"""
§3 — Activity span & density.

Analyses how users' activity is spread across the 8-day window:
- Active days count (distinct calendar days the user appeared)
- Events per active day (the honest density metric)
- Activity span (first → last event in hours)
- Separates binge users from consistent users.
"""

import sys
from pathlib import Path

_EDA_DIR = Path(__file__).resolve().parent
if str(_EDA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_EDA_DIR.parent))

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns
from matplotlib.colors import LogNorm

from _common import load_or_fetch_stats, savefig, set_mpl_style, log_spaced_bins

OUT_DIR = Path(__file__).resolve().parent / "results"


def plot_span_and_density(df: pl.DataFrame):
    """Multi-panel figure: active_days histogram, events_per_active_day histogram,
    and hexbin of the two against each other."""
    set_mpl_style()

    # Prep data
    sub = df.filter(pl.col("total_events") > 0)
    ad = sub["active_days"].to_numpy()
    epad = sub["events_per_active_day"].to_numpy()
    span_h = sub["span_hours"].to_numpy()
    total = sub["total_events"].to_numpy()

    fig = plt.figure(figsize=(18, 12))

    # ---- Panel 1: Active days histogram ----
    ax1 = fig.add_subplot(2, 3, 1)
    max_days = int(ad.max())
    bins_ad = np.arange(0.5, max_days + 1.5, 1)
    ax1.hist(ad, bins=bins_ad, color="#4A90D9", alpha=0.85, edgecolor="white", linewidth=0.3)
    ax1.set_xlabel("Active days (of 8)")
    ax1.set_ylabel("Number of users")
    ax1.set_title("Active days per user")
    ax1.grid(True, alpha=0.3)
    # Annotate
    for day in [1, 2, 4, 8]:
        ax1.axvline(x=day, color="#D94A4A", linestyle=":", linewidth=0.8, alpha=0.5)

    # ---- Panel 2: Events per active day histogram ----
    ax2 = fig.add_subplot(2, 3, 2)
    bins_epad = log_spaced_bins(epad[epad > 0], 40)
    ax2.hist(epad, bins=bins_epad, color="#27AE60", alpha=0.85, edgecolor="none")
    ax2.set_xscale("log")
    ax2.set_yscale("log")
    ax2.set_xlabel("Events per active day")
    ax2.set_ylabel("Number of users")
    ax2.set_title("Events per active day")
    ax2.grid(True, alpha=0.3)
    for v, label in [(1, "1/day"), (10, "10/day"), (50, "50/day"), (100, "100/day (bot)")]:
        ax2.axvline(x=v, color="#D94A4A", linestyle=":", linewidth=0.8, alpha=0.5)
        ax2.text(v * 1.05, ax2.get_ylim()[1] * 0.5, label, fontsize=7,
                 rotation=90, color="#D94A4A", va="top")

    # ---- Panel 3: Span (hours) histogram ----
    ax3 = fig.add_subplot(2, 3, 3)
    span_h_filtered = span_h[span_h > 0]
    bins_span = log_spaced_bins(span_h_filtered, 30)
    ax3.hist(span_h_filtered, bins=bins_span, color="#8E44AD", alpha=0.85, edgecolor="none")
    ax3.set_xscale("log")
    ax3.set_xlabel("Activity span (hours)")
    ax3.set_ylabel("Number of users")
    ax3.set_title("Activity span (first → last event)")
    ax3.grid(True, alpha=0.3)
    # 8 days = 192 hours
    ax3.axvline(x=192, color="#D94A4A", linestyle="--", label="Full 8-day window")
    ax3.axvline(x=24, color="#E67E22", linestyle=":", label="1 day")
    ax3.legend(fontsize=7)

    # ---- Panel 4: Hexbin: events_per_active_day vs active_days ----
    ax4 = fig.add_subplot(2, 3, 4)
    mask = (epad > 0) & (ad > 0)
    hb = ax4.hexbin(ad[mask], epad[mask], gridsize=30, cmap="YlOrRd",
                    norm=LogNorm(), mincnt=1)
    ax4.set_yscale("log")
    ax4.set_xlabel("Active days")
    ax4.set_ylabel("Events per active day")
    ax4.set_title("Density vs consistency")
    plt.colorbar(hb, ax=ax4, label="Users (log)")
    ax4.grid(True, alpha=0.3)

    # ---- Panel 5: Hexbin: events_per_active_day vs total_events ----
    ax5 = fig.add_subplot(2, 3, 5)
    mask2 = total > 0
    hb2 = ax5.hexbin(total[mask2], epad[mask2], gridsize=50, cmap="YlOrRd",
                     norm=LogNorm(), mincnt=1)
    ax5.set_xscale("log")
    ax5.set_yscale("log")
    ax5.set_xlabel("Total events")
    ax5.set_ylabel("Events per active day")
    ax5.set_title("Density vs total activity")
    plt.colorbar(hb2, ax=ax5, label="Users (log)")
    ax5.grid(True, alpha=0.3)
    # Line for 1 active day (if all events in 1 day, dots are on this line)
    # Line for 8 active days
    ax5.plot([1, 1e5], [1/8, 1e5/8], "k--", linewidth=0.8, alpha=0.3, label="8 active days")
    ax5.plot([1, 1e5], [1, 1e5], "k:", linewidth=0.8, alpha=0.3, label="1 active day")
    ax5.legend(fontsize=7)

    # ---- Panel 6: Span vs total events ----
    ax6 = fig.add_subplot(2, 3, 6)
    mask3 = (span_h > 0) & (total > 0)
    hb3 = ax6.hexbin(total[mask3], span_h[mask3], gridsize=50, cmap="YlOrRd",
                     norm=LogNorm(), mincnt=1)
    ax6.set_xscale("log")
    ax6.set_xlabel("Total events")
    ax6.set_ylabel("Activity span (hours)")
    ax6.set_title("Span vs total activity")
    plt.colorbar(hb3, ax=ax6, label="Users (log)")
    ax6.grid(True, alpha=0.3)
    ax6.axhline(y=192, color="#D94A4A", linestyle="--", alpha=0.5, label="8 days")
    ax6.legend(fontsize=7)

    fig.tight_layout()
    savefig(fig, "03_activity_span.png")


def compute_binge_breakdown(df: pl.DataFrame) -> str:
    """Categorize users as binge vs consistent and return summary text."""
    # Binge: all events in 1 active day, or span < 1 hour with many events
    one_day = df.filter(pl.col("active_days") == 1)
    two_days = df.filter(pl.col("active_days") == 2)
    three_plus = df.filter(pl.col("active_days") >= 3)
    all_8 = df.filter(pl.col("active_days") >= 7)

    n = len(df)
    lines = [
        f"  Active in 1 day only:  {len(one_day):>10,}  ({100*len(one_day)/n:.1f}%)",
        f"  Active in 2 days:      {len(two_days):>10,}  ({100*len(two_days)/n:.1f}%)",
        f"  Active in 3+ days:     {len(three_plus):>10,}  ({100*len(three_plus)/n:.1f}%)",
        f"  Active in 7–8 days:    {len(all_8):>10,}  ({100*len(all_8)/n:.1f}%)",
        "",
        "  High-activity 1-day bingers (≥50 events, active_days=1):",
    ]
    bingers = df.filter((pl.col("active_days") == 1) & (pl.col("total_events") >= 50))
    lines.append(f"    {len(bingers):,} users — possible bots or scheduled accounts")
    lines.append(f"    They produce {bingers['total_events'].sum():,} events total")

    return "\n".join(lines)


def run(force_reload: bool = False) -> dict:
    """Run §3 and return results dict."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_or_fetch_stats(force=force_reload)

    plot_span_and_density(df)
    binge_text = compute_binge_breakdown(df)

    # Summary
    sub = df.filter(pl.col("total_events") > 0)
    epad = sub["events_per_active_day"].to_numpy()
    ad = sub["active_days"].to_numpy()

    lines = [
        "=== §3: Activity span & density ===",
        f"Users with events: {len(sub):,}",
        f"Active days — median: {np.median(ad):.1f}, mean: {np.mean(ad):.1f}",
        f"Events/active day — median: {np.percentile(epad, 50):.1f}, mean: {np.mean(epad):.1f}",
        "",
        "--- Binge vs Consistent ---",
        binge_text,
    ]
    out = "\n".join(lines)
    (OUT_DIR / "03_summary.txt").write_text(out)
    print(f"\n{out}", file=sys.stderr)

    return {
        "section": "§3 — Activity span",
        "median_active_days": float(np.median(ad)),
        "median_events_per_active_day": float(np.percentile(epad, 50)),
        "pct_one_day": 100 * (ad == 1).sum() / len(ad),
        "pct_full_8_days": 100 * (ad >= 7).sum() / len(ad),
    }


if __name__ == "__main__":
    run()
