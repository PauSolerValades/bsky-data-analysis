"""Event composition of very short sessions (<60s).

For each session table: how many short sessions exist, and how many
events do they contain (1 / 2 / 3-4 / 5-9 / 10+)?

Usage:
    uv run sessions/analysis/short_session_events.py
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "table_creation"))
from _core import get_connection, _execute, TBL_PREFIX, load_dids, fetch_user_timestamps

SHORT_S = 60
BATCH = 2000

TABLES = [f"sessions_dbscan_e{e}_ms2" for e in [5, 15, 30, 60, 300, 600]] + \
         [f"sessions_hdbscan_mcs2_ms1_e{e}" for e in [0, 60, 180, 300, 600]]

BUCKETS = ["1", "2", "3-4", "5-9", "10+"]


def bucket(n):
    if n == 1: return 0
    if n == 2: return 1
    if n <= 4: return 2
    if n <= 9: return 3
    return 4


def main():
    conn = get_connection()
    dids = load_dids(conn)
    print(f"{len(dids):,} DIDs", file=sys.stderr)

    # Cache short sessions per table: {did: [(start_us, end_us, is_singleton)]}
    print("table\t n_short\t" + "\t".join(BUCKETS))
    for t in TABLES:
        short = defaultdict(list)
        n_total = _execute(conn, f"SELECT COUNT(*) FROM {TBL_PREFIX}{t}")[0][0]
        cur = conn.cursor()
        cur.execute(f"""SELECT did, session_start, session_end, is_singleton
                        FROM {TBL_PREFIX}{t} WHERE duration_s < {SHORT_S}
                        ORDER BY did""")
        for did, s, e, sing in cur:
            short[did].append((int(s), int(e), int(sing)))
        cur.close()

        counts = np.zeros(5, dtype=np.int64)
        for i in range(0, len(dids), BATCH):
            batch = [d for d in dids[i:i + BATCH] if d in short]
            if not batch:
                continue
            events = fetch_user_timestamps(conn, batch)
            for did in batch:
                ev = np.array(sorted(events.get(did, [])), dtype=np.int64)
                for s, e, sing in short[did]:
                    n = (np.searchsorted(ev, e, side="right")
                         - np.searchsorted(ev, s, side="left"))
                    counts[bucket(n)] += 1

        n_short = counts.sum()
        pcts = [f"{100 * c / n_short:.1f}" for c in counts]
        print(f"{t}\t{n_short} ({100 * n_short / n_total:.0f}%)\t" + "\t".join(pcts))

    conn.close()


if __name__ == "__main__":
    main()
