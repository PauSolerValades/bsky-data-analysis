"""Dump per-user inter-post gaps (within-session + global) to parquet chunks.

Posts: event_type IN ('post_top','post_reply') from pau_db.events (raw μs,
NOT deduped to seconds). Sessions: pau_db.sessions (DBSCAN e300 ms2).
A consecutive post pair is "within" iff both posts fall inside the same
session interval; "global" ignores session boundaries (the control series
that quantifies session-length truncation of the within gaps).

Output: data/chunk{K}.parquet (did, col, value) with
col in {"interpost_within", "interpost_global"}, value = seconds (float).
Compatible with distribution-fit/fit_chunk.R: GAP_SHIFT applies only to
col == "gap", so these columns pass through unshifted (correct: no eps
truncation here).

Usage:
    uv run inter-post-creation/dump.py --chunk 0
"""

import argparse
import bisect
import sys
import time
from collections import defaultdict
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "table_creation"))
from _core import get_connection, _execute, TBL_PREFIX

BATCH_DIDS = 2000


def user_gaps(posts_us: list[int], intervals: list[tuple[int, int]]):
    """Return (within, global) gap lists in seconds.

    posts_us: sorted post timestamps (μs).
    intervals: sorted non-overlapping [(start_us, end_us)].
    """
    if len(posts_us) < 2:
        return [], []
    starts = [s for s, _ in intervals]

    def owner(t):
        i = bisect.bisect_right(starts, t) - 1
        return i if i >= 0 and t <= intervals[i][1] else None

    within, global_ = [], []
    prev_t, prev_o = posts_us[0], owner(posts_us[0])
    for t in posts_us[1:]:
        o = owner(t)
        gap = (t - prev_t) / 1e6
        global_.append(gap)
        if o is not None and o == prev_o:
            within.append(gap)
        prev_t, prev_o = t, o
    return within, global_


def _group_sorted(rows):
    """Group [(did, v...)] rows already sorted by did into {did: [v...]}."""
    out = defaultdict(list)
    for row in rows:
        out[row[0]].append(row[1:] if len(row) > 2 else row[1])
    return out


def main():
    parser = argparse.ArgumentParser(description="Dump inter-post gaps to parquet chunks.")
    parser.add_argument("--chunk", type=int, required=True)
    parser.add_argument("--chunks", type=int, default=10)
    args = parser.parse_args()

    out = HERE / "data" / f"chunk{args.chunk}.parquet"
    out.parent.mkdir(exist_ok=True)

    conn = get_connection()
    dids = [r[0] for r in _execute(conn, f"SELECT did FROM {TBL_PREFIX}sessions_users ORDER BY did")]
    dids = dids[args.chunk::args.chunks]
    print(f"chunk {args.chunk}: {len(dids):,} users", file=sys.stderr, flush=True)

    buf_did, buf_col, buf_val = [], [], []
    n_users_post = n_users_within = n_zero = n_sub1s = 0
    t0 = time.time()

    for i in range(0, len(dids), BATCH_DIDS):
        batch = dids[i:i + BATCH_DIDS]
        ph = ",".join(["%s"] * len(batch))
        sessions = _group_sorted(_execute(conn, f"""
            SELECT did, session_start, session_end
            FROM {TBL_PREFIX}sessions
            WHERE did IN ({ph}) ORDER BY did, session_start
        """, batch))
        posts = _group_sorted(_execute(conn, f"""
            SELECT did, time_us
            FROM {TBL_PREFIX}events
            WHERE did IN ({ph}) AND event_type IN ('post_top','post_reply')
            ORDER BY did, time_us
        """, batch))

        for did, posts_us in posts.items():
            if len(posts_us) < 2:
                continue
            n_users_post += 1
            within, global_ = user_gaps(posts_us, sessions.get(did, []))
            n_zero += sum(1 for g in global_ if g <= 0)
            n_sub1s += sum(1 for g in global_ if 0 < g < 1)
            if within:
                n_users_within += 1
            for col, gaps in (("interpost_within", within), ("interpost_global", global_)):
                for g in gaps:
                    buf_did.append(did); buf_col.append(col); buf_val.append(g)

        if (i // BATCH_DIDS) % 10 == 0:
            print(f"\r  {i + len(batch):,}/{len(dids):,}", end="", file=sys.stderr, flush=True)

    conn.close()
    pl.DataFrame({"did": buf_did, "col": buf_col, "value": buf_val}).write_parquet(out)
    print(f"\n  → {out} ({len(buf_val):,} rows) in {(time.time()-t0)/60:.0f} min", file=sys.stderr)
    print(f"  users with ≥2 posts: {n_users_post:,} | with ≥1 within gap: {n_users_within:,}",
          file=sys.stderr)
    print(f"  global gaps ≤0 (same-μs posts): {n_zero:,} | 0<gap<1s: {n_sub1s:,}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
