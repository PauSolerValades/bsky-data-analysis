# Cascade Metrics

Produces broadcast group and root-to-leaf path datasets.

## Output

| `broadcast_groups.parquet` | `root_to_leaf_paths.parquet` |
|---|---|
| Per-parent broadcast speed/decay | Per-leaf traversal speed/depth |

## Usage

```bash
cd go && go build -o ../build_metrics .
./build_metrics [-output dir] <cascades.tsv>
```

Requires the same `cascades.tsv` produced by the `01_dump_reposts.sql` dump.
