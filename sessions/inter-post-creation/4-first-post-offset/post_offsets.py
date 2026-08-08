"""Within-session post offsets: at which second of the session each post lands.

Justifies the first-post-offset work: posts cluster in the first seconds of a
session. For every post inside a session, offset = post_time - session_start.

Output: plots/post_offsets_hist.png  (1s bins, capped at 60s)
        post_offsets_stats.tsv       (median, mean, min, P25, P90, P99 over ALL offsets)

Usage:
    uv run inter-post-creation/4-first-post-offset/post_offsets.py [--n-dids 20000]
"""

import argparse
import bisect
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "table_creation"))
from _core import get_connection, _execute, TBL_PREFIX

BATCH_DIDS = 2000
MAX_S = 15

sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,  # no latex binary on this box; same as other scripts here
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-dids", type=int, default=20_000)
    args = ap.parse_args()

    conn = get_connection()
    dids = [r[0] for r in _execute(conn, f"SELECT did FROM {TBL_PREFIX}sessions_users ORDER BY did")]
    stride = max(1, len(dids) // args.n_dids)
    dids = dids[::stride]
    print(f"{len(dids):,} sampled DIDs (stride {stride})", file=sys.stderr)

    offsets = []
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
            for t in posts.get(did, []):
                j = bisect.bisect_right(starts, t) - 1
                if j >= 0 and t <= ivals[j][1]:
                    offsets.append((t - ivals[j][0]) / 1e6)
        print(f"\r  {i + len(batch):,}/{len(dids):,}", end="", file=sys.stderr)
    conn.close()

    off = np.array(offsets)
    pct = {p: float(np.percentile(off, p)) for p in (25, 90, 99)}
    stats = pd.DataFrame([{
        "n_posts": len(off),
        "min": off.min(),
        "p25": pct[25],
        "median": float(np.median(off)),
        "mean": off.mean(),
        "p90": pct[90],
        "p99": pct[99],
        f"pct_within_{MAX_S}s": 100 * (off <= MAX_S).mean(),
    }])
    out_tsv = HERE / "post_offsets_stats.tsv"
    stats.round(2).to_csv(out_tsv, sep="\t", index=False)
    print(f"\n\n{stats.round(2).to_string(index=False)}\n→ {out_tsv}", file=sys.stderr)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.hist(off[off <= MAX_S], bins=np.arange(0, MAX_S + 2),
            weights=np.full((off <= MAX_S).sum(), 100.0 / len(off)),
            color=sns.color_palette("colorblind")[0], edgecolor="white")
    ax.set_xlim(0, MAX_S)
    ax.set_xlabel("seconds since session start")
    ax.set_ylabel("share of all session posts (%)")
    ax.set_title(f"Post timing within sessions (first {MAX_S}s, 1s bins)")
    fig.tight_layout()
    p = HERE / "plots" / "post_offsets_hist.png"
    p.parent.mkdir(exist_ok=True)
    fig.savefig(p, dpi=300)
    print(f"→ {p}", file=sys.stderr)


if __name__ == "__main__":
    main()
