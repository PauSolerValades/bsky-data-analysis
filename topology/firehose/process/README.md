# process-data — StarRocks → Parquet → SCD2 DuckDB

Three-phase pipeline transforming the `bsky_topology.graph_events` event log
(1,794,550,438 rows) into a queryable SCD2 DuckDB database matching
`bluesky_db_specification.md`.

## Target Schema (↔ `bluesky_db_specification.md`)

| Table | Key | Description |
|-------|-----|-------------|
| `users` | `did` | Unique DIDs with first-seen metadata |
| `follow_edges` | `uri` | Follow relationships with `valid_from`/`valid_to` |
| `block_edges` | `uri` | Block relationships with `valid_from`/`valid_to` |

Each edge is a single row. `valid_to = NULL` means still active. Flip-flopping
(follow→unfollow→follow) creates separate rows with different URIs — correct
by construction because AT Protocol creates a new record URI for each follow.

## Directory Structure

```
process-data/
├── README.md
├── phase1_export.sh            # Phase 1: StarRocks → raw Parquet
├── phase2_transform.sql        # Phase 2: raw → SCD2 Parquet
├── phase3_materialize.sql      # Phase 3: Parquet → indexed .db
│
├── data/                       ← gitignored
│   ├── raw/                    ← Phase 1 output
│   │   └── graph_events_*.parquet     (70 files, 62 GB)
│   └── topology/               ← Phase 2 output
│       ├── follow_edges.parquet       (67 GB, 1.47B rows)
│       ├── block_edges.parquet        (5.6 GB, 117M rows)
│       └── users.parquet              (1.3 GB, 29M DIDs)
│
├── bsky_topology.db            ← Phase 3 output (130 GB, gitignored by *.db)
└── logs/                       ← gitignored
    ├── phase1.log
    ├── phase2.log
    └── phase3.log
```

## Pipeline Overview

```
 StarRocks (10.18.74.14:9030)
   │  bsky_topology.graph_events
   │  1,794,550,438 rows
   │
   ▼  Phase 1: Export (Shell script, DuckDB mysql attach, weekly chunks)
   │  ~12 minutes (4 parallel workers)
   │
 data/raw/graph_events_*.parquet    (70 files, 62 GB)
   │
   ▼  Phase 2: SCD2 Transform (DuckDB GROUP BY, no ORDER BY)
   │  ~25 minutes
   │
 data/topology/follow_edges.parquet   (67 GB, 1.47B rows)
 data/topology/block_edges.parquet    (5.6 GB, 117M rows)
 data/topology/users.parquet          (1.3 GB, 29M DIDs)
   │
   ▼  Phase 3: Materialize .db (for indexed queries)
   │  ~5 minutes
   │
 bsky_topology.db                    (130 GB, indexed)
```

The SCD2 Parquet files are queryable immediately after Phase 2 finishes —
no need to wait for Phase 3.

### Phase 2 Design Note

The `ORDER BY actor_did` clause was removed after the first attempt caused
23+ TB of temp spill during an external merge sort on 830M groups. The pure
hash aggregation (`GROUP BY uri` without ordering) completes in ~25 minutes
using ~400 GB RAM. Row ordering for query performance is handled by Phase 3
indexes instead.

## Why This Works

Each AT Protocol record has a unique URI. A given URI has at most one create
and one delete event. So building the SCD2 edge is a simple:

```sql
SELECT uri,
       MAX(actor_did)                                    AS actor_did,
       MAX(subject_did)                                  AS subject_did,
       MIN(event_timestamp)                              AS valid_from,
       NULLIF(MAX(event_timestamp), MIN(event_timestamp)) AS valid_to
FROM graph_events
WHERE action_type IN ('follow', 'unfollow')
GROUP BY uri
HAVING COUNT_IF(action_type = 'follow') > 0
```

No window functions, no state machines, no Go code. Just a GROUP BY that
DuckDB parallelizes across CPU cores.

## Files in This Directory

| File | Purpose |
|------|---------|
| `README.md` | This document |
| `EDA.md` | Exploratory data analysis — data coverage, gaps, impact |
| `check_gaps.py` | Python script: find missing days in the source tree |
| `missing_days.csv` | Every missing date (54 rows) |
| `phase1_export.sh` | Bash script: chunked export from StarRocks (weekly, parallel) |
| `phase2_transform.sql` | DuckDB script: GROUP BY into SCD2 Parquet files |
| `phase3_materialize.sql` | DuckDB script: load Parquet into `.db` with indexes |

