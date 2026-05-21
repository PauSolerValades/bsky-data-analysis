#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "numpy",
# ]
# ///
"""
Inter-post gap analysis — time between consecutive posts authored by the
same user.

Two metrics:
  1. global          — all consecutive post/reply timestamps per user,
                        regardless of session boundaries.
  2. within_session  — consecutive post/reply timestamps within the same
                        session (using pau_db.sessions_tukey boundaries).

Output:
  data/inter_post_gaps.csv  — one row per inter-post gap, columns:
    did, gap_s, gap_type

  The CSV is compatible with the R distribution-fitting workflow
  (session-analysis/session_distribution_fit.R): group by `did` →
  per-user vectors of `gap_s`, fit power-law / lognormal / Weibull / etc.

Usage:
    uv run post-lifetime/inter_post_gaps.py
    uv run post-lifetime/inter_post_gaps.py --summary
"""

import argparse
import os
import sys
import time as time_mod
from collections import defaultdict
from pathlib import Path

import numpy as np
import pymysql


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def _load_env_file():
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
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


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


DB_CONFIG = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": _env("DATABASE_NAME", "bsky"),
    "charset": "utf8mb4",
}

BATCH_SIZE = 2000       # DIDs per fetch
INSERT_FLUSH = 50_000   # rows before flushing to CSV

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_CSV = OUTPUT_DIR / "inter_post_gaps.csv"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_post_events_for_dids(
    conn: pymysql.Connection,
    dids: list[str],
) -> dict[str, list[tuple[int, str, int | None, int | None]]]:
    """Return {did: [(time_us, event_type, session_start_us, session_end_us), ...]}
    sorted by time for each DID.

    Joins user_core_events to sessions_tukey to tag each event with
    its enclosing session. Events that don't fall in any session get
    NULL session_start/end (used for global gaps, excluded from
    within-session gaps).
    """
    if not dids:
        return {}

    placeholders = ",".join(["%s"] * len(dids))

    # Fetch post/reply events with their session boundaries via a LEFT JOIN.
    # A LEFT JOIN keeps events that are not in any session (session cols = NULL).
    query = f"""
        SELECT
            e.did,
            e.time_us,
            e.event_type,
            s.session_start,
            s.session_end
        FROM pau_db.user_core_events e
        LEFT JOIN pau_db.sessions_tukey s
            ON e.did = s.did
           AND e.time_us BETWEEN s.session_start AND s.session_end
        WHERE e.did IN ({placeholders})
          AND e.event_type IN ('post', 'reply')
        ORDER BY e.did, e.time_us
    """
    params = dids  # single list, used once

    result: dict[str, list[tuple[int, str, int | None, int | None]]] = defaultdict(list)
    with conn.cursor() as cur:
        cur.execute(query, params)
        for did, time_us, event_type, ss, se in cur:
            result[did].append((
                int(time_us),
                event_type,
                int(ss) if ss is not None else None,
                int(se) if se is not None else None,
            ))
    return dict(result)


# ---------------------------------------------------------------------------
# Gap computation
# ---------------------------------------------------------------------------

