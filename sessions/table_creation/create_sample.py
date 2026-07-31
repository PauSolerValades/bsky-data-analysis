"""Create pau_db.events_sample with N% random DIDs from pau_db.events.

Usage:
    uv run sessions/table_creation/create_sample.py --sample-pct 5
"""

import argparse
import random
import sys

from _core import WHERE, TBL_PREFIX, REPO_ROOT, get_connection, _execute

SAMPLE_SEED = 42


def main():
    parser = argparse.ArgumentParser(description="Create events sample table.")
    parser.add_argument("--sample-pct", type=float, required=True,
                        help="Percentage of DIDs to sample (e.g. 5 for 5%%)")
    args = parser.parse_args()

    conn = get_connection()
    try:
        # 1. Get all DIDs
        dids = [row[0] for row in _execute(
            conn, f"SELECT DISTINCT did FROM {TBL_PREFIX}events ORDER BY did"
        )]
        print(f"  → {len(dids):,} total DIDs", file=sys.stderr)

        # 2. Sample
        rng = random.Random(SAMPLE_SEED)
        k = max(1, int(len(dids) * args.sample_pct / 100))
        sampled = set(rng.sample(dids, k))
        print(f"  → {len(sampled):,} sampled DIDs ({args.sample_pct}%%)", file=sys.stderr)

        # 3. Create sample table
        target = f"{TBL_PREFIX}events_sample"
        _execute(conn, f"DROP TABLE IF EXISTS {target}")
        _execute(conn, f"""
            CREATE TABLE {target} LIKE {TBL_PREFIX}events
        """)
        conn.commit()
        print(f"  → Created {target}", file=sys.stderr)

        # 4. Insert sampled events in batches
        BATCH = 2000
        did_list = sorted(sampled)
        total = 0
        for i in range(0, len(did_list), BATCH):
            batch = did_list[i:i + BATCH]
            placeholders = ",".join(["%s"] * len(batch))
            rows = _execute(conn, f"""
                SELECT COUNT(*) FROM {TBL_PREFIX}events
                WHERE did IN ({placeholders})
            """, batch)
            total += rows[0][0]

            _execute(conn, f"""
                INSERT INTO {target}
                SELECT * FROM {TBL_PREFIX}events
                WHERE did IN ({placeholders})
            """, batch)
            conn.commit()

            pct = min(100, 100 * (i + BATCH) / len(did_list))
            print(f"\r  Inserting... {pct:.0f}%", end="", file=sys.stderr, flush=True)

        print(f"\n  Done — {total:,} rows in {target}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
