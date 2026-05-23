# Firehose — Bluesky Data Analytics

## Data sources

1. **Raw firehose:** `/data/nfs/projects/bluesky-des/` — folders with dates/hours and `.jsonl` files of all events from 6 days of April.
2. **StarRocks database:** `mysql -h 10.18.74.14 -P 9030 -u pau` — processed dump of the firehose. See `docs/DATABASE.md` for schema.

### Databases

- **`bsky`** — raw firehose data (read-only). Tables: `posts`, `records`.
- **`pau_db`** — derived/result tables (read-write). Credentials in `.env`.

---

## Projects

### Project 1: Topology (follow graph)

Reconstruct the Bluesky follow network from firehose events.

| What | Where |
|------|-------|
| Firehose ingest (Go) | `topology/firehose/` |
| API crawler | `topology/crawler/crawl_followers.py` |
| Forest Fire sampling | `topology/sampling/forest_fire.py` |
| Sample validation | `topology/sampling/validate.py` |
| Crawler docs | `topology/crawler/MONITORING.md` |

---

### Project 2: Session description

Define session lengths and inter-session gaps via per-user adaptive Tukey IQR clustering.

| What | Where |
|------|-------|
| **Source tables** | `pau_db.all_events`, `pau_db.engaged_events` |
| **Session tables** | `pau_db.sessions_all` (all events), `pau_db.sessions_engagement` (no likes) |
| Session creation | `sessions/creation-tukey/cluster_all.py`, `cluster_engagement.py` |
| Session EDA | `docs/sessions/eda.md` — comparison of both tables |
| Distribution fitting | `docs/sessions/analysis/distribution-fitting.md` — per-user MLE fits |
| Method comparison | `docs/sessions/tukey-vs-threshold.md` — sessions_all vs sessions_engagement |
| Inter-post gaps | `docs/inter-post-gaps.md` — time between posts within sessions |

---

### Project 3: Post lifetime

How long does a post stay alive from creation to last engagement?

| What | Where |
|------|-------|
| Source tables | `pau_db.post_lifetime`, `pau_db.post_engagement_events` |
| Full results | `docs/post-lifetime.md` — all phases, simulation calibration |
| Scripts | `post-lifetime/eda/*.py` |
| SQL | `post-lifetime/sql/*.sql` |

---

### Project 4: Structural virality

Wiener index ν(T) of repost cascade trees (Goel et al., 2016).

| What | Where |
|------|-------|
| Results | `docs/structural-virality.md` |
| Scripts | `structural-virality/` — SQL dump + Go compute + Python plots |

---

## Global EDA

| What | Where |
|------|-------|
| Full firehose EDA | `docs/EDA.md` — event types, users, ratios, power-law fits |
| Database schema | `docs/DATABASE.md` — all tables in `bsky` and `pau_db` |

---

## ⛔ Deprecated tables

These tables were built from a buggy intermediate filter (`user_core_events`)
and should not be regenerated:

| Deprecated | Replacement |
|------------|-------------|
| `pau_db.user_core_events` | `pau_db.all_events` / `pau_db.engaged_events` |
| `pau_db.user_core_events_human` | Not needed (filtering is in `all_events` / `engaged_events`) |
| `pau_db.user_core_events_dominant` | Not needed |
| `pau_db.sessions_tukey` | `pau_db.sessions_all` + `pau_db.sessions_engagement` |
| `pau_db.sessions_threshold` | `pau_db.sessions_engagement` |

See `docs/DATABASE.md` for the current active tables.
