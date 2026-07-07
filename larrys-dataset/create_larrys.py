#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
# ]
# ///
"""
Create pau_db.larrys — filtered Bluesky reply posts.

Filters:
  1. Within the firehose time window: 2026-04-11 → 2026-04-16
  2. Is a reply (has both reply_root_uri and reply_parent_uri)
  3. Both the root post and the immediate parent post exist in bsky.posts

Output table: pau_db.larrys  (same schema as bsky.posts)

Usage:
    uv run larrys-dataset/create_larrys.py
"""

import os
import sys

import pymysql


def _env(key: str, default: str = "") -> str:
    """Read from environment or fall back to .env file."""
    val = os.environ.get(key)
    if val is not None:
        return val
    # Parse .env file (simple k=v parser)
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == key:
                        return v
    except FileNotFoundError:
        pass
    return default


DB_CONFIG = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": "bsky",
}

TABLE_NAME = "pau_db.larrys"

# AT URI format: at://did:plc:xxx/app.bsky.feed.post/rkey
# Did is the 3rd /-delimited segment (at:, <empty>, did:plc:xxx), rkey is the last.
EXTRACT_DID = "SUBSTRING_INDEX(SUBSTRING_INDEX({col}, '/', 3), '/', -1)"
EXTRACT_RKEY = "SUBSTRING_INDEX({col}, '/', -1)"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    did             VARCHAR(64),
    rkey            VARCHAR(16),
    time_us         BIGINT,
    created_at      DATETIME,
    post_text       VARCHAR(65533),
    lang            VARCHAR(16),
    reply_root_uri  VARCHAR(256),
    reply_root_cid  VARCHAR(64),
    reply_parent_uri VARCHAR(256),
    reply_parent_cid VARCHAR(64)
)
ENGINE = OLAP
DUPLICATE KEY (did, rkey)
DISTRIBUTED BY HASH(did) BUCKETS 32
PROPERTIES ("replication_num" = "1");
"""

POPULATE_SQL = f"""
INSERT INTO {TABLE_NAME}
SELECT p.*
FROM bsky.posts p
WHERE p.created_at >= '2026-04-11 00:00:00'
  AND p.created_at <  '2026-04-17 00:00:00'
  AND p.reply_root_uri IS NOT NULL
  AND p.reply_parent_uri IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM bsky.posts root
      WHERE root.did  = {EXTRACT_DID.format(col='p.reply_root_uri')}
        AND root.rkey = {EXTRACT_RKEY.format(col='p.reply_root_uri')}
  )
  AND EXISTS (
      SELECT 1
      FROM bsky.posts parent
      WHERE parent.did  = {EXTRACT_DID.format(col='p.reply_parent_uri')}
        AND parent.rkey = {EXTRACT_RKEY.format(col='p.reply_parent_uri')}
  );
"""

STATS_SQL = f"""
SELECT
    COUNT(*)                                                     AS total_rows,
    COUNT(DISTINCT did)                                          AS unique_authors,
    COUNT(DISTINCT {EXTRACT_DID.format(col='reply_root_uri')})  AS unique_roots,
    COUNT(DISTINCT reply_root_uri)                               AS unique_threads
FROM {TABLE_NAME};
"""


def main() -> None:
    conn = pymysql.connect(**DB_CONFIG)
    print(f"Connected to {DB_CONFIG['host']}:{DB_CONFIG['port']}", file=sys.stderr)

    with conn.cursor() as cur:
        # 1. Drop existing table if present
        print(f"Dropping {TABLE_NAME} if exists ...", file=sys.stderr)
        cur.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
        conn.commit()

        # 2. Create the table
        print(f"Creating {TABLE_NAME} ...", file=sys.stderr)
        cur.execute(CREATE_TABLE_SQL)
        conn.commit()

        # 3. Populate with filtered posts
        print("Inserting filtered posts (this may take a while) ...", file=sys.stderr)
        cur.execute(POPULATE_SQL)
        conn.commit()
        print(f"Inserted {cur.rowcount:,} rows.", file=sys.stderr)

        # 4. Print stats
        print("\n--- Stats ---", file=sys.stderr)
        cur.execute(STATS_SQL)
        row = cur.fetchone()
        print(f"  Total rows:       {row[0]:,}", file=sys.stderr)
        print(f"  Unique authors:   {row[1]:,}", file=sys.stderr)
        print(f"  Unique root DIDs: {row[2]:,}", file=sys.stderr)
        print(f"  Unique threads:   {row[3]:,}", file=sys.stderr)

    conn.close()
    print("\nDone.", file=sys.stderr)


if __name__ == "__main__":
    main()
