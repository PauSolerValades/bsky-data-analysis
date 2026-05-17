#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pymysql", "python-dotenv"]
# ///
"""
Export the follow graph from pau_db.followers_from_data as a single JSON file.

Output structure:
  {
    "users": ["did:plc:...", "did:plc:...", ...],
    "followers": [
      {"follower_id": "did:plc:...", "followed_id": "did:plc:..."},
      ...
    ]
  }

The file is streamed — memory usage is constant regardless of graph size.

Usage:
  uv run topology-crawl/export_graph.py                           # stdout
  uv run topology-crawl/export_graph.py -o results/graph.json     # to file
  uv run topology-crawl/export_graph.py --edges-only              # skip users array
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pymysql
from dotenv import load_dotenv

# ── Configuration ────────────────────────────────────────────────────────────

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)


def _require(key: str) -> str:
    val = os.environ.get(key)
    if val is None:
        print(f"Missing env var: {key} (check .env)", file=sys.stderr)
        sys.exit(1)
    return val


SR_CFG = {
    "host": _require("DATABASE_HOST"),
    "port": int(_require("DATABASE_PORT")),
    "user": _require("DATABASE_USER"),
    "password": _require("PAU_PASSWORD"),
    "database": "pau_db",
    "charset": "utf8mb4",
}

BATCH_SIZE = 50_000  # rows per DB fetch for edges


# ── Streaming JSON writer ────────────────────────────────────────────────────

class StreamingJSONGraph:
    """Writes a large JSON graph object without holding it in memory."""

    def __init__(self, fp):
        self.fp = fp
        self._first_user = True
        self._first_edge = True
        self._users_written = 0
        self._edges_written = 0

    def open(self):
        self.fp.write('{\n')

    def write_users_array(self, dids):
        """Write the "users" key with a list of DID strings."""
        self.fp.write('  "users": [\n')
        first = True
        for did in dids:
            if not first:
                self.fp.write(',\n')
            self.fp.write(f'    {json.dumps(did)}')
            first = False
            self._users_written += 1
            if self._users_written % 500_000 == 0:
                print(f"  … {self._users_written:,} users written",
                      file=sys.stderr, flush=True)
        self.fp.write('\n  ]')

    def start_followers_array(self):
        self.fp.write(',\n  "followers": [\n')

    def write_edges(self, edges):
        """Write edge objects. `edges` is a list of (follower_did, followee_did)."""
        for fid, fed in edges:
            if not self._first_edge:
                self.fp.write(',\n')
            obj = {"follower_id": fid, "followed_id": fed}
            self.fp.write(f'    {json.dumps(obj)}')
            self._first_edge = False
            self._edges_written += 1
            if self._edges_written % 1_000_000 == 0:
                print(f"  … {self._edges_written:,} edges written",
                      file=sys.stderr, flush=True)

    def end_followers_array(self):
        self.fp.write('\n  ]')

    def close(self):
        self.fp.write('\n}\n')


# ── Database queries ─────────────────────────────────────────────────────────

def fetch_users(conn: pymysql.Connection) -> list[str]:
    """Return all DIDs that appear in at least one edge (follower or followee)."""
    print("Fetching user list (users with ≥1 edge)…", file=sys.stderr, flush=True)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT did FROM (
                SELECT follower_did AS did FROM followers_from_data
                UNION
                SELECT followee_did AS did FROM followers_from_data
            ) t
            ORDER BY did
        """)
        return [row[0] for row in cur.fetchall()]


def fetch_edges(conn: pymysql.Connection) -> list[tuple[str, str]]:
    """Generator that yields edges in batches."""
    print("Fetching edges…", file=sys.stderr, flush=True)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT follower_did, followee_did
            FROM followers_from_data
            ORDER BY follower_did, followee_did
        """)
        while True:
            rows = cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            yield rows


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Export follow graph from pau_db.followers_from_data to JSON"
    )
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="Write to FILE (default: stdout)")
    parser.add_argument("--edges-only", action="store_true",
                        help="Skip the users array (much smaller file)")
    args = parser.parse_args()

    conn = pymysql.connect(**SR_CFG)

    # Open output
    if args.output:
        out = open(args.output, "w")
        print(f"Writing to {args.output} …", file=sys.stderr, flush=True)
    else:
        out = sys.stdout

    writer = StreamingJSONGraph(out)

    # ── Users array ──────────────────────────────────────────────────────
    if not args.edges_only:
        users = fetch_users(conn)
        print(f"  {len(users):,} users", file=sys.stderr, flush=True)

        writer.open()
        writer.write_users_array(users)
        writer.start_followers_array()
    else:
        writer.open()
        # Write users as empty array if edges-only
        out.write('  "users": [],\n  "followers": [\n')

    # ── Edges array ──────────────────────────────────────────────────────
    for batch in fetch_edges(conn):
        writer.write_edges(batch)

    writer.end_followers_array()
    writer.close()

    conn.close()

    if args.output:
        out.close()

    size_mb = Path(args.output).stat().st_size / 1e6 if args.output else 0
    print(f"\nDone: {writer._users_written:,} users, "
          f"{writer._edges_written:,} edges"
          + (f", {size_mb:.0f} MB" if size_mb else ""),
          file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
