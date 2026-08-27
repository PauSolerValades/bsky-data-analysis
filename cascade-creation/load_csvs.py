"""Load the Go pipeline CSVs into StarRocks via chunked INSERT.

LOAD DATA was removed in StarRocks 4.x (this server is 4.1.0) and Stream
Load needs direct BE access (blocked here), so we use the sessions pattern:
10K-row INSERT statements through the FE.

Usage:
    uv run --project sessions cascade-creation/load_csvs.py [csv_dir] [table ...]

Loads from csv_dir (default "."):
    cascades_rows.csv        → pau_db.cascades      (7 cols)
    cascade_edges_rows.csv   → pau_db.cascade_edges (4 cols)
    repost_gaps_rows.csv     → pau_db.repost_gaps   (6 cols, \\N → NULL)

Each table is TRUNCATED before loading (idempotent). Pass table names to
load a subset (csv filename without _rows.csv), e.g. `load_csvs.py dir gaps`.
For parallel shards: `split -n l/4 file` and run one process per shard.
"""

import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from running_locally.local_db import Where, get_connection as local_connect

CHUNK = 5_000  # ponytail: 10K OOMs this FE; 5K is the safe ceiling

TABLES = {
    "cascades_rows.csv": (
        "pau_db.cascades",
        ["post_uri", "author_did", "creation_time_us", "cascade_size",
         "cascade_depth", "max_out_degree", "structural_virality"],
    ),
    "cascade_edges_rows.csv": (
        "pau_db.cascade_edges",
        ["post_uri", "actor_did", "time_us", "parent_did"],
    ),
    "repost_gaps_rows.csv": (
        "pau_db.repost_gaps",
        ["post_uri", "reposter_did", "repost_time_us", "parent_did",
         "global_gap_us", "topology_gap_us"],
    ),
    "broadcast_groups_rows.csv": (
        "pau_db.broadcast_groups",
        ["post_uri", "parent_did", "broadcast_size", "mean_gap_us",
         "median_gap_us", "gap_trend", "first_child_time_us", "last_child_time_us"],
    ),
    "root_to_leaf_paths_rows.csv": (
        "pau_db.root_to_leaf_paths",
        ["post_uri", "leaf_did", "path_depth", "path_total_time_us",
         "traversal_speed", "gap_trend"],
    ),
}


def rows_from_csv(path: Path):
    with open(path, newline="") as f:
        for rec in csv.reader(f):
            yield [None if v == r"\N" else v for v in rec]


def load_table(conn, csv_path: Path, table: str, cols: list):
    n = 0
    batch = []
    for row in rows_from_csv(csv_path):
        batch.append(row)
        if len(batch) >= CHUNK:
            n += _insert(conn, table, cols, batch)
            batch = []
    if batch:
        n += _insert(conn, table, cols, batch)
    print(f"  {Path(csv_path).name}: {n:,} rows → {table}")


def _insert(conn, table, cols, batch):
    placeholders = ",".join(["(" + ",".join(["%s"] * len(cols)) + ")"] * len(batch))
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES {placeholders}"
    flat = [v for row in batch for v in row]
    for attempt in range(6):
        try:
            with conn.cursor() as cur:
                cur.execute(sql, flat)
            conn.commit()
            return len(batch)
        except Exception as e:
            # FE Java heap OOM — let it GC, then retry the same batch
            if "OutOfMemory" in str(e) and attempt < 5:
                print(f"  OOM, sleeping 60s (attempt {attempt + 1}/5)...", flush=True)
                import time
                time.sleep(60)
                continue
            raise
    return len(batch)  # unreachable


def main():
    args = sys.argv[1:]
    csv_dir = Path(args[0]) if args else Path(".")
    wanted = set(args[1:]) or None  # None = all tables
    conn = local_connect(Where.SERVER, repo_root=str(REPO))
    for name, (table, cols) in TABLES.items():
        if wanted and name.replace("_rows.csv", "") not in wanted:
            continue
        path = csv_dir / name
        if not path.exists():
            print(f"  skip {name} (not found)")
            continue
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {table}")
        conn.commit()
        load_table(conn, path, table, cols)
    conn.close()


if __name__ == "__main__":
    main()
