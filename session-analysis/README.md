# Session Engagement Analysis — Bluesky Firehose

Estimates how often Bluesky users like/repost vs. browse-and-do-nothing, using
**per-user adaptive session clustering** on all visible user actions (likes,
reposts, posts authored).

---

## Files

| File | Description |
|------|-------------|
| `session_engagement_analysis.py` | Main analysis script |
| `load_to_sqlite.py` | Loads CSV results into `sessions.db` (SQLite) |
| `users.txt` | 3,086,991 unique DIDs (one per line) |
| `session_engagement_results.csv` | Output: 29.3M session rows with engagement metrics |
| `sessions.db` | SQLite database (same data, indexed for querying) |
| `run.log` | Run log with final summary statistics |

---

## Data sources

All user actions come from the `bsky` database at `10.18.74.14:9030`:

| Source | What | Rows |
|--------|------|------|
| `bsky.records` (collection=`app.bsky.feed.like`) | Likes | 161.7M |
| `bsky.records` (collection=`app.bsky.feed.repost`) | Reposts | 26.4M |
| `bsky.posts` | Posts authored | 28.1M |

These are extracted per-user, sorted by time, and clustered into sessions.

---

## Procedure

### Step 1 — Collect all timestamps per user

For each user, we collect **every visible action** with its microsecond timestamp:

```
User A:  like(t₁), repost(t₂), like(t₃), post(t₄), like(t₅), ...
User B:  post(t₁), like(t₂), like(t₃), ...
```

Three action types are tracked independently:
- **`like`** — user liked someone else's post
- **`repost`** — user reposted someone else's post
- **`post`** — user authored their own post

### Step 2 — Compute per-user adaptive session threshold

For each user, we compute the gaps between consecutive actions:

```
g₁ = t₂ - t₁,   g₂ = t₃ - t₂,   g₃ = t₄ - t₃,   ...
```

Then apply **Tukey's fences** (outlier detection) to that user's gap distribution:

```
Q1  = 25th percentile of gaps
Q3  = 75th percentile of gaps
IQR = Q3 - Q1

session_boundary_threshold = Q3 + 1.5 × IQR
```

Any gap larger than this user-specific threshold is a **session boundary**.  
A hard floor of 120 seconds prevents fragmenting a single browsing burst.

**Example:** If a user typically acts every 30–90 seconds (Q1=30s, Q3=90s, IQR=60s),  
their threshold is `90 + 1.5×60 = 180s`. Any gap > 3 minutes starts a new session.

**Fallback:** Users with fewer than 4 inter-event gaps use a global default of 60 minutes.

### Step 3 — Cluster into sessions

```
Session 1: [t₁ ── t₂ ── t₃]     (gap t₃→t₄ exceeds threshold)
Session 2: [t₄ ── t₅]           (gap t₅→t₆ exceeds threshold)
Session 3: [t₆ ── t₇ ── t₈]
```

