"""
§2 — Session duration.

Raw (all sessions) + real (duration_s > 0) side-by-side.
Histogram, log-log, PDF for both overall durations and per-user median durations.
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
    set_subdir,
)


def _run_one(source: Source, conn, where: str | None, tag: str):
    """Produce duration plots for a single filter variant."""
    subdir = "non_zero_duration" if tag == "real" else "duration"
    set_subdir(subdir)
    label_suffix = f" ({tag})" if tag else ""

    # ── Overall durations ──
    durations = fetch_column(conn, source.table, "duration_s", where=where)
    print_percentiles(durations, f"duration_s ({source.value}{label_suffix})")

    save_hist(durations, source, "02", f"Duration (hist){label_suffix}",
              xlabel="Session duration (s)")
    save_loglog(durations, source, "02", f"Duration (log-log){label_suffix}",
                xlabel="Session duration (s)")
    save_pdf(durations, source, "02", f"Duration (PDF){label_suffix}",
             xlabel="Session duration (s)")

    # ── Per-user median durations ──
    stats = fetch_per_user_stats(conn, source.table, where=where)
    median_dur = stats["median_dur"]
    print_percentiles(median_dur, f"per-user median duration ({source.value}{label_suffix})")

    safe_tag = tag.replace(" ", "_") if tag else "all"
    pfx = f"Per-user median duration ({tag})" if tag else "Per-user median duration"

    save_hist(median_dur, source, "02", f"{pfx} (hist)",
              xlabel="Per-user median duration (s)")
    save_loglog(median_dur, source, "02", f"{pfx} (log-log)",
                xlabel="Per-user median duration (s)")
    save_pdf(median_dur, source, "02", f"{pfx} (PDF)",
             xlabel="Per-user median duration (s)")


def run(source: Source):
    """Produce all §2 plots for a single source — raw + real."""
    print(f"\n── §2: Session duration — {source.value} ──", file=sys.stderr)

    conn = get_connection()

    # Raw: all sessions
    print("\n  [raw — all sessions]", file=sys.stderr)
    _run_one(source, conn, where=None, tag="raw")

    # Filtered: real sessions only (duration > 0)
    print("\n  [real — duration_s > 0]", file=sys.stderr)
    _run_one(source, conn, where="duration_s > 0", tag="real")

    conn.close()
