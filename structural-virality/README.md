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
mysql -h 10.18.74.14 -P 9030 -u pau -p'...' -N -B < structural-virality/01_dump_reposts.sql > structural-virality/results/reposts.tsv
```

### 2. Compute structural virality (Go)

```bash
./structural-virality/02_compute_virality structural-virality/results/reposts.tsv structural-virality/results/virality_results.csv
```

### 3. Generate plots (Python)

```bash
uv run structural-virality/03_plot_virality.py
```

## Rebuild Go binary

```bash
cd structural-virality/go && go build -o ../02_compute_virality .
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