def compute_gaps(
    events: list[tuple[int, str, int | None, int | None]],
) -> tuple[list[float], list[float]]:
    """Compute global and within-session gaps from sorted events.

    Returns (global_gaps_s, within_session_gaps_s).
    Each entry is the gap in seconds to the previous post/reply event.

    Global: gap to previous event regardless of session.
    Within-session: gap to previous event *only if* both events share
    the same non-NULL session_start.
    """
    if len(events) < 2:
        return [], []

    global_gaps: list[float] = []
    within_gaps: list[float] = []

    prev_time_us: int = events[0][0]
    prev_ss: int | None = events[0][2]

    for i in range(1, len(events)):
        t_us, _etype, ss, _se = events[i]
        gap_s = (t_us - prev_time_us) / 1_000_000.0
        global_gaps.append(gap_s)

        # Same session = both events have non-NULL session_start AND they match
        if prev_ss is not None and ss is not None and prev_ss == ss:
            within_gaps.append(gap_s)

        prev_time_us = t_us
        prev_ss = ss

    return global_gaps, within_gaps


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Inter-post gap analysis (global + within-session)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help=f"DIDs per event-fetch query (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Print summary statistics after export",
    )
    args = parser.parse_args()

    # -----------------------------------------------------------------------
    # Connect
    # -----------------------------------------------------------------------
    conn = pymysql.connect(**DB_CONFIG)
    print(f"Connected to {DB_CONFIG['host']}:{DB_CONFIG['port']}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Get all DIDs with ≥2 post/reply events
    # -----------------------------------------------------------------------
    print("Finding users with ≥2 posts/replies ...", file=sys.stderr)
    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT did
            FROM pau_db.user_core_events
            WHERE event_type IN ('post', 'reply')
            GROUP BY did
            HAVING COUNT(*) >= 2
            ORDER BY did
        """)
        all_dids = [row[0] for row in cur]
    elapsed = time_mod.time() - t0
    print(f"  → {len(all_dids):,} DIDs in {elapsed:.0f}s", file=sys.stderr)

    if not all_dids:
        print("No users found. Exiting.", file=sys.stderr)
        conn.close()
        return

    # -----------------------------------------------------------------------
    # Process in batches, streaming to CSV
    # -----------------------------------------------------------------------
    batches = [
        all_dids[i:i + args.batch_size]
        for i in range(0, len(all_dids), args.batch_size)
    ]
    total_batches = len(batches)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_global: list[float] = []
    all_within: list[float] = []
    total_global_gaps = 0
    total_within_gaps = 0
    seen_users_global = set()
    seen_users_within = set()

    t0 = time_mod.time()

    with open(OUTPUT_CSV, "w") as f:
        # Header
        f.write("did,gap_s,gap_type\n")

        row_buffer: list[str] = []

        def flush_buffer():
            nonlocal row_buffer
            if not row_buffer:
                return
            f.write("".join(row_buffer))
            row_buffer.clear()

        for batch_idx, batch_dids in enumerate(batches):
            events_by_did = fetch_post_events_for_dids(conn, batch_dids)

            for did in batch_dids:
                events = events_by_did.get(did, [])
                if len(events) < 2:
                    continue

                global_gaps, within_gaps = compute_gaps(events)

                if global_gaps:
                    seen_users_global.add(did)
                    total_global_gaps += len(global_gaps)
                    all_global.extend(global_gaps[:10000])  # cap for memory
                    for g in global_gaps:
                        row_buffer.append(f"{did},{g:.6f},global\n")

                if within_gaps:
                    seen_users_within.add(did)
                    total_within_gaps += len(within_gaps)
                    all_within.extend(within_gaps[:10000])
                    for g in within_gaps:
                        row_buffer.append(f"{did},{g:.6f},within_session\n")

                if len(row_buffer) >= INSERT_FLUSH:
                    flush_buffer()

            flush_buffer()

            if (batch_idx + 1) % 10 == 0 or batch_idx == total_batches - 1:
                elapsed = time_mod.time() - t0
                pct = 100 * (batch_idx + 1) / total_batches
                rate = (batch_idx + 1) * args.batch_size / elapsed if elapsed > 0 else 0
                print(
                    f"  Batch {batch_idx + 1}/{total_batches} ({pct:.0f}%) | "
                    f"{total_global_gaps:,} global / {total_within_gaps:,} within-session gaps | "
                    f"{elapsed:.0f}s | ~{rate:.0f} users/s",
                    file=sys.stderr,
                )

    conn.close()

    elapsed = time_mod.time() - t0
    file_size_mb = OUTPUT_CSV.stat().st_size / 1e6
    print(f"\nDone in {elapsed:.0f}s", file=sys.stderr)
    print(f"Output: {OUTPUT_CSV} ({file_size_mb:.0f} MB)", file=sys.stderr)
    print(f"  Global gaps:         {total_global_gaps:,} from {len(seen_users_global):,} users", file=sys.stderr)
    print(f"  Within-session gaps: {total_within_gaps:,} from {len(seen_users_within):,} users", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    if args.summary:
        _print_summary(all_global, all_within)


def _print_summary(global_gaps: list[float], within_gaps: list[float]):
    def _stats(arr: list[float], label: str):
        a = np.array(arr, dtype=np.float64)
        a = a[a > 0]  # exclude zero gaps (same-second posts)
        if len(a) == 0:
            print(f"  {label}: no positive gaps", file=sys.stderr)
            return

        def p(pct):
            return np.percentile(a, pct)

        print(f"\n  ── {label} ──", file=sys.stderr)
        print(f"    n       = {len(a):>12,}", file=sys.stderr)
        print(f"    mean    = {np.mean(a):>10.1f} s  ({np.mean(a)/60:.1f} min)", file=sys.stderr)
        print(f"    median  = {np.median(a):>10.1f} s  ({np.median(a)/60:.1f} min)", file=sys.stderr)
        print(f"    std     = {np.std(a):>10.1f} s", file=sys.stderr)
        print(f"    min     = {np.min(a):>10.1f} s", file=sys.stderr)
        print(f"    max     = {np.max(a):>10.1f} s  ({np.max(a)/3600:.1f} h)", file=sys.stderr)
        print(f"    p25     = {p(25):>10.1f} s", file=sys.stderr)
        print(f"    p50     = {p(50):>10.1f} s", file=sys.stderr)
        print(f"    p75     = {p(75):>10.1f} s  ({p(75)/60:.1f} min)", file=sys.stderr)
        print(f"    p90     = {p(90):>10.1f} s  ({p(90)/60:.1f} min)", file=sys.stderr)
        print(f"    p95     = {p(95):>10.1f} s  ({p(95)/60:.1f} min)", file=sys.stderr)
        print(f"    p99     = {p(99):>10.1f} s  ({p(99)/3600:.1f} h)", file=sys.stderr)
        print(f"    p99.9   = {p(99.9):>10.1f} s  ({p(99.9)/3600:.1f} h)", file=sys.stderr)

    print("\n" + "=" * 60, file=sys.stderr)
    print("  INTER-POST GAP SUMMARY", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    _stats(global_gaps, "Global (all posts, same user)")
    _stats(within_gaps, "Within-session (same user, same session)")

    if len(global_gaps) > 0 and len(within_gaps) > 0:
        g = np.array(global_gaps, dtype=np.float64)
        w = np.array(within_gaps, dtype=np.float64)
        g = g[g > 0]
        w = w[w > 0]
        print(f"\n  Within-session median / global median = {np.median(w)/np.median(g):.2f}x",
              file=sys.stderr)
        print(f"  (within-session gaps are {np.median(g)/np.median(w):.1f}x smaller — bursty posting)",
              file=sys.stderr)

    print("=" * 60 + "\n", file=sys.stderr)


if __name__ == "__main__":
    main()
