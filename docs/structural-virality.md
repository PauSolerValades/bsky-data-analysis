# Structural Virality of Bluesky Posts

**Metric:** Structural virality ν(T) (Goel et al., 2016) — Wiener index of the repost
cascade tree. ν = 1 is pure broadcast (star); ν > 1 indicates viral
person-to-person spread.

**Data:** 25.4M repost events from 6 days of the Bluesky firehose (April 2026),
covering 15.3M top-level posts.

---

## 1. Procedure

### 1.1 Cascade tree reconstruction

Bluesky's AT Protocol records include a `via` field in repost events: when user
C reposts, the `via.uri` tells us exactly which repost (by user B) they saw.
This gives us the **true propagation path**, no inference needed.

- **Direct reposts** (`via` is null, 17.6M / 69.3%): user saw the original post.
  Anchor directly to the root (post creator).
- **Via reposts** (`via` has a value, 7.8M / 30.7%): user saw someone else's
  repost. Anchor as a child of that parent repost in the tree.

### 1.2 Structural virality formula

For a cascade tree T with n nodes, let S_i be the size of the subtree rooted at
node i. The Wiener index (average pairwise distance) is computed via subtree
moments in a single post-order traversal:

$$\nu(T) = \frac{2n}{n-1} \cdot \left( \frac{\sum S_i}{n} - \frac{\sum S_i^2}{n^2} \right)$$

- **Complexity:** O(n) per tree. Total computation is O(N) for N = total reposts.
- **Interpretation:** ν = 1 for a pure star (root + k children, all depth 1). A
  deep chain of length d gives ν = (d+1)(d+2)/3d, approaching d/3 for large d.

### 1.3 Implementation

| Step | Tool | Input | Output | Runtime |
|------|------|-------|--------|---------|
| **Dump** | SQL (StarRocks) | `bsky.records` | 5.2 GB TSV, 25.4M rows | ~2 min |
| **Compute** | Go (O(N) streaming) | TSV | 354 MB CSV, 4.4M rows | ~1 min |
| **Plot** | Python (matplotlib) | CSV | 6 PNGs | ~20 s |

The Go binary reads the TSV sorted by `(subject_uri, time_us)`, groups reposts
by original post, builds the cascade tree using a `via_uri → node` hashmap, and
computes ν via the subtree-moments algorithm. Memory: O(largest cascade) ≈ a
few MB. The results CSV contains `post_uri, cascade_size, structural_virality, max_depth`.

SQL query:
```sql
SELECT subject_uri,
       CONCAT('at://', did, '/app.bsky.feed.repost/', rkey) AS repost_uri,
       via_uri, did AS actor_did, time_us
FROM bsky.records
WHERE collection = 'app.bsky.feed.repost'
  AND operation = 'create' AND time_us > 0
ORDER BY subject_uri, time_us;
```

---

## 2. Results

### 2.1 Coverage

Of 15.3M top-level posts, **4.41M (28.9%)** received at least one repost and
thus have a cascade tree. The remaining 10.9M (71.1%) have ν = 0 (no cascade).

### 2.2 Structural virality distribution

| Statistic | Value |
|-----------|-------|
| **N** (cascades) | 4,407,830 |
| **ν mean** | 1.3505 |
| **ν median** | **1.0000** |
| ν std | 0.5736 |
| **ν min** | 1.0000 |
| **ν max** | **80.7410** |
| ν p90 | 2.0331 |
| ν p95 | 2.9765 |
| ν p99 | 3.3733 |
| ν p99.9 | 6.9299 |

**54.7% of all cascades are pure broadcast** (ν = 1.0). These are star-shaped trees
where every reposter saw the original post directly — no chain of reposts.

### 2.3 ν by cascade size

| Size bucket | N cascades | ν mean | ν median | ν p90 | ν max |
|-------------|:----------:|:------:|:--------:|:-----:|:-----:|
| 2 (root + 1 repost) | 2,412,956 | 1.000 | 1.000 | 1.000 | 1.000 |
| 3 | 644,490 | 1.310 | 1.333 | 1.333 | 1.333 |
| 4–5 | 509,949 | 1.663 | 1.500 | 2.250 | 4.500 |
| 6–10 | 400,019 | 1.874 | 1.667 | 3.067 | 7.067 |
| 11–20 | 224,071 | 2.276 | 2.067 | 4.001 | 14.357 |
| 21–50 | 124,565 | 2.987 | 2.790 | 5.812 | 28.003 |
| 51–100 | 40,707 | 4.134 | 3.698 | 8.459 | 44.606 |
| 101–500 | 38,241 | 6.395 | 4.971 | 13.984 | 73.243 |
| 501–1000 | 5,221 | 9.718 | 5.914 | 22.586 | 74.301 |
| 1001+ | 7,611 | 14.738 | 5.944 | 31.157 | 80.741 |

