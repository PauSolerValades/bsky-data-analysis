# ingest-parallel — Bluesky Firehose → StarRocks (parallel)

High-throughput producer-consumer pipeline ingesting Bluesky firehose JSONL
files into a StarRocks `graph_events` table. 64 producers + 40 consumers,
zero sentinels, WaitGroup-based completion tracking.

## Usage

```bash
cd ingest-parallel && go build -o ingest-parallel .

# Default (64 producers, 40 consumers, 10K batches, 20M channel)
./ingest-parallel --data /data/nfs/datasets/bluesky/firehose/non-posts

# Tune for your StarRocks backend limit
./ingest-parallel --data /data/... --consumers 50 --batch 10000
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--data` | *(required)* | Root directory (`YYYY-MM/DD/records_*.jsonl`) |
| `--dsn` | `pau:...@tcp(10.18.74.14:9030)/bsky_topology` | StarRocks DSN |
| `--producers` | `64` | File-reader goroutines |
| `--consumers` | `40` | DB-writer goroutines (match StarRocks backend limit) |
| `--batch` | `10000` | Rows per batch INSERT |
| `--chan` | `20000000` | Channel buffer size (events in flight) |

### Resuming

Safe to Ctrl+C anytime. Restart the same command — files already `COMPLETED`
in `parsed_files` are skipped. Files left in `PROCESSING` state (from a crash)
are retried automatically.

## Output: `bsky_topology.graph_events`

| Column | Type | Description |
|--------|------|-------------|
| `event_timestamp` | DATETIME | When the event happened |
| `uri` | VARCHAR(256) | AT Protocol record URI |
| `actor_did` | VARCHAR(128) | User who acted |
| `subject_did` | VARCHAR(128) | User targeted |
| `action_type` | VARCHAR(16) | `follow`, `unfollow`, `block`, `unblock` |

Sorted on disk by `(event_timestamp, uri)` via `DUPLICATE KEY`.

## Architecture

```
                         NFS (6.2 TB, 9,639 files)
                        ┌────────────────────────────┐
                        │ file1  file2  ...  file9639 │
                        └──────────────┬─────────────┘
                                       │
                              files channel (buffered)
                        ┌──────────────▼─────────────┐
                        │  fp1, fp2, fp3, ...        │
                        └──────────────┬─────────────┘
                                       │
              64 producers pull from channel
         ┌─────────────────────────────┴──────────────────────┐
         │  P1: claim → read → parse → filter → push Event    │
         │  P2: claim → read → parse → filter → push Event    │
         │  ... 64 goroutines ...                              │
         │  P64: claim → read → parse → filter → push Event   │
         └──────────────────────────┬──────────────────────────┘
                                    │
                           events channel (20M)
                    ┌───────────────┴──────────────────┐
                    │  Event, Event, Event, ...        │
                    └───────────────┬──────────────────┘
                                    │
              40 consumers pull from channel
         ┌──────────────────────────┴──────────────────────────┐
         │  C1: accumulate 10K → batch INSERT → repeat         │
         │  C2: accumulate 10K → batch INSERT → repeat         │
         │  ... 40 goroutines ...                              │
         │  C40: accumulate 10K → batch INSERT → repeat        │
         └──────────────────────────┬──────────────────────────┘
                                    │
                            ┌───────▼──────┐
                            │  StarRocks   │
                            │  graph_events│
                            └──────────────┘
```

### Completion tracking — no sentinels, no lies

1. **Producer claims** a file: `INSERT INTO parsed_files ... 'PROCESSING'`
2. **Producer reads**, filters, pushes all Events to channel. Nothing else.
3. **Consumers** pull Events, batch-INSERT into `graph_events`. Nothing else.
4. When all producers finish → `producerWg.Wait()` → channel is closed.
5. Consumers drain remaining batches → `consumerWg.Wait()`.
6. **At this point every Event is in StarRocks.** Main runs a single bulk
   `INSERT INTO parsed_files ... 'COMPLETED'` for every `PROCESSING` file.

If the process crashes: files stuck in `PROCESSING` get retried on restart.
If it finishes clean: everything is `COMPLETED`. No per-file counters, no
sentinels racing through the channel, no "expected X got Y" bugs.

### Why this design

| Decision | Why |
|----------|-----|
| **No sentinels** | 40 consumers split events from one file. A single consumer can't verify the full count. WaitGroup is the canonical Go pattern. |
| **Batch INSERT (10K rows)** | 40 consumers × individual INSERTs = 40× network round-trips per batch. Batching reduces this to 1 round-trip per 10K rows. |
| **20M channel buffer** | Producers produce faster than consumers. Large buffer lets producers read ahead from NFS without blocking. |
| **claimSem(2)** | Prevents 64 producers from all hitting `parsed_files` simultaneously, avoiding StarRocks startup storm. |
| **Separate metaDB / workerDB pools** | Metadata queries (claim, mark) never compete with heavy batch INSERTs. |

## Project layout

```
ingest-parallel/
├── go.mod
├── go.sum
├── README.md
├── main.go          # CLI: flags, channel setup, producer/consumer launch
├── db.go            # Connect() — two pools (meta + worker), CREATE TABLE
├── discover.go      # DiscoverFiles() — walk tree for *.jsonl
├── records.go       # FirehoseRecord, Event, ExtractEvent, helpers
├── producer.go      # RunProducer — claim, read NFS, filter, push to channel
├── consumer.go      # RunConsumer — pull from channel, batch INSERT
├── parsed.go        # ClaimFile, MarkFileFailed, LoadAlreadyParsed
```

All files are `package main` — no sub-packages, no imports.

## Database tables

| Table | Engine | Purpose |
|-------|--------|---------|
| `graph_events` | OLAP, `DUPLICATE KEY(event_timestamp, uri)` | All graph events |
| `parsed_files` | OLAP, `DUPLICATE KEY(filename)` | Progress tracking |

## Results (2026-05-21/22)

Final run: 64 producers, 40 consumers, 10K-row batches, 20M channel buffer.

| Metric | Value |
|--------|-------|
| Files processed | **9,639 / 9,639** |
| Follow events | 1,666,178,984 |
| Block events | 128,371,454 |
| **Total rows** | **1,794,550,438** |
| Elapsed time | ~12 hours |
| Avg throughput | 42,000 rows/sec |
| Peak throughput | 61,000 rows/sec |
| Files failed | **0** |
| Architecture | Producer-consumer, WaitGroup-based completion |

### Lessons learned

1. **No sentinels in the channel.** Multiple consumers split events from one
   file, making per-file counting impossible without shared state. Go's
   `sync.WaitGroup` is the correct primitive — when the pipeline drains, every
   event is safe.

2. **StarRocks backend concurrency.** The backend limit (initially 6, later
   raised to 50 by admin) is the primary bottleneck. Match `--consumers` to
   your limit minus 1-2 for metadata queries.

3. **Batch size matters.** 10K-row batches cut round-trips 2× vs 5K,
   more than doubling throughput (31K → 61K rows/sec).

4. **Separate connection pools.** Metadata operations (claims, marks) must
   never compete with heavy batch INSERTs. Two `*sql.DB` instances, one for
   each.

## Dependencies

- [`go-sql-driver/mysql`](https://github.com/go-sql-driver/mysql) — MySQL driver (StarRocks protocol)
- Standard library otherwise
