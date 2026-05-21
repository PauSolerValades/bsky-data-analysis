# Structural Virality

Compute structural virality ν(T) (Goel et al. 2016) for Bluesky post cascades.

## Algorithm

Structural virality uses the Wiener index — average distance between all pairs
of nodes in the **repost cascade tree** — computed in O(N) via subtree moments.

- ν = 1.0 → pure broadcast (star, one-to-many)
- ν > 1.0 → viral spread (deeper chains, person-to-person)
- ν = 0   → no cascade (single node)

### Cascade reconstruction

We use the `via` field in repost records, which tells us exactly which repost
a user saw. No follow graph needed — this is the **true** propagation path.

## Usage

### 1. Dump reposts from StarRocks

```bash
cd structural-virality
mysql -h 10.18.74.14 -P 9030 -u pau -p'...' -N -B < dump_reposts.sql > results/reposts.tsv
```

This produces a ~2 GB TSV file (columns: subject_uri, repost_uri, via_uri, actor_did, time_us),
sorted by `subject_uri, time_us`.

### 2. Compute structural virality (Go)

```bash
./compute_virality results/reposts.tsv results/virality_results.csv
```

Output CSV columns: `post_uri, cascade_size, structural_virality, max_depth`.

Runs in ~2–5 minutes for 25M reposts. Memory: O(largest cascade), ~a few MB.

### 3. Generate plots (Python)

```bash
uv run plot_virality.py
```

Generates 6 plots in `results/`:
- `virality_distribution.png` — histogram + log-log distribution of ν
- `virality_vs_size.png` — cascade size vs ν scatter (hexbin)
- `virality_top50.png` — top 50 most viral posts
- `virality_ccdf.png` — complementary CDF of ν
- `virality_by_bucket.png` — ν by cascade-size bucket (box plot)
- `virality_depth_distribution.png` — max tree depth histogram

## Rebuild Go binary

```bash
cd go && go build -o ../compute_virality .
```
