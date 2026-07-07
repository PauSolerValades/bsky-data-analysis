# Structural Virality

Compute structural virality ν(T) (Goel et al. 2016) and cascade tree metrics for
Bluesky post cascades. Produces three parquet datasets in a single pass over the data.

## Datasets

The Go binary connects directly to StarRocks and outputs three parquet files:

### `cascades.parquet` — Cascade-level metrics

One row per original post.

| Column | Type | Description |
|---|---|---|
| `post_uri` | string | AT URI of the original post |
| `author_did` | string | DID of the post creator |
| `creation_time_us` | int64 | Post creation timestamp (microseconds) |
| `cascade_size` | int32 | Total nodes (root + all reposts) |
| `cascade_depth` | int32 | Maximum tree depth (root = 0) |
| `max_out_degree` | int32 | Largest fan-out of any node in the tree |
| `structural_virality` | float64 | Wiener-index-based structural virality ν(T) |

### `broadcast_groups.parquet` — Per-parent broadcast analysis

One row per node in the tree that has children. Measures how fast a single
user's audience picks up the content.

| Column | Type | Description |
|---|---|---|
| `post_uri` | string | Which cascade this group belongs to |
| `parent_did` | string | DID of the broadcasting user |
| `broadcast_size` | int32 | Number of children (audience that reposted) |
| `mean_gap_us` | float64 | Mean time between consecutive child reposts |
| `median_gap_us` | float64 | Median time between consecutive child reposts |
| `gap_trend` | float64 | Slope of gap times (positive = decaying reach) |
| `first_child_time_us` | int64 | Timestamp of the first repost of this parent's children |
| `last_child_time_us` | int64 | Timestamp of the last repost of this parent's children |

### `root_to_leaf.parquet` — Root-to-leaf path analysis

One row per leaf node in the cascade tree. Captures end-to-end traversal of a
post through the social graph.

| Column | Type | Description |
|---|---|---|
| `post_uri` | string | Which cascade this path belongs to |
| `leaf_did` | string | DID of the user at the leaf (who never reposted it) |
| `path_depth` | int32 | Number of hops from root to leaf |
| `path_total_time_us` | float64 | t(leaf) − t(root) in microseconds |
| `traversal_speed` | float64 | path_total_time_us / path_depth |
| `gap_trend` | float64 | Slope of inter-hop gaps (positive = deceleration toward leaf) |

## Algorithm

### Structural virality

Uses the Wiener index — average distance between all pairs of nodes in the
**repost cascade tree** — computed in O(N) via a single post-order traversal
that accumulates subtree-crossing counts per edge:

```
ν(T) = 2 · Σ (sub · (n − sub)) / (n · (n − 1))
```

where the sum is over all parent→child edges and `sub` is the size of the
child's subtree.

- ν = 1.0 → pure broadcast (star, one-to-many)
- ν > 1.0 → viral spread (deeper chains, person-to-person)
- ν = 0   → no cascade (single node)

### Cascade reconstruction

We use the `via_uri` field in repost records, which tells us exactly which
repost a user saw. No follow graph needed — this is the **true** propagation path.

The tree is stored in **CSR (Compressed Sparse Row)** format for cache-friendly traversal.

### Broadcast groups

For each parent node, we collect its children (already time-sorted by
construction) and compute:
- **Broadcast speed**: mean/median gap between consecutive child reposts
- **Broadcast decay**: linear regression slope of gaps over position
  (positive = slowing down, negative = accelerating)

### Root-to-leaf paths

A recursive traversal from root collects all paths to leaves (nodes with zero
children). For each path we compute total traversal time, speed, and
inter-hop gap trend.

## Usage

### Rebuild Go binary

```bash
cd go && go build -o ../02_compute_virality .
```

### Run

```bash
./structural-virality/02_compute_virality \
  -host 10.18.74.14 \
  -port 9030 \
  -user pau \
  -password '...' \
  -database bsky \
  -output results/
```

All flags are optional and default to the values above.

### Generate plots (Python)

```bash
uv run structural-virality/03_plot_virality.py
```

Reads `results/cascades.parquet` and generates 6 plots in `results/`.
