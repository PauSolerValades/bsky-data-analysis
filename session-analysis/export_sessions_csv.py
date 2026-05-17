#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
# ]
# ///
"""
Export session data from StarRocks to CSV for R analysis.

Exports both sessions_threshold_total (fixed 265s) and sessions_tukey (adaptive)
into data/ directory with consistent columns for comparison.

Usage:
    uv run session-analysis/export_sessions_csv.py
    uv run session-analysis/export_sessions_csv.py --sample 100000
"""

import argparse
import os
import sys
import time as time_mod
from pathlib import Path

import pymysql


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

# We use an SSCursor for unbuffered streaming to avoid memory issues on
# multi-million row result sets.
try:
    import pymysql.cursors
    _SSCursor = pymysql.cursors.SSCursor
except Exception:
    _SSCursor = None

FETCH_BATCH = 10_000

# Columns to export:
#   did, session_start, session_end, next_session_start,
#   duration_s, reposts, posts_authored, table_name
# (same schema for both tables; tukey has extra columns we skip for now)
EXPORT_SQL = """
SELECT
    did,
    session_start,
    session_end,
    next_session_start,
    duration_s,
    reposts,
    posts_authored,
    '{table}' AS source_table
FROM pau_db.{table}
ORDER BY did, session_start
"""


def export_table(conn: pymysql.Connection, table: str, output_path: Path):
    sql = EXPORT_SQL.format(table=table)
    print(f"  Querying pau_db.{table} ...", file=sys.stderr)

    # Use a separate unbuffered cursor for the export to stream results
    cur = conn.cursor(pymysql.cursors.SSCursor) if _SSCursor else conn.cursor()
    cur.execute(sql)

    total_rows = 0
    t0 = time_mod.time()

    with open(output_path, "w") as f:
        # Write header
        f.write("did,session_start,session_end,next_session_start,"
                "duration_s,reposts,posts_authored,source_table\n")

        while True:
            rows = cur.fetchmany(FETCH_BATCH)
            if not rows:
                break

            for row in rows:
                did, ss, se, nss, dur, rep, pa, src = row
                nss_str = str(nss) if nss is not None else ""
                f.write(f"{did},{ss},{se},{nss_str},{dur},{rep},{pa},{src}\n")

            total_rows += len(rows)
            if total_rows % 1_000_000 == 0:
                elapsed = time_mod.time() - t0
                print(f"    {total_rows:,} rows ... ({elapsed:.0f}s)", file=sys.stderr)

    elapsed = time_mod.time() - t0
    file_size_mb = output_path.stat().st_size / 1e6
    print(f"    → {total_rows:,} rows, {file_size_mb:.0f} MB in {elapsed:.0f}s", file=sys.stderr)


def export_sample(conn: pymysql.Connection, table: str, n: int, output_path: Path):
    """Export a random sample of N users."""
    print(f"  Sampling {n:,} users from pau_db.{table} ...", file=sys.stderr)
    t0 = time_mod.time()

    with conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT did FROM pau_db.{table} ORDER BY RAND() LIMIT %s", (n,))
        dids = [row[0] for row in cur]

    print(f"    → {len(dids):,} DIDs in {time_mod.time() - t0:.0f}s", file=sys.stderr)

    # Fetch events for sampled DIDs in batches
    total_rows = 0
    t1 = time_mod.time()

    with open(output_path, "w") as f:
        f.write("did,session_start,session_end,next_session_start,"
                "duration_s,reposts,posts_authored,source_table\n")

        for i in range(0, len(dids), 1000):
            batch = dids[i:i + 1000]
            placeholders = ",".join(["%s"] * len(batch))
            sql = f"""
                SELECT did, session_start, session_end, next_session_start,
                       duration_s, reposts, posts_authored, '{table}' AS source_table
                FROM pau_db.{table}
                WHERE did IN ({placeholders})
                ORDER BY did, session_start
            """
            with conn.cursor() as cur:
                cur.execute(sql, batch)
                for row in cur:
                    did, ss, se, nss, dur, rep, pa, src = row
                    nss_str = str(nss) if nss is not None else ""
                    f.write(f"{did},{ss},{se},{nss_str},{dur},{rep},{pa},{src}\n")
                    total_rows += 1

            if (i + 1000) % 50_000 == 0:
                elapsed = time_mod.time() - t1
                print(f"    {i+1000:,}/{len(dids):,} DIDs, {total_rows:,} rows ({elapsed:.0f}s)", file=sys.stderr)

    elapsed = time_mod.time() - t1
    file_size_mb = output_path.stat().st_size / 1e6
    print(f"    → {total_rows:,} rows, {file_size_mb:.0f} MB in {elapsed:.0f}s", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Export session data from StarRocks to CSV"
    )
    parser.add_argument(
        "--tables", type=str, default="sessions_threshold_total,sessions_tukey",
        help="Comma-separated table names (default: sessions_threshold_total,sessions_tukey)",
    )
    parser.add_argument(
        "--sample", type=int, default=0,
        help="Export a random sample of N users instead of all (0 = all, default)",
    )
    parser.add_argument(
        "--output-dir", type=str, default="data",
        help="Output directory (default: data/)",
    )
    args = parser.parse_args()

    table_names = [t.strip() for t in args.tables.split(",")]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']} ...", file=sys.stderr)
    conn = pymysql.connect(**DB_CONFIG)

    for tbl in table_names:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  Exporting pau_db.{tbl}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

        suffix = f"_sample{args.sample}" if args.sample > 0 else ""
        out_path = output_dir / f"{tbl}{suffix}.csv"

        if args.sample > 0:
            export_sample(conn, tbl, args.sample, out_path)
        else:
            export_table(conn, tbl, out_path)

    conn.close()
    print(f"\nDone. Files in {output_dir.resolve()}/", file=sys.stderr)


if __name__ == "__main__":
    main()
