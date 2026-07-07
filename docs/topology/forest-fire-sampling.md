# Forest Fire Graph Sampling — Bluesky Social Graph

**Date:** 2026-05-23  
**Method:** Forest Fire (Leskovec & Faloutsos, KDD 2006)  
**Implementation:** Go (streaming Parquet output)  
**Input:** 1.47B active follow edges, 27.5M nodes  
**Output:** 7 nested samples (10K → 1M nodes), ~20 min total

---

## Table of contents

1. [Motivation](#1-motivation)
2. [Output file schema](#2-output-file-schema)
3. [Algorithm: Forest Fire](#3-algorithm-forest-fire)
4. [Implementation strategy](#4-implementation-strategy)
5. [Data pipeline](#5-data-pipeline)
6. [Results](#6-results)
7. [Burned vs induced edges](#7-burned-vs-induced-edges)
8. [Reproducibility](#8-reproducibility)

---

## 1. Motivation

Agent-based simulations of Bluesky need a **representative subset** of the
social graph — one that preserves the power-law degree distribution,
clustering structure, and connectivity patterns of the full 27.5M-node,
1.47B-edge follow network.

Simple uniform random node sampling fails: it misses hubs and destroys
community structure. The Forest Fire method from Leskovec & Faloutsos
(KDD 2006) propagates through edges like a wildfire, naturally capturing
both high-degree hubs and the local neighborhoods around them. The paper
validated FF against in-degree, out-degree, clustering coefficient, WCC
sizes, hop-plots, and spectral properties — exactly the properties we
care about for simulation realism.

We produce **7 nested samples** (each is a superset of the previous) at
10K, 50K, 100K, 250K, 500K, 750K, and 1M nodes. Smaller ones are subsets
of larger ones — a single fire burns through the graph and we snapshot
at each boundary.

---

## 2. Output file schema

Every sample directory (`results/<size>/`) contains three Parquet files:

### `nodes.parquet`

| Column | Type | Description |
|--------|------|-------------|
| `did` | VARCHAR | Bluesky decentralized identifier (e.g., `did:plc:...`) |
| `int_id` | INTEGER (int32) | Dense integer ID (0..N-1) for graph indexing |

Integer IDs are 0-based and dense — all nodes in the sample are numbered
0..N-1. Use `int_id` for building adjacency structures; use `did` when
cross-referencing with other Bluesky data (user tables, post tables, etc.).

### `induced_edges.parquet`

| Column | Type | Description |
|--------|------|-------------|
| `actor_did` | VARCHAR | DID of the follower |
| `subject_did` | VARCHAR | DID of the followee |
| `actor_id` | INTEGER (int32) | Integer ID of the follower |
| `subject_id` | INTEGER (int32) | Integer ID of the followee |

All edges where **both endpoints** are in the visited set. This is the
dense, structurally faithful subgraph. A directed edge actor→subject means
"actor follows subject."

### `burned_edges.parquet`

Same schema as `induced_edges.parquet`. Contains only the edges the fire
actually traversed during sampling — the discovery path (see §6 for the
distinction).

---

## 3. Algorithm: Forest Fire

From Leskovec & Faloutsos, *"Sampling from Large Graphs"*, KDD 2006, §4.3.

### How it works

1. **Pick a seed node** uniformly at random from the full graph.
2. **Forward burn**: from the current node, select a random subset of its
   outgoing edges (who it follows). The number of edges to "burn" is drawn
   from a **geometric distribution** with mean `p_f / (1 - p_f)`, where
   `p_f` is the *forward burning probability*.
3. Each node reached via a burned edge becomes a new "burning" node.
   Repeat recursively (breadth-first).
4. **Backward burn** (optional): also select incoming edges (followers),
   controlled by `p_b`. This captures bidirectional social structure.
5. Nodes are visited **at most once** (no cycles). If the fire dies out
   before reaching the target size, restart with a new random seed.
6. **Induced subgraph**: the sample is the graph induced by all visited
   nodes — every edge in the original graph where *both endpoints* were
   visited is included.

### Geometric distribution

```
n_burn = ⌈ ln(1 - U) / ln(1 - p) ⌉   capped at |available neighbors|
```

Where U ~ Uniform(0, 1). At p_f = 0.5, the expected burn is 1 edge per
node (mean = 0.5/0.5 = 1). This creates a slow, steady spread that
captures realistic local structure without exploding.

### Parameters used

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `p_f` (forward) | 0.5 | Moderate spread — avoids oversampling dense hub regions |
| `p_b` (backward) | 0.2 | Light bidirectional exploration for reciprocity patterns |
| `seed` | 42 | Reproducibility |

---

## 4. Implementation strategy

### Why Go instead of Python

The initial Python implementation (in `../sampling/forest_fire.py`) had two
fatal problems at scale:

1. **Memory**: Python's per-node set lookups (`w in visited`) and list-of-
   numpy-arrays CSR representation had ~10× the memory overhead of Go.
2. **Induced subgraph computation**: collecting 650M edges into a single
   Python list before writing Parquet required ~15 GB just for the Edge
   objects, on top of the ~40 GB CSR. The server ran out of memory.

Go solves both:
- **CSR**: `[][]int32` slices with zero per-element overhead. The full
  adjacency (1.47B edges × 4 bytes × 2 directions) = ~12 GB.
- **`visited` set**: `[]bool` bit array, 27.5M bits = ~3.4 MB.
- **Streaming Parquet**: induced edges are computed by 32 parallel
  goroutines, sent through a buffered channel, and written in row-group
  batches of 5M edges each — never all in memory at once.
- **Total memory**: ~40 GB peak (CSR + DID strings + working buffers).
  Fits comfortably in the server's 1.1 TB RAM.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 0: Export (Bash/DuckDB)             │
│  Parquet → dids.txt (sorted DIDs) + edges.bin (int64 pairs) │
│  27.5M DIDs (867 MB) + 1.47B edges (22 GB binary)          │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                    Phase 1: Load & CSR (Go)                  │
│  edges.bin → []Edge → CSR{OutAdj, InAdj}                    │
│  ~12 GB CSR, ~3 MB visited[]                                │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                 Phase 2: Forest Fire (Go)                    │
│  Single pass: burn → snapshot at each target size           │
│  Snapshot = nodes + burned edges + induced edges            │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│               Phase 3: Streaming Parquet (Go)                │
│  32 parallel workers → channel → row-group writer           │
│  ~5M edges per row group, Zstd compression                  │
└─────────────────────────────────────────────────────────────┘
```

### Key Go design decisions

- **`int32` for node IDs** (not `int64`): 27.5M fits in int32, halving
  the CSR memory footprint.
- **`[]int32` slices for adjacency** (not `[][]int32` + offsets): Go
  slices are headers (24 bytes) pointing to backing arrays. Simpler than
  traditional CSR with offset arrays, and the overhead per node is just
  the slice header (24 bytes × 27.5M = 660 MB) — acceptable.
- **`rand/v2` with PCG**: Go 1.23's new PCG random source is faster than
  the old `math/rand` and doesn't require mutex locking.
- **Channel-based streaming**: 32 goroutines find induced edges, send
  batches through a buffered channel (`cap=32`), one consumer writes
  Parquet row groups. The channel buffers only ~32 batches = ~160K edges
  = ~2.5 MB of in-flight data.
- **`parquet-go` with Zstd**: row-group-level writing, compression.

---

## 5. Data pipeline

### Phase 0: Export (one-time, ~25 minutes)

The DuckDB query produces two files:

```bash
# Build sorted DID list
duckdb -c "
COPY (
    SELECT did FROM (
        SELECT DISTINCT CAST(actor_did AS VARCHAR) AS did
        FROM read_parquet('follow_edges.parquet') WHERE valid_to IS NULL
        UNION
        SELECT DISTINCT CAST(subject_did) FROM ...
    ) ORDER BY did
) TO 'dids.txt' (FORMAT CSV);
"

# Join to get integer IDs
duckdb temp.db -c "
CREATE TABLE dids AS
SELECT did, row_number() OVER () - 1 AS id
FROM read_csv('dids.txt');

COPY (
    SELECT da.id, ds.id
    FROM read_parquet('follow_edges.parquet') e
    JOIN dids da ON CAST(e.actor_did) = da.did
    JOIN dids ds ON CAST(e.subject_did) = ds.did
    WHERE e.valid_to IS NULL
) TO 'edges_raw.csv' (FORMAT CSV, DELIMITER '|');
"

# Convert to binary (Python helper)
python3 -c "
import struct
for line in open('edges_raw.csv'):
    a, s = line.strip().split('|')
    f_out.write(struct.pack('<qq', int(a), int(s)))
"
```

Output files in `topology/sampling-go/data/`:
- `dids.txt` — 27.5M DIDs, one per line (line number = integer ID)
- `edges.bin` — 1.47B int64 pairs (22 GB)
- `meta.json` — `{num_nodes: 27524035, num_edges: 1467658411}`

### Phase 1-3: Forest Fire (repeatable, ~20 minutes)

```bash
cd topology/sampling-go
go build -o forest_fire .
./forest_fire --p-f 0.5 --p-b 0.2 --seed 42
```

Optional flags: `--data <dir>`, `--out <dir>`.

---

## 6. Results

### Size summary

| Target | Actual nodes | Burned edges | Induced edges | Density | Disk |
|--------|:-----------:|:------------:|:-------------:|:-------:|-----:|
| 10K | 10,006 | 10,005 | 2,969,172 | 297 | 36 MB |
| 50K | 50,004 | 50,003 | 42,893,816 | 858 | 724 MB |
| 100K | 100,001 | 100,000 | 120,695,594 | 1,207 | 2.2 GB |
| 250K | 250,000 | 249,999 | 312,282,820 | 1,249 | 5.8 GB |
| 500K | 500,008 | 500,007 | 501,928,776 | 1,004 | 9.3 GB |
| 750K | 750,000 | 749,999 | 581,542,360 | 775 | 11 GB |
| 1M | 1,000,002 | 1,000,001 | 653,542,254 | 654 | 13 GB |

**Full graph:** 27,524,035 nodes, 1,467,658,560 edges, density ~53

### Degree distribution (induced edges)

| Sample | In-median | In-max | In-mean | Out-median | Out-max | Out-mean |
|--------|:---------:|:------:|:-------:|:----------:|:-------:|:--------:|
| 10K | 87 | 17,056 | 309 | 44 | 32,353 | 312 |
| 50K | 194 | 24,823 | 879 | 109 | 55,957 | 889 |
| 100K | 204 | 42,568 | 1,236 | 130 | 86,865 | 1,247 |
| 250K | 202 | 105,635 | 1,274 | 151 | 163,086 | 1,283 |
| 500K | 191 | 211,726 | 1,024 | 163 | 236,236 | 1,029 |
| 750K | 132 | 316,057 | 794 | 122 | 264,191 | 797 |
| 1M | 111 | 407,981 | 670 | 108 | 285,632 | 673 |

**Full graph (from EDA):** out-degree median ~68, max in the millions.

The samples are denser than the full graph because Forest Fire naturally
biases toward connected regions — it follows edges to discover nodes, so
the visited set contains proportionally more well-connected users.
Density peaks at 250K–500K and decreases as larger samples incorporate
more sparse periphery nodes.

### Performance

| Phase | Time | Memory |
|-------|-----:|-------:|
| Export (DuckDB) | ~25 min (one-time) | ~50 GB |
| Load edges.bin | ~15 s | 22 GB |
| Build CSR | ~106 s | 12 GB |
| FF burn to 1M | ~8 min | ~40 GB total |
| Induced edges (1M) | ~5 min | streaming, ~2 GB |
| **Total runtime** | **~20 min** | **~40 GB peak** |

---

## 7. Burned vs induced edges

This distinction is critical for understanding the output:

### Burned edges

Edges the fire **actually traversed** to discover new nodes. If the fire
was at Alice and chose to follow her edge to Bob, then Alice→Bob is a
burned edge. These are the sampling *path*.

**Properties:**
- Exactly ~1 edge per visited node (because p_f=0.5 gives mean 1 burn
  per forward step, plus occasional backward burns)
- Forms a collection of trees (one per fire ignition)
- Useful for: analyzing how the sampling algorithm explores the graph,
  studying discovery patterns

### Induced edges

**All** edges between visited nodes in the original graph, whether the
fire traversed them or not. If both Alice and Carol are in the visited
set, and in the real graph Alice follows Carol, that edge is included —
even if the fire reached them through completely different paths.

**Properties:**
- Dense (297–1,249 edges/node vs 53 in full graph)
- Preserves the full local structure of the sampled region
- Useful for: simulation input, where you need a structurally accurate
  social graph

### Which to use for simulation?

Use **induced edges** if you need a realistic subgraph where all existing
follow relationships between sampled users are present. This is the
standard approach from the KDD 2006 paper.

If the density is too high for your simulation model, two alternatives:
1. **Subsample induced edges**: keep edges with probability proportional
   to 1/degree to approximate the full graph's density.
2. **Use burned edges only**: sparser but captures the algorithmic
   exploration pattern rather than the static graph structure.

---

## 8. Reproducibility

### Prerequisites

- DuckDB ≥ 1.2
- Go ≥ 1.23
- Python ≥ 3.12 (for export helper)
- The `follow_edges.parquet` file from the topology pipeline
- ~60 GB free disk space (22 GB binary + results)

### Full pipeline

```bash
# 1. Export (one-time, ~25 min)
cd topology/sampling-go
bash export.sh

# 2. Build Go binary
go build -o forest_fire .

# 3. Run Forest Fire (~20 min)
./forest_fire --p-f 0.5 --p-b 0.2 --seed 42

# 4. Check results
for d in results/*/; do
    cat "$d/meta.json" | python3 -m json.tool
done
```

### Output structure

```
topology/sampling-go/results/
├── 10000/
│   ├── nodes.parquet          (did, int_id)
│   ├── burned_edges.parquet   (actor_did, subject_did, actor_id, subject_id)
│   ├── induced_edges.parquet
│   └── meta.json
├── 50000/
│   └── ...
├── 100000/
├── 250000/
├── 500000/
├── 750000/
└── 1000000/
```

### Changing parameters

```bash
# Different burning probabilities
./forest_fire --p-f 0.3 --p-b 0.1 --seed 123 --out results_lowburn

# Different random seed (independent sample)
./forest_fire --seed 999 --out results_seed999
```

---

## References

- Leskovec, J. & Faloutsos, C. (2006). *Sampling from Large Graphs*. KDD 2006.
  Defines the Forest Fire algorithm, validates against 9 graph properties.
- Leskovec, J., Kleinberg, J. & Faloutsos, C. (2007). *Graphs over Time:
  Densification Laws, Shrinking Diameters and Possible Explanations*. ACM TKDD.
  Provides the theoretical foundation for why Forest Fire produces realistic
  graph samples.
