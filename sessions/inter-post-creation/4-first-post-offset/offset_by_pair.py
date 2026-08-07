"""Does the first-post offset law depend on the user's (dur, gap) family pair?

If quantiles per pair look alike -> one pooled offset ECDF for the sim.
If not -> per-pair offset ECDFs, same route as 3-ecdf/ecdf_by_pair.py.

Usage:
    uv run first_post_offset/offset_by_pair.py [--n-dids 20000]
"""

import argparse
import bisect
import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "table_creation"))
from _core import get_connection, _execute, TBL_PREFIX

WIDE = HERE.parents[1] / "distribution-fit" / "results" / "pair_params_wide.tsv"
BATCH_DIDS = 2000
MIN_SESSIONS = 500  # ponytail: below this quantiles are noise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-dids", type=int, default=20_000)
    args = ap.parse_args()

    pairs = pl.read_csv(WIDE, separator="\t", columns=["did", "dur_family", "gap_family"])
    pair_of = {r["did"]: (r["dur_family"], r["gap_family"]) for r in pairs.iter_rows(named=True)}

    conn = get_connection()
    dids = [d for d, in _execute(conn, f"SELECT did FROM {TBL_PREFIX}sessions_users ORDER BY did")
            if d in pair_of]
    stride = max(1, len(dids) // args.n_dids)
    dids = dids[::stride]
    print(f"{len(dids):,} sampled DIDs with pair (stride {stride})", file=sys.stderr)

    offsets = {}  # pair -> list of seconds
    for i in range(0, len(dids), BATCH_DIDS):
        batch = dids[i:i + BATCH_DIDS]
        ph = ",".join(["%s"] * len(batch))
        sessions = {}
        for did, s, e in _execute(conn, f"""
            SELECT did, session_start, session_end FROM {TBL_PREFIX}sessions
            WHERE did IN ({ph}) ORDER BY did, session_start""", batch):
            sessions.setdefault(did, []).append((s, e))
        posts = {}
        for did, t in _execute(conn, f"""
            SELECT did, time_us FROM {TBL_PREFIX}events
            WHERE did IN ({ph}) AND event_type IN ('post_top','post_reply')
            ORDER BY did, time_us""", batch):
            posts.setdefault(did, []).append(t)

        for did, ivals in sessions.items():
            starts = [s for s, _ in ivals]
            prev_o = None
            for t in posts.get(did, []):
                j = bisect.bisect_right(starts, t) - 1
                o = j if j >= 0 and t <= ivals[j][1] else None
                if o is not None and (prev_o is None or o != prev_o):
                    offsets.setdefault(pair_of[did], []).append((t - ivals[o][0]) / 1e6)
                prev_o = o
        print(f"\r  {i + len(batch):,}/{len(dids):,}", end="", file=sys.stderr)
    conn.close()

    qs = [0.25, 0.5, 0.75, 0.9, 0.99]
    print(f"\n\n{'pair':<28}{'n':>8}" + "".join(f"p{int(q*100):>6}" for q in qs)
          + f"{'mean':>7}{'<5s':>6}")
    rows = sorted(offsets.items(), key=lambda kv: -len(kv[1]))
    total = sum(len(v) for v in offsets.values())
    for (dur, gap), v in rows:
        if len(v) < MIN_SESSIONS:
            continue
        a = np.array(v)
        q = np.quantile(a, qs)
        print(f"{dur + ' / ' + gap:<28}{len(a):>8,}" + "".join(f"{x:>6.0f}" for x in q)
              + f"{a.mean():>7.0f}{(a < 5).mean():>6.1%}   ({len(v)/total:.1%})")


if __name__ == "__main__":
    main()
