"""Posts per session: the count-side showcase of the within-session fitting problem.

For every session, count how many post creations fall inside it. Most sessions
contain zero or one post (66% / 24%): sessions are short AND users do not post
much inside them, so the within-session inter-post sample is intrinsically
tiny.

Per-session (post count, duration s) is cached in results/posts_per_session_cache.npz
after the first DB pass, so reruns only replot. Delete the cache to recompute.
The length-normalized rate lives in the sister script posts_per_minute.py.

Output: plots/posts_per_session.png (counts, % labels).

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
CACHE = HERE / "results" / "posts_per_session_cache.npz"

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})


def fetch():
    """Per-session (post count, duration s) — cache first, else DB pass."""
    if CACHE.exists():
        z = np.load(CACHE)
        print(f"→ loaded {len(z['count']):,} sessions from {CACHE.name}", file=sys.stderr)
        return z["count"], z["duration_s"]
    conn = get_connection()
    dids = [r[0] for r in _execute(conn, f"SELECT did FROM {TBL_PREFIX}sessions_users ORDER BY did")]
    print(f"{len(dids):,} users", file=sys.stderr)

    count_batches, dur_batches = [], []
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

        cs, ds = [], []
        for did, iv in sessions.items():
            starts = [s for s, _ in iv]
            per_owner = defaultdict(int)
            for t in posts.get(did, []):
                j = bisect.bisect_right(starts, t) - 1
                o = j if j >= 0 and t <= iv[j][1] else None
                per_owner[o] += 1
            cs.extend(per_owner.get(k, 0) for k in range(len(iv)))
            ds.extend((iv[j][1] - iv[j][0]) // 1_000_000 for j in range(len(iv)))
        count_batches.append(np.fromiter(cs, np.int32))
        dur_batches.append(np.fromiter(ds, np.int32))
        if (i // BATCH_DIDS) % 10 == 0:
            print(f"\r  {i + len(batch):,}/{len(dids):,}", end="", file=sys.stderr, flush=True)
    conn.close()

    counts = np.concatenate(count_batches)
    durations = np.maximum(np.concatenate(dur_batches), 1)  # zero-length guard
    np.savez_compressed(CACHE, count=counts, duration_s=durations)
    print(f"\n→ cached per-session data to {CACHE}", file=sys.stderr)
    return counts, durations


def main():
    counts, _ = fetch()
    n = len(counts)
    print(f"\nsessions: {n:,}   posts/session: "
          f"mean {counts.mean():.3f}  p50 {np.median(counts):.0f}  "
          f"p90 {np.percentile(counts, 90):.0f}  p99 {np.percentile(counts, 99):.0f}  "
          f"max {counts.max():.0f}", file=sys.stderr)

    dist = np.bincount(counts)
    tail6 = dist[6:].sum()
    pct = np.concatenate([dist[:6] / n * 100, [tail6 / n * 100]])
    for k in range(6):
        print(f"  {k} posts: {100 * dist[k] / n:.2f}%", file=sys.stderr)
    print(f"  >=6 posts: {100 * tail6 / n:.2f}%", file=sys.stderr)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.bar(np.arange(7), pct, color=sns.color_palette("colorblind")[0], width=0.7)
    ax.bar_label(ax.containers[0], fmt="%.1f", padding=2, fontsize=9)
    ax.set_xticks(np.arange(7))
    ax.set_xticklabels(["0", "1", "2", "3", "4", "5", "6+"])
    ax.set_xlabel("posts per session")
    ax.set_ylabel("share of sessions (%)")
    ax.set_title("Post creations per session")
    ax.set_ylim(0, pct.max() * 1.15)
    fig.tight_layout()
    p = HERE / "plots" / "posts_per_session.png"
    fig.savefig(p, dpi=300)
    print(f"→ {p}", file=sys.stderr)


if __name__ == "__main__":
    main()