## Prerequisites

- [DuckDB](https://duckdb.org/docs/installation/) ≥ 1.2
- GNU `date`, `xargs`, `bash` (standard Linux)
- Network access to `10.18.74.14:9030` (StarRocks FE)
- ~200 GB free local disk (raw Parquet + SCD2 Parquet + eventual `.db`)
- Enough RAM for Phase 2 GROUP BY (~33 GB hash table for 830M follow URIs;
  if less RAM is available, set `memory_limit` to force disk spilling)

## Usage

```bash
# Phase 1: export raw events from StarRocks → weekly Parquet chunks
./phase1_export.sh         # default: 4 workers (~12 min)
./phase1_export.sh 8       # 8 parallel workers

# Monitor Phase 1 progress
tail -f phase1.log

# Phase 2: transform into SCD2 Parquet files (run after Phase 1 finishes)
nohup duckdb < phase2_transform.sql > phase2.log 2>&1 &  # ~25 min

# After Phase 2: query the SCD2 Parquet files immediately
duckdb -c "
  SELECT 'follow_edges  rows' AS metric, COUNT(*) AS value FROM read_parquet('data/topology/follow_edges.parquet')
  UNION ALL SELECT 'active follows', COUNT(*) FROM read_parquet('data/topology/follow_edges.parquet') WHERE valid_to IS NULL
  UNION ALL SELECT 'users', COUNT(*) FROM read_parquet('data/topology/users.parquet');
"

# Phase 3 (optional, background): materialize into indexed .db file
duckdb bsky_topology.db < phase3_materialize.sql
```

## Graph Snapshot (2026-05-12)

As of the latest data point in the database:

| Metric | Value |
|---|---|
| Latest event | 2026-05-12 09:01:18 UTC |
| Active follow edges | 1,467,658,560 |
| Active block edges | 117,051,465 |
| Total unique DIDs | 28,860,506 |
| Users who follow someone | 21,592,211 |
| Users with at least one follower | 22,331,845 |

Average follows per active user: ~68. About 75% of discovered DIDs follow
at least one other user; the remaining 25% are lurkers or users who only
appear as targets of follows/blocks.

### Querying: Parquet vs .db

Full-scan aggregations like the snapshot above are **much faster on the
Parquet files** than the indexed `.db` file:

```sql
-- Fast: scan Parquet directly (DuckDB parallelizes reads across CPU cores)
SELECT
  (SELECT COUNT(*) FROM read_parquet('data/topology/follow_edges.parquet') WHERE valid_to IS NULL) AS active_follows,
  (SELECT COUNT(*) FROM read_parquet('data/topology/block_edges.parquet')  WHERE valid_to IS NULL) AS active_blocks,
  (SELECT COUNT(*) FROM read_parquet('data/topology/users.parquet'))                         AS total_users,
  (SELECT COUNT(DISTINCT actor_did)   FROM read_parquet('data/topology/follow_edges.parquet') WHERE valid_to IS NULL) AS users_who_follow,
  (SELECT COUNT(DISTINCT subject_did) FROM read_parquet('data/topology/follow_edges.parquet') WHERE valid_to IS NULL) AS users_followed;
```

Parquet wins for full scans because DuckDB reads column chunks in parallel,
pushes down filters to skip irrelevant data, and the columnar layout means
it only touches the columns it needs. The `.db` file uses DuckDB's native
storage format which adds MVCC overhead.

**Rule of thumb:**
- **Parquet** → full-table aggregations, COUNT(DISTINCT), time-series analysis
- **`.db` file** → indexed point lookups ("who does Alice follow?"), sub-millisecond latency

## Notes

- Data products live in `data/` (gitignored), logs in `logs/` (gitignored).
  Only scripts and docs are tracked by git.
- Phase 1 splits into ~70 weekly chunks (2025-02 → 2026-05) to stay under
  StarRocks' `query_timeout`. Each chunk is ~0.1-3 GB Parquet.
- Phase 1 supports **resume**: chunks already exported as non-empty .parquet
  files in `data/raw/` are skipped on restart.
- Phase 1 uses the DuckDB `mysql` extension (auto-installed).
- The raw `data/raw/` files can be deleted after Phase 2 succeeds
  to reclaim ~62 GB.
