"""
§2 — Session duration.

Histogram, log-log, PDF for both overall durations and per-user median durations.
One file per plot, per source.
"""

import sys

from _common import (
    Source,
    fetch_column,
    fetch_per_user_stats,
    get_connection,
    print_percentiles,
    save_hist,
    save_loglog,
    save_pdf,
)


def run(source: Source):
    """Produce all §2 plots for a single source."""
    print(f"\n── §2: Session duration — {source.value} ──", file=sys.stderr)

    conn = get_connection()

    # ── Overall durations ──
    durations = fetch_column(conn, source.table, "duration_s")
    print_percentiles(durations, f"duration_s ({source.value})")

    save_hist(durations, source, "02", "Duration (hist)",
              xlabel="Session duration (s)")
    save_loglog(durations, source, "02", "Duration (log-log)",
                xlabel="Session duration (s)")
    save_pdf(durations, source, "02", "Duration (PDF)",
             xlabel="Session duration (s)")

    # ── Per-user median durations ──
    stats = fetch_per_user_stats(conn, source.table)
    median_dur = stats["median_dur"]
    print_percentiles(median_dur, f"per-user median duration ({source.value})")

    save_hist(median_dur, source, "02", "Per-user median duration (hist)",
              xlabel="Per-user median duration (s)")
    save_loglog(median_dur, source, "02", "Per-user median duration (log-log)",
                xlabel="Per-user median duration (s)")
    save_pdf(median_dur, source, "02", "Per-user median duration (PDF)",
             xlabel="Per-user median duration (s)")

    conn.close()
