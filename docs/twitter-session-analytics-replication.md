# Twitter / X session analytics replication — Bluesky adaptation

This document explains the steps taken to replicate the [Twitter Session
Analytics](https://link.springer.com/chapter/10.1007/978-3-319-47874-6_6)
study (Kooti et al., SocInfo 2016) with Bluesky firehose data. It covers the
EDA that informed the filtering strategy, the elbow-method threshold detection,
and the full pipeline that produced `pau_db.sessions_threshold`.

---

## 1. Event mapping

The original study models user activity with **three event types**:

| Event | Twitter / X | Bluesky equivalent |
|-------|------------|-------------------|
| Original content | Tweet | Top-level `app.bsky.feed.post` (no reply parent) |
| Conversation engagement | Reply / Quote-tweet | `app.bsky.feed.post` with a reply parent |
| Amplification | Retweet | `app.bsky.feed.repost` |

Quote tweets are **not** a separate protocol action — they are regular posts
that embed another post in their JSON payload. On Bluesky they naturally fall
into "post" (if top-level) or "reply" (if part of a thread) categories.

Likes are deliberately excluded, following the study methodology: they are
passive/low-effort and don't represent content creation or curation intent.

---

## 2. Data filters — three `user_core_events` tables

The original `user_core_events` table contains **all** users (1,750,802 DIDs,
53.5M events). Based on the EDA (§3), two filtered subsets were created:

| Table | Filter | Users | Gaps | Purpose |
|-------|--------|-------|------|---------|
| `user_core_events` | all events | 1,750,802 | 51.7M | Raw/unfiltered reference |
| `user_core_events_dominant` | **101–500 events** | ~96K (5.5%) | 19.3M (37.4%) | Elbow-method threshold — the dominant gap stratum |
| `user_core_events_human` | **6–500 events** | ~810K (46%) | 36.3M (70%) | Per-user IQR session clustering |

### Table schema (all three)

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(128) | User identifier |
| `time_us` | bigint | Event timestamp (microseconds since epoch) |
| `event_type` | varchar(16) | `'post'`, `'reply'`, or `'repost'` |

- **Engine:** OLAP, `DUPLICATE KEY(did, time_us)`
- **Buckets:** 32

### Size (user_core_events, 2026-05-16)

| event_type | Rows |
|------------|------|
| `post` | 15,282,626 |
| `reply` | 12,791,049 |
| `repost` | 25,388,590 |
| **Total** | **53,462,265** |
| **Unique users** | **1,750,802** |

### SQL scripts

Located in [`session-analysis/sql-scripts/`](../session-analysis/sql-scripts/):

| Script | Creates |
|--------|---------|
| `create_core_events_table.sql` | `user_core_events` (all users) |
| `insert_core_events.sql` | Populates from `bsky.posts` and `bsky.records` |
| `create_core_events_dominant.sql` | `user_core_events_dominant` (101–500) |
| `insert_core_events_dominant.sql` | Populates from `user_core_events` |
| `create_core_events_human.sql` | `user_core_events_human` (6–500) |
| `insert_core_events_human.sql` | Populates from `user_core_events` |

To regenerate:

```bash
mysql -h 10.18.74.14 -P 9030 -u pau -p < session-analysis/sql-scripts/create_core_events_table.sql
mysql -h 10.18.74.14 -P 9030 -u pau -p < session-analysis/sql-scripts/insert_core_events.sql
mysql -h 10.18.74.14 -P 9030 -u pau -p < session-analysis/sql-scripts/create_core_events_dominant.sql
mysql -h 10.18.74.14 -P 9030 -u pau -p < session-analysis/sql-scripts/insert_core_events_dominant.sql
mysql -h 10.18.74.14 -P 9030 -u pau -p < session-analysis/sql-scripts/create_core_events_human.sql
mysql -h 10.18.74.14 -P 9030 -u pau -p < session-analysis/sql-scripts/insert_core_events_human.sql
```

---

## 3. Exploratory Data Analysis

A systematic EDA was run to understand *who* the session analysis is about,
*before* imposing any threshold. The full EDA is documented in
[`session-analysis/eda/README.md`](../session-analysis/eda/README.md).
Here we surface the findings most relevant to the session threshold.

### 3.1 Time window

The firehose snapshot covers **exactly 8 days**:

| Timestamp | Value |
|-----------|-------|
| First event | ~2026-04-11 |
| Last event | ~2026-04-19 |
| Span | 8.0 days |

### 3.2 Event-count distribution (EDA §1)

The event-count per user follows a power-law with **α = 1.68, xmin = 5**.
Half of all users (P50) have ≤5 events in 8 days.

| Percentile | Events |
|------------|--------|
| P1 | 1 |
| P10 | 1 |
| P25 | 2 |
| P50 | 5 |
| P75 | 18 |
| P90 | 61 |
| P95 | 124 |
| P99 | 424 |

Since xmin = 5, the heavy tail starts at 6 events — validating ≥6 as the
tourist-removal cutoff.

### 3.3 User archetypes (EDA §2)

Users split into distinct behavioural classes based on event-type composition:

| Archetype | Users | % | Description |
|-----------|-------|---|-------------|
| Tourist | 922,044 | 52.7% | ≤5 events total |
| Engager | 418,525 | 23.9% | Like-heavy, little content creation |
| Balanced | 148,994 | 8.5% | Mix of creation and engagement |
| Creator | 142,948 | 8.2% | Post/reply-heavy, little liking |
| Curator | 90,655 | 5.2% | Repost-heavy |

Creators and engagers form **distinct populations** in ratio-space (likes vs
posts hexbin), not a single continuum. They likely have different session
rhythms — a motivation for per-user adaptive thresholds.

### 3.4 Activity span (EDA §3)

| Active days | Users | % |
|-------------|-------|---|
| 1 day only | 570,645 | 32.6% |
| 2 days | 273,933 | 15.6% |
| 3+ days | 906,224 | 51.8% |
| 7–8 days | 352,560 | 20.1% |

A third of users appear on only one day. Their "inter-arrival gaps" are
intra-burst activity, not between-session pauses. For session analysis,
these users produce meaningless gap distributions.

### 3.5 Per-user gap distributions (EDA §4)

Per-user median inter-arrival gaps span **six orders of magnitude**:

| Percentile | Median gap (per user) |
|------------|----------------------|
| P10 | 63s (1.1 min) |
| P25 | 338s (5.6 min) |
| P50 | 6,791s (113 min) |
| P75 | 61,000s (1,017 min) |
| P90 | 158,331s (2,639 min) |

Only **23.9%** of users have median gap < 5 min. The distribution varies
systematically by event-count bucket — active users have consistently tighter
medians than casual users. A single global threshold will be too loose for
power users and too tight for casuals.

### 3.6 Coverage: who contributes the gaps? (EDA §5)

This is the critical table. It quantifies which users the elbow is actually
computed from:

| Events per user | % Users | % Gaps | Role |
|-----------------|---------|--------|------|
| 1 | 22.6% | 0.0% | Irrelevant (no gaps) |
| 2–5 | 30.1% | 2.1% | Negligible |
| 6–25 | 27.7% | 10.6% | Meaningful |
| 26–100 | 13.4% | 22.3% | Meaningful |
| **101–500** | **5.5%** | **37.4%** | **DOMINANT — drives the elbow** |
| 501+ | 0.8% | 27.7% | Bot-heavy (compress elbow downward) |

**52.7% of users contribute 2.1% of gaps.** They are invisible noise.

**The 101–500 bucket alone supplies 37.4% of all gaps** — this is the
population the elbow threshold is effectively about. Combined with the 501+
bucket (27.7%, bot-heavy), **6.3% of users drive 65% of the analysis**.

### 3.7 EDA conclusion & filtering strategy

1. **Remove tourists** — `total_events ≥ 6`. 52.7% of users, 2.1% of gaps.
2. **Remove bots** — `total_events ≤ 500` (~≤62/day). 0.8% of users, 27.7% of
   gaps. They compress the elbow downward.
3. **The 101–500 stratum is dominant** (37.4% of gaps). The elbow should be
   computed on this stratum alone — it *is* the elbow.
4. **The 6–500 human range** (46% of users, 70% of gaps) is suitable for
   per-user adaptive (IQR) thresholds, preserving the diversity of casual,
   regular, and power-user rhythms.

Two tables implement this:
- `user_core_events_dominant` — 101–500 events, for the fixed-threshold elbow.
- `user_core_events_human` — 6–500 events, for per-user IQR clustering.

---

## 4. Empirical session threshold — dominant stratum

### 4.1 Methodology

**Source:** _"How Many Tweets Does It Take to Make a Session?"_ — Kooti et al.,
SocInfo 2016 ([link](https://link.springer.com/chapter/10.1007/978-3-319-47874-6_6))

1. **Sample** — Randomly select 175,000 users from
   `pau_db.user_core_events_dominant` (101–500 events). The table has ~96K
   users so all are sampled.

2. **Inter-arrival gaps** — For each user, sort events by `time_us` and
   compute Δt = t_{n+1} − t_n (seconds) between every consecutive action.
   First event per user has no predecessor and is excluded.

3. **Histogram** — Aggregate all gaps into 10-second bins from 0 to 3600 s
   (60 minutes). The distribution shows a sharp spike at very short gaps
   (within-session bursts), a steep decline, then a long flat tail
   (between-session pauses).

4. **Elbow detection (Kneedle algorithm)** — The [Kneedle
   algorithm](https://ieeexplore.ieee.org/document/5961514) (Satopää et al.,
   2011) finds the point of maximum curvature on the histogram. This is the
   transition from "bursty within-session" to "between-session" behaviour.
   Implemented via the [`kneed`](https://pypi.org/project/kneed/) package
   (`curve="convex"`, `direction="decreasing"`).

### 4.2 Result

Running the elbow on the dominant 101–500 stratum (95,795 users,
19.4M events, 16.3M gaps):

| Metric | Value |
|--------|-------|
| Users sampled | 95,795 |
| Total gaps | 16,342,577 |
| Mean gap | 453.9s |
| Median gap | 104.4s |
| **Elbow** | **265s (4.4 min)** |

### 4.3 Comparison with earlier unfiltered runs

| Source population | Elbow | Notes |
|-------------------|-------|-------|
| All users (unfiltered) | 195s (3.2m) | Bot-distorted |
| ≥6 events, ≤100/day | 285s (4.8m) | Broad human, manual filter |
| 35–100/day | 255s (4.2m) | Narrow active-human band |
| **101–500 events (dominant stratum)** | **265s (4.4m)** | **Recommended — data-driven filter** |

The 265s result lands between the broad-human (285s) and active-human (255s)
thresholds — consistent with the dominant stratum being the most active
genuinely-human slice, without the manual bucket boundaries of the earlier
attempts.

### 4.4 Interpretation

- **265s (4.4 min)** is the threshold that reflects the population that
  actually drives the gap distribution — not tourists, not bots, but the
  101–500-event users who supply 37.4% of all gaps.
- This is roughly **half of Twitter's 10-minute threshold**, consistent with
  Bluesky being a younger, more real-time platform.
- The per-user gap analysis (EDA §4) shows that even within this dominant
  stratum, individual users have very different gap distributions — hence
  the per-user adaptive (IQR) method is recommended for session clustering
  even if the elbow provides the global fallback.

### 4.5 Script

[`session-analysis/session_threshold_elbow.py`](../session-analysis/session_threshold_elbow.py)
defaults to the `user_core_events_dominant` table:

```bash
# Dominant-stratum elbow (default — 265s)
uv run session-analysis/session_threshold_elbow.py

# Other source tables:
uv run session-analysis/session_threshold_elbow.py --source-table pau_db.user_core_events_human
```

Output: histogram plot saved to `session-analysis/results/session_elbow_*.png`.

---

## 5. Populating `pau_db.sessions_threshold`

With the threshold determined (265s), the session table was populated by
clustering every user's events from the dominant stratum.

### 5.1 Table schema

**Database:** `pau_db`  
**Table:** `sessions_threshold`

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(128) | User identifier (DID) |
| `session_start` | bigint | First event timestamp in the session (µs since epoch) |
| `session_end` | bigint | Last event timestamp in the session |
| `next_session_start` | bigint | Start of the next session (NULL for the last one) |
| `duration_s` | double | Session duration = `(session_end − session_start) / 1e6` |
| `reposts` | int | Reposts made during the session |
| `posts_authored` | int | Posts + replies authored during the session |
| `threshold_s` | double | The global threshold used (265.0) |

- **Engine:** OLAP
- **Key:** `DUPLICATE KEY(did, session_start)`
- **Buckets:** 32, distributed by `HASH(did)`
- **DDL:** defined in `session_core_events.py` (executed automatically)

### 5.2 Clustering algorithm

For each user, events are fetched from `user_core_events_dominant` ordered by
`time_us`. The fixed-threshold rule is applied:

```
threshold_us = 265 * 1_000_000

for event_i in events:
    if gap between event_i and event_{i-1} > threshold_us:
        start a new session
    else:
        add event_i to the current session
```

For each resulting session, the script records:
- Start and end timestamps
- `next_session_start` (the start of the immediately following session; NULL
  for the last one — enables gap-between-sessions queries)
- Counts of reposts and posts authored

### 5.3 Event-type mapping

| `event_type` | Mapped to | Source |
|-------------|-----------|--------|
| `'post'` | `posts_authored` | Top-level posts (no reply parent) |
| `'reply'` | `posts_authored` | Posts that are part of a reply chain |
| `'repost'` | `reposts` | `app.bsky.feed.repost` records |

Likes are absent — the core events table was designed without them per the
study methodology.

### 5.4 Script

[`session-analysis/session_core_events.py`](../session-analysis/session_core_events.py)

```bash
# Full run (populated 2026-05-17):
uv run session-analysis/session_core_events.py \
  --source-table pau_db.user_core_events_dominant \
  --threshold 265 \
  --min-events 1 --max-events 999999 \
  --summary

# Resume partial run (if interrupted):
uv run session-analysis/session_core_events.py \
  --did-file /tmp/remaining_dids.txt \
  --source-table pau_db.user_core_events_dominant \
  --threshold 265 \
  --min-events 1 --max-events 999999 \
  --summary
```

The `--did-file` flag accepts a plain-text file of DIDs (one per line) and
skips the DID-discovery query — useful for resuming an interrupted run.

### 5.5 Processing flow

```
user_core_events_dominant (95,795 DIDs)
  │
  ├─ Batch 1:  2,000 DIDs → fetch events → cluster → INSERT
  ├─ Batch 2:  2,000 DIDs → fetch events → cluster → INSERT
  │  ...
  └─ Batch 48: 1,795 DIDs → fetch events → cluster → INSERT
                                    │
                                    ▼
                          pau_db.sessions_threshold
                          (8,470,675 sessions)
```

- **Batch size:** 2,000 DIDs per `WHERE did IN (…)` query
- **Insert flush:** every 50,000 rows
- **Source:** `pau_db.user_core_events_dominant` (95,795 users, 19.4M events)

### 5.6 Final statistics

| Metric | Value |
|--------|-------|
| Users | 95,795 |
| Sessions | 8,470,675 |
| Avg sessions per user | 88.4 |
| Avg session duration | 90 s |
| Median session duration | 0 s (many single-event sessions) |
| P75 session duration | 104 s |
| P90 session duration | 277 s |
| Max session duration | 33,238 s (~9.2 h) |
| Total reposts | 9,758,802 |
| Total posts authored | 9,666,935 |

### 5.7 Example queries

```sql
-- Sessions per user
SELECT did, COUNT(*) AS n_sessions
FROM pau_db.sessions_threshold
GROUP BY did
ORDER BY n_sessions DESC
LIMIT 20;

-- Duration distribution
SELECT
  CASE
    WHEN duration_s = 0 THEN '0s (single-event)'
    WHEN duration_s < 60 THEN '<1 min'
    WHEN duration_s < 300 THEN '1–5 min'
    WHEN duration_s < 600 THEN '5–10 min'
    WHEN duration_s < 1800 THEN '10–30 min'
    WHEN duration_s < 3600 THEN '30–60 min'
    ELSE '>1 hour'
  END AS bucket,
  COUNT(*) AS sessions
FROM pau_db.sessions_threshold
GROUP BY bucket
ORDER BY MIN(duration_s);

-- Inter-session gaps (time between consecutive sessions per user)
SELECT
  ROUND(AVG(next_session_start - session_end) / 1e6, 0) AS avg_gap_s,
  ROUND(PERCENTILE(next_session_start - session_end, 0.5) / 1e6, 0) AS median_gap_s
FROM pau_db.sessions_threshold
WHERE next_session_start IS NOT NULL;

-- Users with the most posts per session on average
SELECT did,
       COUNT(*) AS n_sessions,
       ROUND(AVG(posts_authored), 1) AS avg_posts_per_session,
       ROUND(AVG(reposts), 1) AS avg_reposts_per_session
FROM pau_db.sessions_threshold
GROUP BY did
ORDER BY avg_posts_per_session DESC
LIMIT 20;
```

---

## 6. Final recommendation

| Method | Source table | Threshold | When to use |
|--------|-------------|-----------|-------------|
| Fixed threshold (elbow) | `user_core_events_dominant` (101–500) | **265s (4.4 min)** | Global fallback; all users share one boundary |
| Per-user adaptive (IQR) | `user_core_events_human` (6–500) | Q3 + 1.5×IQR per user, 2-min floor | Recommended — adapts to each user's rhythm |

Applied to the session-clustering scripts:

```bash
# Fixed threshold (as done — writes to sessions_threshold)
uv run session-analysis/session_core_events.py \
  --source-table pau_db.user_core_events_dominant \
  --threshold 265 \
  --summary

# Per-user IQR (adaptive, human range — writes to sessions_tukey)
uv run session-analysis/session_engagement_analysis.py \
  --did-file session-analysis/results/users.txt \
  --fallback-threshold 4.4 \
  --summary
```

---

## 7. Using the filtered tables

Instead of filtering in the Python script with `--min-events` / `--max-events`,
query the pre-filtered tables directly:

```sql
-- Dominant stratum (for elbow / fixed threshold)
SELECT did, time_us, event_type
FROM pau_db.user_core_events_dominant
ORDER BY did, time_us;

-- Human range (for IQR / adaptive threshold)
SELECT did, time_us, event_type
FROM pau_db.user_core_events_human
ORDER BY did, time_us;
```

The `event_type` values (`'post'`, `'reply'`, `'repost'`) map directly to
`_inc()` in the Python session-clustering code.

---

## 8. Scripts index

| Script | Purpose |
|--------|---------|
| `session-analysis/eda/` | Systematic EDA — 8 sections, see `eda/README.md` |
| `session-analysis/eda.py` | EDA orchestrator |
| `session-analysis/sql-scripts/create_core_events_table.sql` | DDL for `pau_db.user_core_events` (all users) |
| `session-analysis/sql-scripts/insert_core_events.sql` | Populate `user_core_events` |
| `session-analysis/sql-scripts/create_core_events_dominant.sql` | DDL for `user_core_events_dominant` (101–500) |
| `session-analysis/sql-scripts/insert_core_events_dominant.sql` | Populate `user_core_events_dominant` |
| `session-analysis/sql-scripts/create_core_events_human.sql` | DDL for `user_core_events_human` (6–500) |
| `session-analysis/sql-scripts/insert_core_events_human.sql` | Populate `user_core_events_human` |
| `session-analysis/session_threshold_elbow.py` | Elbow-method threshold detection (defaults to dominant table) |
| `session-analysis/session_core_events.py` | Session clustering with fixed threshold (defaults to human table) |
| `session-analysis/session_engagement_analysis.py` | Session clustering with Tukey/IQR (adaptive) |
| `session-analysis/eda_event_histogram.py` | Quick events-per-user histogram (pre-EDA) |
| `session-analysis/user_time_entropy.py` | Per-user time-interval entropy (bot detection) |

---

## 9. Notes

- **Likes are excluded** — passive, don't represent content creation intent.
- **Quote posts are included** — they are regular `app.bsky.feed.post` records
  with an embedded reference. They fall into `'post'` or `'reply'` depending on
  whether they have a `reply_root_uri`.
- **Only `operation = 'create'`** reposts are kept (deletes/updates ignored).
- **Entropy table available** at `pau_db.user_time_entropy` for users who want
  to filter by posting-interval regularity instead of raw event counts.
- **EDA is at [`session-analysis/eda/README.md`](../session-analysis/eda/README.md)** —
  the 8-section systematic analysis that produced the filtering strategy
  and motivated the two-table approach.
