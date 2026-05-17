# Post Lifetime Analysis

**How long does a top-level post stay alive?** Measured as the time from
creation to the first and last engagement of each type (repost, like, reply).
All aggregate metrics are precomputed in `pau_db.post_lifetime`. Individual
event timelines are stored in `pau_db.post_engagement_events`.

## Design decisions

| Decision | Why |
|----------|-----|
| **Top-level posts only** (`reply_root_uri IS NULL`) | Replies are thread participants, not original content. ~15.3M posts. |
| **First + last + count precomputed** | `first_*_us`, `last_*_us`, `total_*` for each type. Combined `last_engagement_us` = MAX(lasts). `total_engagement` = SUM(counts). |
| **No quote posts** | `bsky.records` has zero `app.bsky.feed.post` rows. See "Fixing the quote-post gap" below. |
| **Raw event table** | `post_engagement_events` stores every individual repost/like/reply targeting top-level posts. ~140M rows. Enables temporal decay fitting and cascade analysis. |

---

## Table: `pau_db.post_lifetime` (aggregated, 1 row per post)

| Column | Type | Description |
|--------|------|-------------|
| `post_did` | VARCHAR(128) | DID of the post author (= the `did` identifier) |
| `post_rkey` | VARCHAR(16) | Record key — `(post_did, post_rkey)` = unique post ID |
| `created_at` | DATETIME | Post creation timestamp (UTC) |
| `first_reposted_us` | BIGINT | Earliest repost (µs), NULL if never |
| `last_reposted_us` | BIGINT | Latest repost (µs), NULL if never |
| `first_liked_us` | BIGINT | Earliest like (µs), NULL if never |
| `last_liked_us` | BIGINT | Latest like (µs), NULL if never |
| `first_replied_us` | BIGINT | Earliest direct reply (µs), NULL if never |
| `last_replied_us` | BIGINT | Latest direct reply (µs), NULL if never |
| `last_engagement_us` | BIGINT | `MAX(last_repost, last_like, last_reply)` |
| `total_reposts` | BIGINT | Repost count |
| `total_likes` | BIGINT | Like count |
| `total_replies` | BIGINT | Direct reply count |
| `total_engagement` | BIGINT | `SUM(counts)` |

## Table: `pau_db.post_engagement_events` (individual events, 1 row per engagement)

| Column | Type | Description |
|--------|------|-------------|
| `post_did` | VARCHAR(128) | Target post author DID |
| `post_rkey` | VARCHAR(16) | Target post rkey |
| `event_time_us` | BIGINT | When the engagement happened (µs) |
| `event_type` | VARCHAR(16) | `repost`, `like`, or `reply` |
| `actor_did` | VARCHAR(128) | DID of the user who engaged |

Grouped by `(post_did, post_rkey)` and ordered by `event_time_us` gives the
full engagement timeline for any post.

---

## SQL scripts

| Script | Purpose |
|--------|---------|
| `create_post_lifetime_table.sql` | CREATE TABLE (fresh start) |
| `migrate_add_first_columns.sql` | ALTER + DELETE (run once if upgrading from v1) |
| `populate_post_lifetime.sql` | INSERT ~15.3M rows |
| `create_post_engagement_events.sql` | CREATE TABLE for raw events |
| `populate_post_engagement_events.sql` | INSERT ~140M event rows |

## Usage

### Fresh start

```bash
mysql -h 10.18.74.14 -P 9030 -u pau -p \
  < post-lifetime/sql-scripts/create_post_lifetime_table.sql
mysql -h 10.18.74.14 -P 9030 -u pau -p \
  < post-lifetime/sql-scripts/populate_post_lifetime.sql
mysql -h 10.18.74.14 -P 9030 -u pau -p \
  < post-lifetime/sql-scripts/create_post_engagement_events.sql
mysql -h 10.18.74.14 -P 9030 -u pau -p \
  < post-lifetime/sql-scripts/populate_post_engagement_events.sql
```

### Upgrade from v1 (add first_* columns)

```bash
mysql -h 10.18.74.14 -P 9030 -u pau -p \
  < post-lifetime/sql-scripts/migrate_add_first_columns.sql
mysql -h 10.18.74.14 -P 9030 -u pau -p \
  < post-lifetime/sql-scripts/populate_post_lifetime.sql
```

### Analysis

```bash
uv run post-lifetime/analyze_post_lifetime.py
uv run post-lifetime/analyze_post_lifetime.py --no-plots
```

---

## EDA plan (next steps in `post-lifetime/eda/`)

| Script | Phase | Data source |
|--------|-------|-------------|
| `fit_powerlaw_counts.py` | Phase 1 | `post_lifetime` (total_* columns) |
| `fit_powerlaw_lifetimes.py` | Phase 2a | `post_lifetime` (last_engagement_us − created_at) |
| `temporal_decay.py` | Phase 2b | `post_engagement_events` (per-post event timelines) |
| `time_to_first.py` | Phase 3 | `post_lifetime` (first_* − created_at) |
| `cascade_ordering.py` | Phase 6 | `post_engagement_events` (event_type sequences) |
| `correlates.py` | Phase 5 | `post_lifetime` + `bsky.posts` + `pau_db.users` |

---

## Quick SQL queries

```sql
-- Time-to-first-engagement distribution (Phase 3)
SELECT
    CASE
        WHEN ttf_h < 0.0167   THEN '< 1 min'
        WHEN ttf_h < 1        THEN '1 min – 1 hr'
        WHEN ttf_h < 24       THEN '1 hr – 1 day'
        WHEN ttf_h < 168      THEN '1 day – 1 week'
        ELSE                        '> 1 week'
    END AS ttf_bucket,
    COUNT(*) AS posts
FROM (
    SELECT (first_reposted_us - UNIX_TIMESTAMP(created_at)*1000000)/3600000000.0 AS ttf_h
    FROM post_lifetime WHERE first_reposted_us IS NOT NULL
) t
GROUP BY ttf_bucket ORDER BY MIN(ttf_h);

-- Per-post event timeline (for temporal_decay.py)
SELECT event_time_us, event_type, actor_did
FROM post_engagement_events
WHERE post_did = 'did:plc:...' AND post_rkey = '...'
ORDER BY event_time_us;

-- Engagement cascade: what comes first?
-- For posts that have both reposts AND likes, which type arrives first?
SELECT
    CASE
        WHEN pl.first_reposted_us < pl.first_liked_us THEN 'repost first'
        WHEN pl.first_liked_us < pl.first_reposted_us THEN 'like first'
        ELSE 'simultaneous'
    END AS cascade_order,
    COUNT(*) AS posts
FROM post_lifetime pl
WHERE pl.first_reposted_us IS NOT NULL
  AND pl.first_liked_us IS NOT NULL
GROUP BY cascade_order;
```

## Fixing the quote-post gap

To include quote posts as replies, `bsky.posts` needs an `embed_uri` column.
A DBA would need to extract it from the original `record_json` in the JSONL
files and backfill.  Once done, add a fourth CTE to the populate scripts that
extracts `(post_did, post_rkey)` from `embed_uri`.
