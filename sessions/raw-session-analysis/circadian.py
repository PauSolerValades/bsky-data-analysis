"""
§5 — Circadian density.

When do sessions start and end?  Plots UTC-hour distributions to validate
that Tukey-clustered sessions follow a human circadian pattern.
"""

import sys

import matplotlib.pyplot as plt
import numpy as np

from _common import (
    Source,
    get_connection,
    savefig,
    set_subdir,
    OUT,
    N_BINS,
)

SECONDS_PER_HOUR = 3600
US_PER_HOUR = 3_600_000_000


def run(source: Source):
    """Produce §5 plots for a single source."""
    set_subdir("circadian")
    print(f"\n── §5: Circadian density — {source.value} ──", file=sys.stderr)

    conn = get_connection()
    sql = f"""
        SELECT session_start, session_end, duration_s
        FROM {source.table}
        WHERE duration_s > 0
    """
    print(f"  Fetching {source.table} (real sessions only) ...", file=sys.stderr)
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    conn.close()
    print(f"    → {len(rows):,} sessions", file=sys.stderr)

    starts = np.array([r[0] for r in rows], dtype=np.int64)
    ends = np.array([r[1] for r in rows], dtype=np.int64)
    durations = np.array([r[2] for r in rows], dtype=np.float64)

    # Convert to UTC hour (fractional for KDE)
    start_hours = (starts / 1_000_000 / SECONDS_PER_HOUR) % 24
    end_hours = (ends / 1_000_000 / SECONDS_PER_HOUR) % 24

    # ── Histogram: session starts by hour ──
    _hour_hist(start_hours, source, "05", "Session start hour (UTC)",
               "session_start_hour", "Session starts by hour (UTC)")
    _hour_hist(end_hours, source, "05", "Session end hour (UTC)",
               "session_end_hour", "Session ends by hour (UTC)")

    # ── KDE: session starts ──
    _hour_kde(start_hours, source, "05", "Session start hour (UTC)",
              "session_start_hour_kde", "Session start density (UTC)")

    # ── Boxplot: duration by hour of start ──
    _duration_by_hour(start_hours, durations, source)

    print(f"  Peak start hour: {np.argmax(np.bincount(start_hours.astype(int)))},00 UTC", file=sys.stderr)
    print(f"  Peak end hour:   {np.argmax(np.bincount(end_hours.astype(int)))},00 UTC", file=sys.stderr)


def _hour_hist(hours: np.ndarray, source: Source, section: str,
               title: str, fname: str, xlabel: str, bins_per_hour: int = 4):
    """Histogram with configurable bin resolution."""
    n_bins = 24 * bins_per_hour
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.hist(hours, bins=n_bins, range=(0, 24), color=source.color,
            alpha=0.85, edgecolor="white", linewidth=0.3)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Number of sessions")
    ax.set_title(f"{source.label}\n{title}")
    ax.set_xticks(range(0, 24, 2))
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    savefig(fig, f"{section}_{source.value}_{fname}.png")


def _hour_kde(hours: np.ndarray, source: Source, section: str,
              title: str, fname: str, xlabel: str, bins_per_hour: int = 4):
    """KDE + histogram with configurable bin resolution."""
    from scipy.stats import gaussian_kde

    n_bins = 24 * bins_per_hour
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.hist(hours, bins=n_bins, range=(0, 24), density=True,
            color=source.color, alpha=0.3, edgecolor="none", label="Histogram")

    # KDE — pad edges to handle the midnight wrap
    padded = np.concatenate([hours - 24, hours, hours + 24])
    kde = gaussian_kde(padded, bw_method=0.3)
    xs = np.linspace(0, 24, 200)
    ax.plot(xs, kde(xs), "-", color=source.color, linewidth=2, label="KDE")

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Density")
    ax.set_title(f"{source.label}\n{title}")
    ax.set_xticks(range(0, 24, 2))
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    savefig(fig, f"{section}_{source.value}_{fname}.png")


def _duration_by_hour(start_hours: np.ndarray, durations: np.ndarray,
                      source: Source):
    """Boxplot: session duration broken down by start hour."""
    fig, ax = plt.subplots(figsize=(14, 6))

    box_data = []
    labels = []
    for h in range(24):
        mask = (start_hours >= h) & (start_hours < h + 1)
        d = durations[mask]
        if len(d) > 10:
            # Clip to P95 for visibility
            clip = np.percentile(d, 95)
            box_data.append(d[d <= clip])
            labels.append(f"{h:02d}h")

    bp = ax.boxplot(box_data, tick_labels=labels, patch_artist=True)
    for patch in bp["boxes"]:
        patch.set_facecolor(source.color)
        patch.set_alpha(0.7)

    ax.set_xlabel("Session start hour (UTC)")
    ax.set_ylabel("Session duration (s, P95 clipped)")
    ax.set_title(f"{source.label}\nSession duration by start hour (UTC)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    savefig(fig, f"05_{source.value}_duration_by_hour.png")
