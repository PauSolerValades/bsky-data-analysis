"""First-post offset (session start -> first post) vs within inter-post gaps.

Answers: can the sim schedule the first post of a session with the same
within-gap ECDF used for post->post gaps, or is the offset a different law?

Samples a strided set of DIDs, computes per session:
  offset = first_post_time - session_start  (seconds, sessions with >=1 post)
and compares its quantiles to the within-gap ECDF quantiles.

Usage:
    uv run inter-post-creation/first_post_offset.py [--n-dids 20000]
"""

import argparse
import bisect
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "table_creation"))
from _core import get_connection, _execute, TBL_PREFIX

BATCH_DIDS = 2000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-dids", type=int, default=20_000)
    args = ap.parse_args()

    conn = get_connection()
    dids = [r[0] for r in _execute(conn, f"SELECT did FROM {TBL_PREFIX}sessions_users ORDER BY did")]
    stride = max(1, len(dids) // args.n_dids)
    dids = dids[::stride]
    print(f"{len(dids):,} sampled DIDs (stride {stride})", file=sys.stderr)

    offsets, within = [], []
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
            prev_t, prev_o = None, None
            for t in posts.get(did, []):
                j = bisect.bisect_right(starts, t) - 1
                o = j if j >= 0 and t <= ivals[j][1] else None
                if o is not None:
                    if prev_o is None or o != prev_o:
                        offsets.append((t - ivals[o][0]) / 1e6)  # first post in this session
                    elif prev_t is not None:
                        within.append((t - prev_t) / 1e6)
                prev_t, prev_o = t, o
        print(f"\r  {i + len(batch):,}/{len(dids):,}", end="", file=sys.stderr)

    conn.close()
    off = np.array(offsets)
    wit = np.array([w for w in within if w > 0])
    print(f"\n\nsessions with >=1 post: {len(off):,}   within gaps: {len(wit):,}")
    qs = [0.1, 0.25, 0.5, 0.75, 0.9, 0.99]
    print(f"{'':>14}" + "".join(f"p{int(q*100):>5}" for q in qs) + f"{'mean':>8}")
    for name, v in (("offset", off), ("within gap", wit)):
        q = np.quantile(v, qs)
        print(f"{name:>14}" + "".join(f"{x:>6.0f}" for x in q) + f"{v.mean():>8.0f}")
    print(f"\noffset < 5s: {(off < 5).mean():.1%}   offset/within median ratio: "
          f"{np.median(off) / np.median(wit):.2f}")


if __name__ == "__main__":
    main()
