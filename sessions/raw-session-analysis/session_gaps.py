"""
§3 — Inter-session gaps.

Raw (all sessions) + real (duration_s > 0) side-by-side.
Histogram, log-log, PDF for both overall gaps and per-user median gaps.
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
    set_subdir,
)


def _fetch_gaps(conn, table: str, where: str | None = None) -> np.ndarray:
    """Fetch all inter-session gaps (seconds) for a table.

    If *where* is provided, filters sessions first (e.g. duration_s > 0).
    """
    sql = f"""
        SELECT did, session_start, session_end
        FROM {table}
    """
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY did, session_start"

    print(f"  Fetching gaps{'' if not where else ' (' + where + ')'} ...", file=sys.stderr)
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


def _run_one(source: Source, conn, where: str | None, tag: str):
    """Produce gap plots for a single filter variant."""
    subdir = "non_zero_gaps" if tag == "real" else "gaps"
    set_subdir(subdir)
    label_suffix = f" ({tag})" if tag else ""

    # ── Per-user median gaps ──
    stats = fetch_per_user_stats(conn, source.table, where=where)
    median_gap = stats["median_gap"]
    print_percentiles(median_gap, f"per-user median gap ({source.value}{label_suffix})")

    safe_tag = tag.replace(" ", "_") if tag else "all"

    pfx = f"Per-user median gap ({tag})" if tag else "Per-user median gap"
    save_hist(median_gap, source, "03", f"{pfx} (hist)",
              xlabel="Per-user median gap (s)")
    save_loglog(median_gap, source, "03", f"{pfx} (log-log)",
                xlabel="Per-user median gap (s)")
    save_pdf(median_gap, source, "03", f"{pfx} (PDF)",
             xlabel="Per-user median gap (s)")

    # ── All gaps (overall) ──
    all_gaps = _fetch_gaps(conn, source.table, where=where)
    print_percentiles(all_gaps, f"all gaps ({source.value}{label_suffix})")

    gfx = f"All gaps ({tag})" if tag else "All gaps"
    save_hist(all_gaps, source, "03", f"{gfx} (hist)",
              xlabel="Inter-session gap (s)")
    save_loglog(all_gaps, source, "03", f"{gfx} (log-log)",
                xlabel="Inter-session gap (s)")
    save_pdf(all_gaps, source, "03", f"{gfx} (PDF)",
             xlabel="Inter-session gap (s)")


def run(source: Source):
    """Produce all §3 plots for a single source — raw + real."""
    print(f"\n── §3: Inter-session gaps — {source.value} ──", file=sys.stderr)

    conn = get_connection()

    # Raw: all sessions
    print("\n  [raw — all sessions]", file=sys.stderr)
    _run_one(source, conn, where=None, tag="raw")

    # Filtered: real sessions only (duration > 0)
    print("\n  [real — duration_s > 0]", file=sys.stderr)
    _run_one(source, conn, where="duration_s > 0", tag="real")

    conn.close()
