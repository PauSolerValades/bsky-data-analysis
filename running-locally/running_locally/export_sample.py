"""Export a random sample of rows from StarRocks to Parquet.

Uses hash-based sampling: `murmur_hash3_32(CONCAT(did, seed)) % N < PCT`.
Deterministic, fast (no full sort), ~PCT% of rows.

Run ONCE on artemis.

Usage:
    uv run export_sample.py --pct 10
"""

import argparse
import os
from pathlib import Path

import polars as pl
import pymysql
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / ".env")

DB_CONFIG = {
    "host": os.environ["DATABASE_HOST"],
    "port": int(os.environ["DATABASE_PORT"]),
    "user": os.environ["DATABASE_USER"],
    "password": os.environ["PAU_PASSWORD"],
    "database": "bsky",
    "charset": "utf8mb4",
}

OUT_DIR = REPO / "data" / "sample"
BATCH_SIZE = 200_000


def export_table(conn, table: str, pct: int, seed: int, out_path: Path):
    """Export ~pct% of rows using murmur hash on did."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        total = cur.fetchone()[0]
    expected = int(total * pct / 100)
    print(f"── {table}: {total:,} rows → ~{expected:,} sampled ({pct}%)")

    # Filter on DB side: hash(did + seed) masked to positive, modulo 100
    query = f"""
        SELECT * FROM {table}
        WHERE (murmur_hash3_32(CONCAT(did, '{seed}')) & 2147483647) % 100 < {pct}
    """

    with conn.cursor() as cur:
        cur.execute(query)
        cols = [desc[0] for desc in cur.description]

        all_dfs = []
        fetched = 0
        while True:
            rows = cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            all_dfs.append(pl.DataFrame(rows, schema=cols, orient="row"))
            fetched += len(rows)
            print(f"    {fetched:>12,} rows", end="\r")

    print()
    if all_dfs:
        combined = pl.concat(all_dfs)
        combined.write_parquet(out_path)
        size_mb = out_path.stat().st_size // 1024 // 1024
        print(f"    → {out_path}  ({len(combined):,} rows, {size_mb} MB)")
    else:
        print("    → No rows — something went wrong.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pct", type=int, default=10, help="% of rows to sample")
    parser.add_argument("--seed", type=int, default=42, help="Seed for hash")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = pymysql.connect(**DB_CONFIG)
    print(f"Connected to {DB_CONFIG['host']}:{DB_CONFIG['port']} — {args.pct}% hash sampling\n")

    export_table(conn, "records", args.pct, args.seed, OUT_DIR / "records.parquet")
    print()
    export_table(conn, "posts", args.pct, args.seed, OUT_DIR / "posts.parquet")

    conn.close()
    print(f"\nDone. Copy data/sample/*.parquet to your local machine.")


if __name__ == "__main__":
    main()