**ν grows sub-linearly with cascade size.** The largest cascades (1001+ nodes)
are ~15× more viral on average than the smallest (2 nodes), despite being
~1000× larger. A cascade of 10K nodes with ν ≈ 5 still looks more like a broad
fan-out than a deep chain.

### 2.4 Cascade depth

| Statistic | Value |
|-----------|-------|
| Max depth | 235 |
| Mean depth | 1.28 |
| Median depth | 1 |
| Depth = 1 (all direct) | 3.24M (73.4%) |

**73.4% of cascades have depth 1** — all reposters saw the original post. Even
among viral cascades (ν > 2), the typical depth is modest (median 2, mean 3.1);
the high ν values come from a combination of non-trivial branching and chain
structure rather than extreme depth alone.

The deepest cascade (235 generations) corresponds to the post with ν = 80.74
and 12,720 nodes — a long, branching chain propagating through 235 "hops."

### 2.5 Engagement context

| Engagement type | Posts with ≥1 | Median time-to-first |
|-----------------|:------------:|:--------------------:|
| Likes | 6.95M (45.7%) | 5.6 min |
| Replies | 2.65M (17.5%) | 5.9 min |
| **Reposts** | **4.41M (28.9%)** | **13.3 min** |

Reposts are the **slowest** engagement type to arrive (median 13.3 min vs.
5.6 min for likes), consistent with the "engagement ladder": likes → replies →
reposts. A post must clear the like threshold before amplification begins.

### 2.6 CCDF and tail behavior

The CCDF of ν decays faster than a power law: above ν ≈ 2, the probability
drops by roughly an order of magnitude per unit increase in ν. At ν = 10,
fewer than 1 in 10,000 cascades remain. The tail (< 0.1% of cascades) contains
the genuinely viral events (ν > 7).

---

## 3. Interpretation

### 3.1 Bluesky is predominantly broadcast

More than half of all repost cascades (54.7%) are pure broadcast — the post
spreads one hop from creator to audience, with no secondary propagation. For
comparison, Goel et al. (2016) found that Twitter cascades had a median ν ≈ 1.0
as well, but with a longer tail (higher fraction of viral cascades). Bluesky's
cascade distribution is **more concentrated near ν = 1**, consistent with a
smaller, less densely-connected network.

### 3.2 Viral cascades exist but are rare

The top 0.1% of cascades (ν > 7) show genuinely viral diffusion: multi-generational
chains with branching at each hop. The most viral post (ν = 80.74) propagated
through 235 generations and reached 12,720 nodes — a tree shape far from a star,
approaching a broad-but-deep diffusion pattern.

### 3.3 ν is a useful discrimination metric

Structural virality cleanly separates three regimes:

| Regime | ν range | % of cascades | Interpretation |
|--------|---------|:------------:|----------------|
| **Broadcast** | ν = 1.0 | 54.7% | One-to-many, no chain |
| **Mixed** | 1.0 < ν ≤ 3.0 | 42.7% | Some chain structure, mostly shallow |
| **Viral** | ν > 3.0 | 2.6% | Multi-generational, true diffusion |

### 3.4 Comparison to simulation models

For agent-based simulations of Bluesky (from the companion TFM project), the
empirical ν distribution provides a calibration target. A correctly calibrated
simulation should reproduce:
- ~55% of cascades at ν = 1.0 (pure broadcast)
- Median ν ≈ 1.0, mean ν ≈ 1.35
- Maximum ν in the 50–100 range
- ν growing sub-linearly with cascade size (ν ∝ log(size) rather than ν ∝ size)

---

## 4. Reproducibility

All scripts in `structural-virality/`:

```bash
# 1. Dump reposts from StarRocks (~2 min, 5.2 GB)
mysql -h 10.18.74.14 -P 9030 -u pau -p'...' -N -B \
  < structural-virality/dump_reposts.sql \
  > structural-virality/results/reposts.tsv

# 2. Compute ν with Go (~1 min)
./structural-virality/compute_virality \
  structural-virality/results/reposts.tsv \
  structural-virality/results/virality_results.csv

# 3. Generate plots (~20 s)
uv run structural-virality/plot_virality.py

# Rebuild Go binary if needed:
cd structural-virality/go && go build -o ../compute_virality .
```

### Output files

| File | Description |
|------|-------------|
| `results/virality_results.csv` | 4.4M rows: post_uri, cascade_size, ν, max_depth |
| `results/virality_distribution.png` | Histogram + log-log distribution of ν |
| `results/virality_vs_size.png` | Hexbin: cascade size vs ν, with bucket means |
| `results/virality_top50.png` | Horizontal bar chart of top 50 most viral posts |
| `results/virality_ccdf.png` | Log-log complementary CDF of ν |
| `results/virality_by_bucket.png` | Box plot of ν by cascade-size bucket |
| `results/virality_depth_distribution.png` | Histogram + log-log of max tree depth |

---

*Computed 2026-05-21 from 25,385,590 repost events across 4,407,830 cascades.*
