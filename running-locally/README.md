# Running Locally

We export a random sample of users from StarRocks and rebuild all derived
tables with DuckDB locally — same pipeline, zero latency, near-identical SQL.

## How it works

```
┌─ artemis (server) ──────────────────────────────────────────┐
│  export_sample.py  ──►  data/sample/records.parquet         │
│                         data/sample/posts.parquet            │
└──────────────────────────────────────────────────────────────┘
         │ scp
         ▼
┌─ your machine (local) ──────────────────────────────────────┐
│  WHERE=local  +  duckdb  ──►  all_events, sessions, plots   │
└──────────────────────────────────────────────────────────────┘
```

### 1. Export (run once on artemis)

```bash
cd firehose-analysis/running-locally
uv run export_sample.py --pct 10
```

### 2. Copy to your local machine

```bash
scp artemis:firehose-analysis/data/sample/*.parquet data/sample/
```

### 3. Set WHERE in .env

```env
WHERE=local
```

### 4. How your code uses it

The `local_db` module provides a `pymysql`-compatible DuckDB backend.
Your `_common.py` / `_core.py` switches transparently:

```python
from running_locally.local_db import Where, get_connection as local_connect

def get_connection():
    where = Where.from_env()
    if where == Where.LOCAL:
        return local_connect(where, repo_root=str(REPO))
    return pymysql.connect(**DB_CONFIG)
```

Everything else — `cursor()`, `execute()`, `fetchall()` — works the same.
No other code changes needed.

## Sampling strategy

We sample ~10% of **rows** from each table using deterministic hash-based
sampling (`murmur_hash3_32` on `did`). Same seed → same sample every time.

At 10%:
- `records.parquet`: ~21M rows, ~200 MB
- `posts.parquet`:  ~2.8M rows, ~40 MB
