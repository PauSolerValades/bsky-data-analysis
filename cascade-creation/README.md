# Cascade Creation

Builds the full cascade datasets from StarRocks. Produces 5 parquet files in a
single streaming pass.

## Pipeline

```
pau_db.actions →  mysql dump  →  TSV  →  build_cascades  →  5 parquet files
 (~2 min)                    (~8 min)
```

`pau_db.actions` is the firehose "action trace" (built by
`eda-raw-dataset/create_actions_table.py` from `bsky.posts` + `bsky.records`):
per-event `did, time_us, event_type, rkey, subject_uri, via_uri`, on the same
user-side `created_at` clock as sessions.

## Output

All files written to `../cascade-metrics/results/`:

| File | Rows | Description |
|---|---|---|
| `cascades.parquet` | 29M | Cascade-level: size, depth, ν, max_out_degree |
| `post_lifetime.parquet` | 3.5M | T_50, T_95, T_99, time_to_peak |
| `broadcast_groups.parquet` | 8.3M | Per-parent broadcast speed/decay |
| `root_to_leaf_paths.parquet` | 21M | Per-path traversal speed |
| `repost_gaps.parquet` | 25M | Per-repost global/topology gaps |

## Usage

```bash
# 1. Dump from StarRocks
mysql -h 10.18.74.14 -P 9030 -u pau -p -N -B < 01_dump_reposts.sql > /tmp/cascades.tsv

# 2. Build cascades
cd go && go build -o ../build_cascades .
./build_cascades /tmp/cascades.tsv

# Output in ../cascade-metrics/results/
```

## Architecture

CSR (Compressed Sparse Row) tree representation. Single O(N) post-order pass
computes structural virality (Wiener index / subtree-crossing formula), broadcast
groups, root-to-leaf paths, lifetime percentiles, and per-repost gaps.
