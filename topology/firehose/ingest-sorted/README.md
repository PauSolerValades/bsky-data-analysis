# ingest — Bluesky Firehose → SQLite Social Graph

A Go tool that walks a directory tree of Bluesky firehose JSONL exports and
populates a SQLite database with `follow` and `block` edges, using
**Slowly Changing Dimension Type 2** semantics (each edge has `valid_from` /
`valid_to`).

## Usage

```bash
# Build
go build -o ingest .

# Create fresh database and ingest
./ingest --db bsky-topology.db /data/bluesky/full_data/data_non_posts

# Recreate database from scratch
./ingest --db bsky-topology.db --delete-db /data/bluesky/full_data/data_non_posts

# Resume after interruption (skips already-parsed files)
./ingest --db bsky-topology.db /data/bluesky/full_data/data_non_posts
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--db` | `bsky-topology.db` | SQLite database path |
| `--delete-db` | `false` | Delete and recreate DB if it exists |
| `--batch-size` | `10000` | Records per transaction commit |

### Behaviour

- If the database **does not exist**, it is created with the full schema.
- If the database **exists** and `--delete-db` is **not** set, the program
  **crashes immediately** — no prompts, no silent overwrite.
- If the database **exists** and `--delete-db` **is** set, it is deleted and
  recreated.

## Project Layout

```
ingest/
├── go.mod
├── go.sum
├── README.md
├── main.go          # CLI: flags, orchestration loop
├── db.go            # InitDB — create/open database, run schema, set pragmas
├── schema.sql       # DDL embedded at compile time (CREATE TABLE, indexes)
├── discover.go      # DiscoverFiles — walk directory tree for .jsonl files
├── records.go       # FirehoseRecord, MicrosToISO, AtURI, TableForCollection
├── parsed.go        # AlreadyParsed, MarkFileParsed, FilterAlreadyParsed
└── process.go       # ProcessFile — core ingestion loop with batch commits
```

All files are `package main` — no sub-packages, no imports.

## Source File Reference

### `db.go` — Database initialisation

| Exported | Signature | Description |
|----------|-----------|-------------|
| `InitDB` | `(dbPath string, deleteDB bool) (*sql.DB, error)` | Create or recreate the SQLite DB. Runs embedded schema. Sets WAL + NORMAL synchronous pragmas. Returns an open, ready-to-use connection. |

### `discover.go` — File discovery

| Exported | Signature | Description |
|----------|-----------|-------------|
| `DiscoverFiles` | `(baseDir string) ([]string, error)` | Walk `baseDir` recursively. Return all `.jsonl` paths sorted alphabetically (≈ chronologically, since the tree is `YYYY-MM/DD/<file>`). |

### `records.go` — Data types & helpers

**Types:**

| Type | Fields | Description |
|------|--------|-------------|
| `FirehoseRecord` | `Kind`, `TimeUS`, `DID`, `Commit` | A single JSONL line from the firehose. |
| `CommitRecord` | `Collection`, `Operation`, `RKey`, `Record` | The `commit` sub-object; `Record` is raw JSON (lazy-parsed for `.subject`). |

**Constants:**

| Constant | Value |
|----------|-------|
| `CollectionFollow` | `"app.bsky.graph.follow"` |
| `CollectionBlock` | `"app.bsky.graph.block"` |

**Functions:**

| Exported | Signature | Description |
|----------|-----------|-------------|
| `MicrosToISO` | `(us int64) string` | Convert microsecond epoch → ISO 8601 UTC string. |
| `AtURI` | `(did, collection, rkey string) string` | Build `at://<did>/<collection>/<rkey>`. |
| `TableForCollection` | `(collection string) (string, error)` | Map lexicon → `"follow_edges"` / `"block_edges"`. |

### `parsed.go` — Idempotency tracking

| Exported | Signature | Description |
|----------|-----------|-------------|
| `AlreadyParsed` | `(db *sql.DB, filename string) (bool, error)` | Check if a file has status `COMPLETED`. |
| `MarkFileParsed` | `(db *sql.DB, filename string, count int, status string) error` | Insert / update a row in `parsed_files`. |
| `FilterAlreadyParsed` | `(db *sql.DB, files []string) ([]string, error)` | Remove already-COMPLETED files from the list. Returns only files that still need work. |

### `process.go` — Ingestion loop

| Exported | Signature | Description |
|----------|-----------|-------------|
| `ProcessFile` | `(db *sql.DB, path string, batchSize int) (int, error)` | Open JSONL file, scan lines, dispatch follow/block creates and deletes. Commits every `batchSize` records. Returns count of relevant records processed. |

Internally uses `dispatchRecord`, `handleCreate`, `handleDelete` (unexported).

## Database Schema

Four tables (see `schema.sql` and `bluesky_db_specification.md`):

| Table | Purpose |
|-------|---------|
| `users` | Unique DIDs with first-seen metadata |
| `follow_edges` | Follow relationships with `valid_from` / `valid_to` (SCD2) |
| `block_edges` | Block relationships with `valid_from` / `valid_to` (SCD2) |
| `parsed_files` | Ledger of processed files (idempotency) |

## Dependencies

- [`mattn/go-sqlite3`](https://github.com/mattn/go-sqlite3) — SQLite driver (CGo)
