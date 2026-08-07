"""Likes and reposts per session, per user.

For every session in pau_db.sessions, count feed_like and feed_repost
events (from pau_db.events) whose timestamp falls inside the session
window [session_start, session_end].

Output: results/likes_reposts_per_session.tsv.gz
    did, session_start, session_end, likes, reposts

Usage:
    uv run likes-reposts/likes_reposts_per_session.py
    uv run likes-reposts/likes_reposts_per_session.py demo   # self-check
"""

import gzip
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "table_creation"))
from _core import get_connection, _execute, TBL_PREFIX, load_dids, BATCH_SIZE

OUT = Path(__file__).resolve().parent / "results" / "likes_reposts_per_session.tsv.gz"

SESSIONS_SQL = f"""
    SELECT did, session_start, session_end
    FROM {TBL_PREFIX}sessions
    WHERE did IN ({{placeholders}})
    ORDER BY did, session_start
"""

TYPED_EVENTS_SQL = f"""
    SELECT did, time_us, event_type
    FROM {TBL_PREFIX}events
    WHERE did IN ({{placeholders}})
      AND event_type IN ('feed_like', 'feed_repost')
    ORDER BY did, time_us
"""


def group_by_did(rows, key_len):
    """Rows sorted by did -> {did: [tuple of remaining cols]} in one pass."""
    out, cur_did, cur = {}, None, []
    for row in rows:
        did, rest = row[0], row[1 : 1 + key_len]
        if did != cur_did:
            if cur_did is not None:
                out[cur_did] = cur
            cur_did, cur = did, [rest]
        else:
            cur.append(rest)
    if cur_did is not None:
        out[cur_did] = cur
    return out


def count_likes_reposts(sessions, evs):
    """Count like/repost events inside each session window.

    sessions: [(start_us, end_us)] sorted; evs: [(time_us, event_type)] sorted.
    Sessions per user are disjoint, so a single forward pass works.
    """
    counts = []
    i = 0
    n = len(evs)
    for s_start, s_end in sessions:
        while i < n and evs[i][0] < s_start:
            i += 1
        likes = reposts = 0
        j = i
        while j < n and evs[j][0] <= s_end:
            if evs[j][1] == "feed_like":
                likes += 1
            else:
                reposts += 1
            j += 1
        counts.append((likes, reposts))
        i = j
    return counts


def demo():
    sessions = [(100, 200), (300, 300)]
    evs = [(50, "feed_like"), (100, "feed_like"), (150, "feed_repost"),
           (200, "feed_like"), (300, "feed_repost"), (400, "feed_like")]
    assert count_likes_reposts(sessions, evs) == [(2, 1), (0, 1)]
    assert count_likes_reposts([], evs) == []
    assert count_likes_reposts(sessions, []) == [(0, 0), (0, 0)]
    print("demo OK")


def main():
    conn = get_connection()
    dids = load_dids(conn)
    print(f"{len(dids):,} DIDs", file=sys.stderr)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    n_sessions = 0

    with gzip.open(OUT, "wt") as f:
        f.write("did\tsession_start\tsession_end\tlikes\treposts\n")
        for batch_idx in range(0, len(dids), BATCH_SIZE):
            batch = dids[batch_idx : batch_idx + BATCH_SIZE]
            ph = ",".join(["%s"] * len(batch))
            sessions = group_by_did(_execute(conn, SESSIONS_SQL.format(placeholders=ph), batch), 2)
            evs = group_by_did(_execute(conn, TYPED_EVENTS_SQL.format(placeholders=ph), batch), 2)
            for did in batch:
                for (s_start, s_end), (likes, reposts) in zip(
                    sessions.get(did, []), count_likes_reposts(sessions.get(did, []), evs.get(did, []))
                ):
                    f.write(f"{did}\t{s_start}\t{s_end}\t{likes}\t{reposts}\n")
                    n_sessions += 1
            if (batch_idx // BATCH_SIZE + 1) % 10 == 0 or batch_idx + BATCH_SIZE >= len(dids):
                print(f"  {batch_idx + len(batch):,}/{len(dids):,} users | "
                      f"{n_sessions:,} sessions | {time.time() - t0:.0f}s", file=sys.stderr)

    print(f"Done: {n_sessions:,} sessions -> {OUT} ({time.time() - t0:.0f}s)", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        main()
