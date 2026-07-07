# Structural Virality & Cascade Analysis

Compute structural virality ν(T) (Goel et al. 2016), cascade tree metrics, post
lifetime percentiles, and per-repost gaps — all in a single pass over the repost
data. Produces five parquet datasets.

## Datasets

### `cascades.parquet` — Cascade-level metrics (1 row per post)

| Column | Description |
|---|---|
| `post_uri` | AT URI of the original post |
| `author_did` | DID of the post creator (extracted from subject_uri) |
| `creation_time_us` | 0 (not available — `bsky.records` has no `app.bsky.feed.post` rows) |
| `cascade_size` | Total nodes (root + all reposts) |
| `cascade_depth` | Maximum tree depth (root = 0) |
| `max_out_degree` | Largest fan-out of any node |
| `structural_virality` | Wiener-index-based ν(T) |

### `post_lifetime.parquet` — Percentile timings (1 row per post with ≥1 repost)

All times are **deltas from the first repost** (not from post creation).

| Column | Description |
|---|---|
| `T_50_us` | Time from first repost to 50% of all reposts |
| `T_95_us` | Time from first repost to 95% of all reposts |
| `T_99_us` | Time from first repost to 99% of all reposts |
| `time_to_peak_us` | Time from first repost to the densest 1% activity bin |

### `broadcast_groups.parquet` — Per-parent analysis (1 row per node with children)

| Column | Description |
|---|---|
| `parent_did` | DID of the broadcasting user |
| `broadcast_size` | Number of children |
| `mean_gap_us` / `median_gap_us` | Time between consecutive child reposts |
| `gap_trend` | Slope of gaps (positive = decaying reach) |

### `root_to_leaf_paths.parquet` — Path analysis (1 row per leaf)

| Column | Description |
|---|---|
| `leaf_did` | DID at the end of the path |
| `path_depth` | Number of hops from root |
| `path_total_time_us` | t(leaf) − t(first repost) |
| `traversal_speed` | path_total_time / path_depth |

### `repost_gaps.parquet` — Per-repost gaps (1 row per repost)

| Column | Description |
|---|---|
| `reposter_did` | Who reposted |
| `parent_did` | Who they saw it from (via_uri resolution) |
| `global_gap_us` | Time since previous repost in this cascade (−1 for first) |
| `topology_gap_us` | Time since previous repost from same parent (−1 for first) |

## Algorithm

Cascade trees are built in **CSR (Compressed Sparse Row)** format from the `via_uri`
field — the true propagation path. Structural virality uses the subtree-crossing
Wiener index formula, computed in a single O(N) post-order traversal:

```
ν(T) = 2 · Σ (sub · (n − sub)) / (n · (n − 1))
```

- ν = 1.0 → pure broadcast (star)
- ν > 1.0 → viral spread
- ν = 0   → single node

Lifetime percentiles (T_50, etc.) are computed from the already time-sorted
repost array. Broadcast groups and root-to-leaf paths are collected during the
same tree traversal.

## Usage

### 1. Dump reposts from StarRocks

```bash
mysql -h 10.18.74.14 -P 9030 -u pau -p -N -B < structural-virality/01_dump_reposts.sql > reposts.tsv
```

### 2. Compute everything

```bash
cd go && go build -o ../02_compute_virality .
./structural-virality/02_compute_virality reposts.tsv
```

Output: `results/{cascades,broadcast_groups,root_to_leaf_paths,post_lifetime,repost_gaps}.parquet`

### 3. Query examples (DuckDB)

```sql
-- Top 10 most viral posts
SELECT post_uri, cascade_size, cascade_depth, structural_virality
FROM read_parquet('results/cascades.parquet')
WHERE cascade_size >= 2
ORDER BY structural_virality DESC LIMIT 10;

-- Median T_50 by cascade size bucket
SELECT
    CASE
        WHEN cascade_size BETWEEN 2 AND 5 THEN '2-5'
        WHEN cascade_size BETWEEN 6 AND 20 THEN '6-20'
        WHEN cascade_size BETWEEN 21 AND 100 THEN '21-100'
        ELSE '100+'
    END AS bucket,
    MEDIAN(T_50_us) / 1e6 AS median_T50_seconds
FROM read_parquet('results/post_lifetime.parquet')
JOIN read_parquet('results/cascades.parquet') USING (post_uri)
WHERE total_reposts >= 2
GROUP BY bucket ORDER BY MIN(cascade_size);
```