For each session we record:
- `likes`, `reposts`, `posts_authored` — counts of each action type
- `interactions` — `likes + reposts` (actions on other people's content)
- `duration_s` — `(t_end - t_start) / 1e6`

### Step 4 — Estimate posts seen per session

This is the key estimation step. We **cannot know** which posts a user saw and
scrolled past without acting. Instead we use time as a proxy:

```
posts_seen_est = max(
    session_duration_s / avg_view_time_per_post,   ← time-based estimate
    interactions + posts_authored + floor_posts     ← action floor
)
```

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `avg_view_time_per_post` (`-s`) | 5s | How long a user spends looking at one post on average |
| `floor_posts` (`-f`) | 4 | Minimum unseen posts assumed even in the shortest session |

**Why the floor?** Without it, a single-like session (duration=0) would produce  
`posts_seen = 0/5 = 0`, clamped to 1 → engagement rate = `1/1 = 100%`.  
The floor assumes even the briefest browsing involves seeing at least `N` posts.

**Example:**
- Session of 100s with 3 likes and 0 reposts, no posts authored:
  - Time-based: `100 / 5 = 20` posts seen
  - Action floor: `3 + 0 + 4 = 7`
  - Final: `max(20, 7) = 20` posts seen
  - Engagement rate: `3 / 20 = 15%`

**Sensitivity:** The `avg_view_time_per_post` parameter dominates the result.  
Vary it to model different browsing behaviors:

| `-s` | Interpretation | Median posts/session | Median engagement |
|------|---------------|---------------------|-------------------|
| 2s | Fast scrolling | 50 | ~6% |
| 5s | Default | 20 | ~20% |
| 10s | Careful reading | 10 | ~30% |
| 15s | Deep engagement | 7 | ~44% |

---

## Results summary

Computed across **3,086,991 users** → **2,281,225 active users** (≥2 actions) → **29.3M sessions**.

| Metric | Median | Mean | P25 | P75 |
|--------|--------|------|-----|-----|
| Engagement rate | 20.0% | 19.5% | 3.1% | 31.6% |
| Session duration | 100s | 8,126s | 3s | 493s |
| Likes per session | 3 | 6.0 | 1 | 6 |
| Reposts per session | 0 | 0.4 | 0 | 0 |
| Posts authored per session | 0 | 0.3 | 0 | 0 |
| Interactions (likes + reposts) | 3 | 6.4 | 1 | 7 |
| Est. posts seen per session | 20 | 1,627 | 6 | 99 |
| Users on fallback threshold | — | 24% | — | — |

---

## Database schema (`sessions.db`)

```sql
CREATE TABLE sessions (
    did              TEXT NOT NULL,      -- User identifier (DID)
    session_start    INTEGER NOT NULL,   -- Session start (microseconds since epoch)
    session_end      INTEGER NOT NULL,   -- Session end (microseconds since epoch)
    duration_s       REAL NOT NULL,      -- Duration in seconds
    likes            INTEGER NOT NULL,   -- Likes given during session
    reposts          INTEGER NOT NULL,   -- Reposts made during session
    posts_authored   INTEGER NOT NULL,   -- Posts created during session
    interactions     INTEGER NOT NULL,   -- likes + reposts (convenience column)
    user_threshold_s REAL NOT NULL,      -- Per-user adaptive gap threshold (seconds)
    user_threshold_fallback INTEGER NOT NULL,  -- 1 if fallback threshold was used
    user_gap_count   INTEGER NOT NULL    -- Number of inter-event gaps for this user
);
```

Indexed on `did` and `session_start` for fast user-level and time-range queries.

---

## Usage

```bash
# Run the analysis (already done; results in session_engagement_results.csv):
uv run session-metrics/session_engagement_analysis.py \
  --did-file session-metrics/users.txt \
  --summary \
  -o session-metrics/session_engagement_results.csv \
  2> session-metrics/run.log

# Load into SQLite for querying:
uv run session-metrics/load_to_sqlite.py

# Then query with sqlite3:
sqlite3 session-metrics/sessions.db

# Key parameters:
#   -s 5     avg seconds viewing one post (default: 5)
#   -f 4     floor unseen posts per session (default: 4)
#   -q 1.5   IQR multiplier for Tukey's fences (default: 1.5)
#   -G 60    fallback gap in minutes for sparse users (default: 60)

# Example: sensitivity analysis across different viewing speeds
for s in 2 5 10 15; do
    uv run session-metrics/session_engagement_analysis.py \
      --did-file session-metrics/users.txt \
      --summary -s $s -o /dev/null 2>&1 | grep -A6 "Engagement rate"
done
```

---

## Example queries

```sql
-- Top users by total interactions
SELECT did, COUNT(*) as sessions,
       SUM(likes) as total_likes,
       SUM(reposts) as total_reposts,
       SUM(posts_authored) as total_posts,
       ROUND(AVG(duration_s), 1) as avg_session_s
FROM sessions
GROUP BY did
ORDER BY total_likes DESC
LIMIT 20;

-- Engagement rate distribution
SELECT
    CASE
        WHEN rate = 0 THEN '0% (never interact)'
        WHEN rate < 0.01 THEN '<1%'
        WHEN rate < 0.05 THEN '1-5%'
        WHEN rate < 0.10 THEN '5-10%'
        WHEN rate < 0.20 THEN '10-20%'
        WHEN rate < 0.30 THEN '20-30%'
        WHEN rate < 0.50 THEN '30-50%'
        ELSE '50%+'
    END AS engagement_bucket,
    COUNT(*) AS sessions
FROM (
    SELECT CAST(interactions AS REAL) / NULLIF(
        MAX(duration_s / 5.0, interactions + posts_authored + 4), 0
    ) AS rate
    FROM sessions
)
GROUP BY engagement_bucket
ORDER BY MIN(rate);

-- Sessions per user distribution
SELECT
    CASE
        WHEN n = 1 THEN '1'
        WHEN n BETWEEN 2 AND 5 THEN '2-5'
        WHEN n BETWEEN 6 AND 20 THEN '6-20'
        WHEN n BETWEEN 21 AND 100 THEN '21-100'
        ELSE '100+'
    END AS sessions_bucket,
    COUNT(*) AS user_count
FROM (SELECT did, COUNT(*) AS n FROM sessions GROUP BY did)
GROUP BY sessions_bucket
ORDER BY MIN(n);
```

---

*Analysis run 2026-05-15. IQR multiplier=1.5, avg_view_time=5s/post, floor_posts=4.*
