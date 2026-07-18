"""
§3 — Inter-session gaps.

Histogram, log-log, PDF for both overall gaps and per-user median gaps.
One file per plot, per source.
"""

import sys
import time as time_mod

import numpy as np

from _common import (
    Source,
    fetch_per_user_stats,
    get_connection,
    print_percentiles,
    save_hist,
    save_loglog,
    save_pdf,
)


def _fetch_all_gaps(conn, table: str) -> np.ndarray:
    """Fetch all inter-session gaps (seconds) for a table."""
    sql = f"""
        SELECT did, session_start, session_end
        FROM {table}
        ORDER BY did, session_start
    """
    print(f"  Fetching all gaps from {table} ...", file=sys.stderr)
    t0 = time_mod.time()

    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    print(f"    → {len(rows):,} rows in {time_mod.time() - t0:.0f}s", file=sys.stderr)

    gaps = []
    prev_did = None
    prev_end = None
    for did, start, end in rows:
        start, end = int(start), int(end)
        if did == prev_did and prev_end is not None:
            gap = (start - prev_end) / 1_000_000
            if gap > 0:
                gaps.append(gap)
        prev_did = did
        prev_end = end

    result = np.array(gaps, dtype=np.float64)
    print(f"    → {len(result):,} gaps", file=sys.stderr)
    return result


def run(source: Source):
    """Produce all §3 plots for a single source."""
    print(f"\n── §3: Inter-session gaps — {source.value} ──", file=sys.stderr)

    conn = get_connection()
    stats = fetch_per_user_stats(conn, source.table)
    median_gap = stats["median_gap"]
    print_percentiles(median_gap, f"per-user median gap ({source.value})")

    # ── All gaps (overall) ──
    all_gaps = _fetch_all_gaps(conn, source.table)
    print_percentiles(all_gaps, f"all gaps ({source.value})")

    save_hist(all_gaps, source, "03", "All gaps (hist)",
              xlabel="Inter-session gap (s)")
    save_loglog(all_gaps, source, "03", "All gaps (log-log)",
                xlabel="Inter-session gap (s)")
    save_pdf(all_gaps, source, "03", "All gaps (PDF)",
             xlabel="Inter-session gap (s)")

    # ── Per-user median gaps ──
    save_hist(median_gap, source, "03", "Per-user median gap (hist)",
              xlabel="Per-user median gap (s)")
    save_loglog(median_gap, source, "03", "Per-user median gap (log-log)",
                xlabel="Per-user median gap (s)")
    save_pdf(median_gap, source, "03", "Per-user median gap (PDF)",
             xlabel="Per-user median gap (s)")

    conn.close()
