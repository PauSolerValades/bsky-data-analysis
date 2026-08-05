"""Posts per session: the count-side showcase of the within-session fitting problem.

For every session, count how many post creations fall inside it. The point:
most sessions contain zero or one post --- sessions are short (median 110 s)
AND users simply do not post much inside them, by definition of a session as
an activity window dominated by non-post events. The within-session inter-post
sample is therefore intrinsically tiny; this is the data-side reason the
per-user within fits are starved, complementary to the AIC-margin and
truncation-simulation evidence (see the report's Within Creation Distribution
section).

Output: plots/posts_per_session.png + console summary.

Usage:
    uv run inter-post-creation/posts_per_session.py
"""

import bisect
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "table_creation"))
from _core import get_connection, _execute, TBL_PREFIX

BATCH_DIDS = 2000


def main():
    conn = get_connection()
    dids = [r[0] for r in _execute(conn, f"SELECT did FROM {TBL_PREFIX}sessions_users ORDER BY did")]
    print(f"{len(dids):,} users", file=sys.stderr)

    counts = []  # posts per session, over ALL sessions (zeros included)
    for i in range(0, len(dids), BATCH_DIDS):
        batch = dids[i:i + BATCH_DIDS]
        ph = ",".join(["%s"] * len(batch))
        sessions = defaultdict(list)
        for d, s, e in _execute(conn, f"""
            SELECT did, session_start, session_end FROM {TBL_PREFIX}sessions
            WHERE did IN ({ph}) ORDER BY did, session_start""", batch):
            sessions[d].append((int(s), int(e)))
        posts = defaultdict(list)
        for d, t in _execute(conn, f"""
            SELECT did, time_us FROM {TBL_PREFIX}events
            WHERE did IN ({ph}) AND event_type IN ('post_top','post_reply')
            ORDER BY did, time_us""", batch):
            posts[d].append(int(t))

        for did, iv in sessions.items():
            starts = [s for s, _ in iv]
            per_owner = defaultdict(int)
            for t in posts.get(did, []):
                j = bisect.bisect_right(starts, t) - 1
                o = j if j >= 0 and t <= iv[j][1] else None
                per_owner[o] += 1
            counts.extend(per_owner.get(k, 0) for k in range(len(iv)))
        if (i // BATCH_DIDS) % 10 == 0:
            print(f"\r  {i + len(batch):,}/{len(dids):,}", end="", file=sys.stderr, flush=True)
    conn.close()

    c = np.array(counts)
    print(f"\nsessions: {len(c):,}   posts/session: "
          f"mean {c.mean():.3f}  p50 {np.median(c):.0f}  "
          f"p90 {np.percentile(c, 90):.0f}  p99 {np.percentile(c, 99):.0f}  "
          f"max {c.max():.0f}", file=sys.stderr)
    for k in range(6):
        print(f"  {k} posts: {100 * np.mean(c == k):.2f}%", file=sys.stderr)
    print(f"  >=6 posts: {100 * np.mean(c >= 6):.2f}%", file=sys.stderr)

    cap = float(np.percentile(c, 99))
    fig, ax = plt.subplots(figsize=(7, 4.5))
    sns.histplot(c[c <= cap], bins=np.arange(0, cap + 2) - 0.5, stat="percent",
                 color=sns.color_palette("colorblind")[0], ax=ax)
    ax.set_xlabel("posts per session")
    ax.set_ylabel("share of sessions (%)")
    ax.set_title("Post creations per session")
    fig.tight_layout()
    out = HERE / "plots" / "posts_per_session.png"
    fig.savefig(out, dpi=300)
    print(f"→ {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
