# session-creation-tukey

Self-contained pipeline: raw Bluesky firehose → `pau_db.sessions_tukey`.

**Method:** Per-user adaptive session clustering via Tukey's fences (IQR).
Each user gets their own gap threshold: Q3 + 1.5 × IQR (floor 120 s, fallback
60 min if fewer than 4 gaps). All event types from `bsky.records` + `bsky.posts`
are used for gap estimation — likes, reposts, follows, blocks, profiles, posts,
and everything else.

---

## Quick start

```bash
# 1. Build core-events table (posts + replies + reposts, 1.75M users)
mysql -h 10.18.74.14 -P 9030 -u pau -p < 01_core_events.sql

# 2. Cluster into sessions → pau_db.sessions_tukey
uv run cluster_tukey.py --min-events 6 --max-events 500 --summary
```

---

## Files

| File | What |
|------|------|
| `01_core_events.sql` | CREATE + INSERT `pau_db.user_core_events` — posts, replies, reposts for all 1.75M users. Used ONLY to filter which DIDs to process (6–500 range). |
| `eda/` | Exploratory analysis of `user_core_events` — events per user, archetypes, gap distributions, coverage. Produces the 6–500 and 101–500 cutoffs. Run with `uv run eda/run.py`. |
| `cluster_tukey.py` | Fetches ALL events per user from `bsky.records` + `bsky.posts`, computes per-user Tukey threshold, clusters into sessions, writes `pau_db.sessions_tukey`. |

---

## Parameters

All defaults match the EDA recommendations (§8 of `eda/README.md`):

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `--min-events` | 6 | Minimum core events (removes tourists, 52.7% of users) |
| `--max-events` | 500 | Maximum core events (removes bots, 0.8% of users) |
| `--iqr-multiplier` | 1.5 | Tukey multiplier (Q3 + 1.5 × IQR) |
| `--fallback-threshold` | 60 | Fallback in minutes when user has < 4 gaps |
| `--min-gaps` | 4 | Minimum gaps required for per-user IQR |
| `--summary` | off | Print aggregate statistics after completion |

---

## Output table

```
pau_db.sessions_tukey
├── did, session_start, session_end, next_session_start
├── duration_s
├── likes, reposts, posts_authored, follows, other_actions
├── interactions (= likes + reposts)
├── total_actions
├── user_threshold_s          — per-user adaptive threshold
├── user_threshold_fallback   — 1 if fallback was used
└── user_gap_count            — number of inter-event gaps
```

---

## Regeneration

```bash
mysql -h 10.18.74.14 -P 9030 -u pau -p -e "TRUNCATE TABLE pau_db.sessions_tukey;"
uv run cluster_tukey.py --min-events 6 --max-events 500 --summary
```

`01_core_events.sql` is idempotent (`CREATE TABLE IF NOT EXISTS`) — run once.
