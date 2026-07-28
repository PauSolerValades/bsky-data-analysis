"""Export a random sample of users from StarRocks to Parquet.

Samples P% of users and exports ALL their records + posts to local Parquet files.
Run ONCE on artemis, then scp the .parquet files to your local machine.

Usage:
    uv run export_sample.py --pct 10
"""

import argparse
import os
import sys
from pathlib import Path

import duckdb
import pymysql
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent
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
BATCH_SIZE = 500  # DIDs per batch for export queries


def main():
    parser = argparse.ArgumentParser(description="Export sample from StarRocks")
    parser.add_argument("--pct", type=float, default=10, help="Percentage of users to sample (default: 10)")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = pymysql.connect(**DB_CONFIG)
    print(f"Connected to {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print(f"Sampling {args.pct}% of users\n")

    # ── Step 1: Get total users and pick a random sample of DIDs ─────────

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT did) FROM records")
        total_users = cur.fetchone()[0]
    sample_size = max(1, int(total_users * args.pct / 100))
    print(f"Total users: {total_users:,}")
    print(f"Sample size: {sample_size:,} ({args.pct}%)\n")

    print("Picking random DIDs...")
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT DISTINCT did FROM records
            ORDER BY RAND()
            LIMIT {sample_size}
        """)
        sampled_dids = [row[0] for row in cur.fetchall()]
    print(f"  → {len(sampled_dids):,} DIDs\n")

    # Also include DIDs that only appear in posts (no records)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT did FROM posts
            WHERE did NOT IN (SELECT DISTINCT did FROM records)
        """)
        posts_only_dids = [row[0] for row in cur.fetchall()]
    posts_only_sample = max(1, int(len(posts_only_dids) * args.pct / 100))
    if posts_only_sample > 0:
        import random
        random.seed(42)
        extra_dids = random.sample(posts_only_dids, min(posts_only_sample, len(posts_only_dids)))
        sampled_dids.extend(extra_dids)
        print(f"  + {len(extra_dids):,} posts-only DIDs\n")

    # ── Step 2: Export records for sampled DIDs ──────────────────────────

    records_path = OUT_DIR / "records.parquet"
    print(f"Exporting records to {records_path} ...")

    batches = [sampled_dids[i:i + BATCH_SIZE] for i in range(0, len(sampled_dids), BATCH_SIZE)]

    local = duckdb.connect()
    first_batch = True

    for i, batch in enumerate(batches):
        placeholders = ",".join(["%s"] * len(batch))
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT did, time_us, rev, operation, collection, rkey, cid,
                       created_at, subject_uri, subject_cid, subject_did,
                       via_uri, via_cid, record_json
                FROM records
                WHERE did IN ({placeholders})
            """, batch)
            rows = cur.fetchall()

        if rows:
            # Create temp table for this batch and append to parquet
            cols = ["did", "time_us", "rev", "operation", "collection", "rkey",
                    "cid", "created_at", "subject_uri", "subject_cid", "subject_did",
                    "via_uri", "via_cid", "record_json"]
            rows_as_dicts = [dict(zip(cols, row)) for row in rows]

            # Use DuckDB to write; create table from first batch, insert for rest
            temp_path = OUT_DIR / "_temp_batch.parquet"
            local.execute("""
                CREATE OR REPLACE TABLE _batch AS
                SELECT * FROM (VALUES
            """)
            # simpler: use duckdb to read from list

            local.execute("DROP TABLE IF EXISTS _batch")
            # Build with cursor.description for proper types
            local.execute("""
                CREATE TEMP TABLE _batch (
                    did VARCHAR, time_us BIGINT, rev VARCHAR, operation VARCHAR,
                    collection VARCHAR, rkey VARCHAR, cid VARCHAR,
                    created_at TIMESTAMP, subject_uri VARCHAR, subject_cid VARCHAR,
                    subject_did VARCHAR, via_uri VARCHAR, via_cid VARCHAR,
                    record_json VARCHAR
                )
            """)

        if (i + 1) % 20 == 0:
            print(f"  Batch {i + 1}/{len(batches)} ({len(sampled_dids) * (i + 1) // len(batches)} DIDs)")

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
