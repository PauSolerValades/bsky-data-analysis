# Bluesky Firehose Data — Database Schema & Data Types

**Database:** `bsky`  
**Host:** `10.18.74.14:9030`  

This database contains a selective dump from the Bluesky AT Protocol firehose. It is split into two tables:

| Table | Row count | Description |
|-------|-----------|-------------|
| `posts` | ~28.1 million | Normalized post content (text, language, reply chains) |
| `records` | ~212.5 million | All AT Protocol records (raw firehose events — likes, reposts, follows, blocks, profiles, etc.) |

---

## Table: `posts`

Dedicated to **post content only** (i.e., `app.bsky.feed.post` records). This table is pre-filtered and normalized: only the fields relevant to post analysis are kept.

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(64) | Decentralized Identifier of the post author |
| `rkey` | varchar(16) | Record key (unique within the author's repo) |
| `time_us` | bigint | Firehose event timestamp (microseconds) |
| `created_at` | datetime | Post creation timestamp (UTC) |
| `post_text` | varchar(65533) | Full text content of the post |
| `lang` | varchar(16) | Language tag (e.g., `en`, `ja`, `ko`) — nullable |
| `reply_root_uri` | varchar(256) | AT URI of the root post in the reply chain (null for top-level posts) |
| `reply_root_cid` | varchar(64) | Content ID (hash) of the root post |
| `reply_parent_uri` | varchar(256) | AT URI of the immediate parent post in the thread |
| `reply_parent_cid` | varchar(64) | Content ID of the immediate parent post |

### Key statistics

- **Total posts:** ~28.1 million
- **Top-level posts:** ~15.3 million (54.4%)
- **Replies:** ~12.8 million (45.6%)
- **Unique authors:** ~1.45 million
- **Date range:** 2026-04-11 through 2028-01-09 (some anomalous timestamps exist)

### Language distribution (top 10)

| Language | Post count | % |
|----------|------------|---|
| `en` (English) | 17,055,196 | 60.8% |
| `NULL` (unknown) | 3,287,521 | 11.7% |
| `ja` (Japanese) | 2,975,376 | 10.6% |
| `pt` (Portuguese) | 796,573 | 2.8% |
| `de` (German) | 789,156 | 2.8% |
| `es` (Spanish) | 732,974 | 2.6% |
| `fr` (French) | 455,077 | 1.6% |
| `ko` (Korean) | 395,676 | 1.4% |
| `en-US` | 201,593 | 0.7% |
| `nl` (Dutch) | 193,278 | 0.7% |

### Data types

- **Top-level posts** (`reply_root_uri IS NULL`): Original posts that start a new thread or stand alone.
- **Replies** (`reply_root_uri IS NOT NULL`): Posts made in response to another post. The full reply chain can be reconstructed via `reply_root_uri` (the thread) and `reply_parent_uri` (the direct parent).

---

## Table: `records`

A comprehensive dump of **all AT Protocol record events** from the firehose. Each row represents a `create`, `update`, or `delete` operation on any lexicon collection.

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(64) | DID of the user performing the action |
| `time_us` | bigint | Firehose event timestamp (microseconds) |
| `rev` | varchar(16) | Revision ID of the record |
| `operation` | varchar(8) | Event type: `create`, `update`, or `delete` |
| `collection` | varchar(128) | AT Protocol lexicon collection (e.g., `app.bsky.feed.like`) |
| `rkey` | varchar(64) | Record key |
| `cid` | varchar(64) | Content ID (hash) — null for deletions |
| `created_at` | datetime | Record creation timestamp — nullable |
| `subject_uri` | varchar(256) | URI of the subject record (used for likes, reposts, blocks) |
| `subject_cid` | varchar(64) | CID of the subject record |
| `subject_did` | varchar(64) | DID of the subject (used for follows, blocks) |
| `via_uri` | varchar(256) | Indirect reference URI |
| `via_cid` | varchar(64) | Indirect reference CID |
| `record_json` | json | Full record payload as JSON |

### Key statistics

- **Total records:** ~212.5 million
- **Unique authors:** ~2.84 million
- **Operations:** `create` (205.9M, 96.9%), `delete` (5.8M, 2.7%), `update` (0.8M, 0.4%)
- **Date range:** spans from anomalous dates (year 0001) through 2826 — most real data is from 2026

### Record types by collection

| Collection | Count | % | Description |
|------------|-------|---|-------------|
| `app.bsky.feed.like` | 161,700,519 | 76.1% | Likes on posts |
| `app.bsky.feed.repost` | 26,372,977 | 12.4% | Reposts / retweets |
| `app.bsky.graph.follow` | 18,774,479 | 8.8% | Follow relationships |
| `app.bsky.graph.block` | 1,740,380 | 0.8% | Block relationships |
| `app.bsky.feed.threadgate` | 1,472,652 | 0.7% | Thread reply controls (who can reply) |
| `app.bsky.actor.profile` | 841,651 | 0.4% | Profile metadata (display name, bio, avatar) |
| `app.bsky.feed.postgate` | 801,586 | 0.4% | Post interaction controls (quote posts, embeds) |
| `app.bsky.graph.listitem` | 451,747 | 0.2% | Items in a user list |
| `app.bsky.actor.status` | 270,489 | 0.1% | Short-lived status messages |
| `app.bsky.labeler.service` | 31,966 | <0.1% | Labeler service declarations |
| `app.bsky.feed.generator` | 9,257 | <0.1% | Custom feed generator configs |
| `app.bsky.graph.list` | 8,678 | <0.1% | User-created lists |
| `app.bsky.graph.listblock` | 6,631 | <0.1% | Blocking of entire lists |
| `app.bsky.notification.declaration` | 6,114 | <0.1% | Notification preference declarations |
| `app.bsky.graph.starterpack` | 2,023 | <0.1% | Starter pack definitions |
| Others | 308 | <0.1% | Rare types: `graph.repost`, `verification`, `lexicon.collection`, etc. |

### Data types in detail

#### 1. Social interactions (feed records)

- **`app.bsky.feed.like`** — A user liking a post. References the target post via `subject_uri` and `subject_cid`. The `record_json` contains `{"$type": "app.bsky.feed.like", "subject": {"uri": "...", "cid": "..."}}`.

- **`app.bsky.feed.repost`** — A user reposting another post (Bluesky's equivalent of a retweet). Same structure as likes, referencing the target post.

#### 2. Social graph (follows, blocks)

- **`app.bsky.graph.follow`** — A user following another user. The target user's DID is stored in `subject_did` (not `subject_uri`, unlike posts). The `record_json` contains `{"$type": "app.bsky.graph.follow", "subject": "did:plc:..."}`.

- **`app.bsky.graph.block`** — A user blocking another user. Same structure as follows. Deletion of a block record represents an unblock.

#### 3. Profile & identity

- **`app.bsky.actor.profile`** — A user's profile metadata: display name, bio, avatar, banner. Uses the record key `self` (only one profile per user). The `record_json` contains fields like `displayName`, `description`, `avatar`, `banner`.

- **`app.bsky.actor.status`** — Ephemeral short-lived status messages tied to a user's presence.

#### 4. Content moderation & gating

- **`app.bsky.feed.threadgate`** — Controls who can reply to a specific post. Rules include `followingRule` (only followers), `mentionRule` (only mentioned users), `listRule` (members of a specific list). The JSON references the target post.

- **`app.bsky.feed.postgate`** — Controls embedding and quoting of a post. Rules like `disableRule` prevent quote-posting; `detachedEmbeddingUris` can detach embeds.

#### 5. Lists & curation

- **`app.bsky.graph.list`** — A user-created list with a name, description, and purpose (e.g., `app.bsky.graph.defs#curatelist` for curation, `app.bsky.graph.defs#modlist` for moderation).

- **`app.bsky.graph.listitem`** — An individual entry (user) in a list, linking a list to a subject DID.

- **`app.bsky.graph.listblock`** — Blocking an entire list and all its members.

#### 6. Discovery & feeds

- **`app.bsky.feed.generator`** — Configuration for a custom algorithmic feed (feed generator), including description, avatar, and the feed's endpoint.

- **`app.bsky.graph.starterpack`** — A starter pack definition (a curated bundle of users and feeds for onboarding new users).

- **`app.bsky.labeler.service`** — A labeler service declaration (third-party moderation/labeling services).

#### 7. Other / rare

- **`app.bsky.graph.repost`** — An older/deprecated version of the repost record (only 186 records).
- **`app.bsky.graph.verification`** — Verification-related records (119 records).
- **`app.bsky.graph.cancellation`** — A single cancellation record.
- **`app.bsky.draft.createDraft`** — A single draft creation record.
- **`app.bsky.notification.declaration`** — Declares notification preferences (6,114 records).
- **`app.bsky.lexicon.collection`** — Custom lexicon collection definitions (2 records).

---

## Relationship between `posts` and `records`

The `posts` table is a **filtered and normalized subset** of the `records` table. Specifically:

- `records` contains **all** `app.bsky.feed.post` events mixed in with all other collections.
- The `posts` table extracts only the `app.bsky.feed.post` records and normalizes them: it unpacks the `record_json` to surface the `post_text`, `lang`, `reply_root_uri`, `reply_parent_uri`, etc. as dedicated columns.

To join the two tables, match `records.record_json` post records with `posts` using the combination of `did`, `rkey`, and `created_at`.

---

## Note on timestamps

Some records have anomalous timestamps (year 0001, year 1000, year 2826). These are likely placeholder or corrupted values from the firehose. The majority of real data falls within the 2026–2028 range.

---

*Document generated on 2026-05-15*

---

# `pau_db` — Derived / results database

**Database:** `pau_db`  
**Host:** `10.18.74.14:9030`  
**User:** `pau` (read-write on his own tables)

This database holds results computed from the `bsky` firehose, including
pre-aggregated per-user summaries and topology crawl results.

---

## Table: `users`

A **per-user summary table** aggregating basic activity statistics for every
unique DID that appears anywhere in the firehose (`bsky.posts` or `bsky.records`).
This is intended as a lightweight "user profile at a glance" — join it against
session data or the follow graph to enrich analyses without re-scanning the
raw firehose tables.

**Computed on:** 2026-05-16  
**Populated by:** `topology-crawl/sql-scripts/create_users_table.sql`  

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(128) | User's decentralized identifier (primary key) |
| `num_posts` | bigint | Total posts authored (from `bsky.posts`) |
| `num_likes` | bigint | Total likes given (`app.bsky.feed.like` records) |
| `num_reposts` | bigint | Total reposts given (`app.bsky.feed.repost` records) |
| `num_follows` | bigint | Total follow actions performed — **outbound**, i.e. how many people this user follows (from `app.bsky.graph.follow` records). This is **not** the user's follower count (inbound), which requires the follow-graph crawl. |
| `first_seen_us` | bigint | Earliest activity timestamp for this user (microseconds since epoch), across all record types including non-core collections (blocks, profiles, lists, etc.) |
| `last_seen_us` | bigint | Latest activity timestamp (likewise spanning all collections) |
| `primary_lang` | varchar(16) | Most frequent language tag on the user's posts, or NULL if the user has no posts or all their posts lack a language tag |
| `created_at` | datetime | When this row was inserted |

### Key statistics

- **Total users:** 3,086,991
- **Users with posts (≥1):** ~1,450,000 (same as the distinct authors in `bsky.posts`)
- **Users with at least one like / repost / follow / post:** 3,045,772
- **Users with *only* non-core activity (blocks, profiles, lists, etc. — all four count columns are zero):** 41,219. These users appear in `bsky.records` but exclusively in collections like `app.bsky.graph.block`, `app.bsky.actor.profile`, `app.bsky.graph.list`, etc. They still have valid `first_seen_us` / `last_seen_us`.

### Column distributions

| Column | Min | Max | Notes |
|--------|-----|-----|-------|
| `num_posts` | 0 | 229,860 | 0 for users who have never authored a post |
| `num_likes` | 0 | 70,701 | 0 for users who have never liked anything |
| `num_reposts` | 0 | 42,438 | 0 for users who have never reposted |
| `num_follows` | 0 | 123,670 | 0 for users who have never followed anyone |

### Language coverage

Of the 3,086,991 users, **1,679,815 (54.4%) have no language tag**
(`primary_lang IS NULL`). This is expected and **not a data error**:

- Only ~1.45M users have authored any posts at all.
- Many of those posts carry a `NULL` language tag (Bluesky clients do not
always populate it).
- Users who only like, repost, follow, or block will never have a language tag
because they have never posted.

**Top 10 primary languages among users who have one:**

| Language | Users | % of tagged |
|----------|-------|-------------|
| `en` | 988,046 | 70.2% |
| `ja` | 181,031 | 12.9% |
| `es` | 44,143 | 3.1% |
| `de` | 40,546 | 2.9% |
| `pt` | 34,146 | 2.4% |
| `fr` | 29,780 | 2.1% |
| `ko` | 20,713 | 1.5% |
| `nl` | 11,230 | 0.8% |
| `zh` | 8,319 | 0.6% |
| `tr` | 7,221 | 0.5% |

### Understanding zero values — which users are which?

The four count columns (`num_posts`, `num_likes`, `num_reposts`, `num_follows`)
are independent aggregates. It is normal and correct for any subset of them
to be zero. For example:

| Profile | `num_posts` | `num_likes` | `num_reposts` | `num_follows` | Meaning |
|---------|-------------|-------------|---------------|---------------|---------|
| **Pure creator** | high | 0 | 0 | 0 | Posts but never engages with others |
| **Pure consumer / lurker** | 0 | high | some | some | Never posts, only likes and reposts |
| **Broadcaster** | high | low | low | some | Posts a lot, little interaction |
| **Connector** | 0 | 0 | 0 | >0 | Only follows people, no other visible activity |
| **Minimal profile** | 0 | 0 | 0 | 0 | Only has non-core records (blocks, profile updates, list memberships, etc.) |

41,219 users fall into the **Minimal profile** bucket. They are real users who
happened to only trigger non-core record types in the firehose snapshot.

### Relationship to `bsky.posts` and `bsky.records`

- `users.did` is the union of `SELECT DISTINCT did` from both `bsky.posts` and `bsky.records`.
- `num_posts` = `COUNT(*)` from `bsky.posts GROUP BY did`.
- `num_likes`, `num_reposts`, `num_follows` = conditional `COUNT()` from
  `bsky.records GROUP BY did` (filtered by collection).
- `first_seen_us` / `last_seen_us` span **all** record collections (including
  blocks, profiles, lists, etc.), so even users with all four counts at zero
  still have valid temporal bounds.
- `primary_lang` is the modal `lang` in `bsky.posts` (users without posts or
  without any language-tagged posts get `NULL`).

### Usage

```sql
-- Join with bsky tables to filter analyses
SELECT p.*
FROM bsky.posts p
JOIN pau_db.users u ON p.did = u.did
WHERE u.primary_lang = 'en'
  AND u.num_posts > 100;

-- Find heavy consumers who never post
SELECT did, num_likes, num_reposts, num_follows
FROM pau_db.users
WHERE num_posts = 0 AND num_likes > 1000
ORDER BY num_likes DESC;

-- Activity window per user
SELECT did,
       (last_seen_us - first_seen_us) / 86400000000 AS active_days
FROM pau_db.users
WHERE first_seen_us > 0;
```

### Regeneration

The table is populated by a single `INSERT INTO … SELECT` in
`topology-crawl/sql-scripts/create_users_table.sql`. If you need to refresh it,
a DBA must first drop the table (the `pau` user cannot `DROP`), then re-run the
script.

---

*Users table populated 2026-05-16*

---

## Table: `user_core_events` (and filtered variants)

Pre-filtered tables containing only **core engagement events** for
session-based analysis, following the Twitter session-study methodology
(Kooti et al., SocInfo 2016). Three variants exist for different purposes:

### Base table: `user_core_events`

Contains **posts**, **replies**, and **reposts** for every user in the
firehose — no likes (passive, excluded per study methodology), no follows.

**Populated by:** `session-analysis/sql-scripts/create_core_events_table.sql`
and `insert_core_events.sql`

#### Schema (shared by all three variants)

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(128) | User identifier |
| `time_us` | bigint | Event timestamp (microseconds since epoch) |
| `event_type` | varchar(16) | `'post'`, `'reply'`, or `'repost'` |

Engine: OLAP, `DUPLICATE KEY(did, time_us)`, 32 buckets.

#### Size

| Variant | Table | Rows | Users | Filter |
|---------|-------|------|-------|--------|
| All users | `user_core_events` | 53,462,265 | 1,750,802 | none |
| Human range | `user_core_events_human` | 37,126,795 | 815,271 | 6–500 events |
| Dominant stratum | `user_core_events_dominant` | 19,425,737 | 95,795 | 101–500 events |

### Filtered variant 1: `user_core_events_human` (6–500 events)

**Purpose:** Per-user adaptive (IQR/Tukey) session clustering.

Removes two populations that break session analysis:
- **≤5 events** — tourists (52.7% of users, 2.1% of gaps). Too few inter-arrival
gaps for per-user IQR to be meaningful.
- **501+ events** — heavy bots (>62 events/day, 0.8% of users, 27.7% of gaps).
Their artificially tight posting intervals distort aggregate statistics.

**Leaves three meaningful strata:** 6–25 casuals, 26–100 regulars, 101–500
power users. Together: 46.6% of users, 70.3% of gaps.

**Populated by:** `session-analysis/sql-scripts/create_core_events_human.sql`
and `insert_core_events_human.sql`

### Filtered variant 2: `user_core_events_dominant` (101–500 events)

**Purpose:** Fixed-threshold elbow method (Kneedle algorithm).

Contains only the **dominant stratum** — the 5.5% of users who produce
37.4% of all inter-arrival gaps. Running the elbow on this subset gives
the threshold that actually matters (the one the dominant cohort exhibits),
without distortion from tourists or bots.

**Populated by:** `session-analysis/sql-scripts/create_core_events_dominant.sql`
and `insert_core_events_dominant.sql`

### Event-type composition (all variants)

| event_type | Rows (base table) | Description |
|------------|-------------------|-------------|
| `post` | 15,282,626 | Top-level posts (original content, incl. quote-posts) |
| `reply` | 12,791,049 | Posts with a reply parent (conversation engagement) |
| `repost` | 25,388,590 | Reposts / retweets (content amplification) |

### Relationship to `bsky.posts` and `bsky.records`

All three tables are derived from the base `user_core_events`, which itself
is a filtered `UNION ALL` of:
- `bsky.posts WHERE reply_root_uri IS NULL` → `'post'`
- `bsky.posts WHERE reply_root_uri IS NOT NULL` → `'reply'`
- `bsky.records WHERE collection = 'app.bsky.feed.repost' AND operation = 'create'` → `'repost'`

### Rationale (from EDA)

The event-count distribution follows a power-law (α = 1.68, xmin = 5).
Coverage analysis shows:

| Events per user | % Users | % Gaps | Role |
|-----------------|---------|--------|------|
| 1 | 22.6% | 0.0% | Irrelevant (no gaps) |
| 2–5 | 30.1% | 2.1% | Negligible |
| 6–25 | 27.7% | 10.6% | Meaningful |
| 26–100 | 13.4% | 22.3% | Meaningful |
| **101–500** | **5.5%** | **37.4%** | **DOMINANT — drives the elbow** |
| 501+ | 0.8% | 27.7% | Bot-heavy (downward bias) |

See `session-analysis/eda/README.md` for the full EDA.

---

## Table: `followers_from_data`

Contains **follow edges extracted from the firehose** — no API calls needed.
Every `app.bsky.graph.follow` record in `bsky.records` where both the follower
and followee are in `pau_db.users` becomes a row here.

**Populated by:** `topology-crawl/sql-scripts/insert_firehose_follows.sql`

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `follower_did` | varchar(128) | DID of the user who followed |
| `followee_did` | varchar(128) | DID of the user being followed |
| `crawled_at` | datetime | When this row was inserted |

### Key statistics

- **13.7 million distinct edges**
- **1.35 million distinct followers**
- **1.47 million distinct followees**
- Covers follows that happened during the firehose capture window (~April 2026)

---

## Table: `crawled_followers`

Follow edges discovered by the **Bluesky API crawler** (`crawl_followers.py`).
These are the *current* follower relationships returned by
`app.bsky.graph.getFollowers` — complementary to `followers_from_data` (which
is a historical snapshot from the firehose).

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `follower_did` | varchar(128) | DID of the follower |
| `followee_did` | varchar(128) | DID being followed |
| `crawled_at` | datetime | When this edge was discovered via API |

---

## Table: `crawl_state`

Tracks which users have been crawled (by either the firehose extraction or the
API crawler).  A row here means "this user's followers have been fetched" —
even if the result was zero followers or an unreachable account.

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(128) PK | User that was crawled |
| `crawled_at` | datetime | When the crawl completed |
| `follower_count` | bigint | How many followers were found (0 = none or unreachable) |

---

## Crawling strategy: proportional batch sampling

### Problem

Crawling 3 million users sequentially takes weeks.  If you crawl the biggest
users first, the partial graph is lopsided — all hubs, no leaves — and doesn't
behave like a real social network until it's nearly complete.

### Solution

**Each batch of N users mirrors the follower-count distribution of the full
population.**  The distribution is computed once from `followers_from_data`
(which already follows a power law) and held fixed.  Every batch contains the
same proportions: ~52% users with 0 firehose followers, ~16% with 1, ~9% with
4–7, tapering down to 1–2 users from the 8192+ bucket.

At any point — after 1 batch, 10 batches, or 300 — the crawled subgraph has
the correct power-law shape.  It's a valid social network; it's just smaller.

### Algorithm

1. **Pre-compute proportions** — bucket all 3M active users by follower count
   (log-scale bins: 0, 1, 2–3, 4–7, …, 8192+).  Count how many users are in
   each bucket.

2. **Fixed allocation** — for a batch of size N, allocate slots proportionally:
   each bucket gets `ceil(N × bucket_size / total_users)` slots, with a floor
   of 1 for any non-empty bucket.

3. **Pick users** — for each bucket, `SELECT … ORDER BY RAND() LIMIT n` from
   the uncrawled pool (`LEFT JOIN crawl_state … WHERE cs.did IS NULL`).

4. **Crawl** — call `app.bsky.graph.getFollowers` for each picked user, insert
   edges into `crawled_followers`, mark user in `crawl_state`.

5. **Repeat** — go to step 3 until no users remain.  Proportions never change;
   sparse buckets that run dry are topped up from the general remaining pool.

### Batch composition (N = 10,000)

| Follower bucket | Users in batch | % of batch | % of population |
|-----------------|---------------|------------|-----------------|
| 8192+ | 2 | 0.02% | 0.00% |
| 4096–8191 | 4 | 0.04% | 0.00% |
| 2048–4095 | 3 | 0.03% | 0.00% |
| 1024–2047 | 1 | 0.01% | 0.02% |
| 512–1023 | 3 | 0.03% | 0.04% |
| 256–511 | 10 | 0.10% | 0.11% |
| 128–255 | 27 | 0.27% | 0.28% |
| 64–127 | 55 | 0.55% | 0.56% |
| 32–63 | 120 | 1.20% | 1.21% |
| 16–31 | 261 | 2.61% | 2.61% |
| 8–15 | 509 | 5.09% | 5.10% |
| 4–7 | 861 | 8.61% | 8.62% |
| 2–3 | 1,290 | 12.90% | 12.91% |
| 1 | 1,654 | 16.54% | 16.55% |
| 0 | 5,200 | 52.00% | 52.01% |

### Why this works

Social networks follow a power-law degree distribution (γ ≈ 2–3).  A tiny
fraction of users have massive follower counts; the vast majority have few or
none.  By sampling proportionally, each batch preserves this structure
regardless of where you stop — you never end up with an all-hubs or all-leaves
partial graph.

### Script

`topology-crawl/crawl_followers.py` — see `topology-crawl/MONITORING.md` for
operational commands.

---

*Crawling strategy documented 2026-05-16*

---

## Table: `sessions_threshold`

Session clustering results using a **fixed global threshold** (elbow method)
applied to the **dominant user stratum** (101–500 events). Two events ≤ Δt
apart belong to the same session; a gap > Δt starts a new one.

**Threshold:** Δt = **265 s (4.4 min)** — determined by the Kneedle algorithm
on the inter-arrival gap histogram of `user_core_events_dominant`.

**Source table:** `pau_db.user_core_events_dominant` (101–500 events, 95,795 users).

**Populated by:** `session-analysis/session_core_events.py`  
**Populated on:** 2026-05-17

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(128) | User identifier |
| `session_start` | bigint | Session start timestamp (microseconds) |
| `session_end` | bigint | Session end timestamp (microseconds) |
| `next_session_start` | bigint | Start of the next session, or NULL for the last one |
| `duration_s` | double | Session duration in seconds |
| `reposts` | int | Reposts made during session |
| `posts_authored` | int | Posts + replies created during session |
| `threshold_s` | double | Fixed gap threshold used (265.0) |

Engine: OLAP, `DUPLICATE KEY(did, session_start)`, 32 buckets, distributed by `HASH(did)`.

### Key statistics

| Metric | Value |
|--------|-------|
| Users | 95,795 |
| Sessions | 8,470,675 |
| Avg sessions per user | 88.4 |
| Avg session duration | 90 s |
| Median session duration | 0 s |
| P75 session duration | 104 s |
| P90 session duration | 277 s |
| Max session duration | 33,238 s (~9.2 h) |
| Total reposts | 9,758,802 |
| Total posts authored | 9,666,935 |
| Total likes | 0 |

### Relationship to the EDA & elbow

1. The [EDA](../session-analysis/eda/README.md) (§5) identified the **101–500
event bucket** as the dominant gap contributor (37.4% of all inter-arrival gaps).
2. `user_core_events_dominant` was created to isolate this stratum.
3. The [elbow script](../session-analysis/session_threshold_elbow.py) ran the
Kneedle algorithm on the dominant table and produced Δt = 265 s.
4. This script clustered all 95,795 users' events into 8.47M sessions.

### Example queries

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
    ELSE '>30 min'
  END AS bucket,
  COUNT(*) AS sessions
FROM pau_db.sessions_threshold
GROUP BY bucket
ORDER BY MIN(duration_s);

-- Inter-session gaps (via next_session_start)
SELECT
  ROUND(AVG(next_session_start - session_end) / 1e6, 0) AS avg_gap_s,
  ROUND(PERCENTILE(next_session_start - session_end, 0.5) / 1e6, 0) AS median_gap_s
FROM pau_db.sessions_threshold
WHERE next_session_start IS NOT NULL;

-- Top posters
SELECT did,
       COUNT(*) AS n_sessions,
       SUM(posts_authored) AS total_posts,
       ROUND(AVG(posts_authored), 1) AS avg_posts_per_session
FROM pau_db.sessions_threshold
GROUP BY did
ORDER BY total_posts DESC
LIMIT 20;
```

### Regeneration

Truncate and re-run:
```bash
mysql -h 10.18.74.14 -P 9030 -u pau -p -e "TRUNCATE TABLE pau_db.sessions_threshold;"

uv run session-analysis/session_core_events.py \
  --source-table pau_db.user_core_events_dominant \
  --threshold 265 \
  --min-events 1 --max-events 999999 \
  --summary
```

See [`docs/twitter-session-analytics-replication.md`](twitter-session-analytics-replication.md) §5
for the full pipeline documentation.

---

## Table: `sessions_tukey`

Session clustering results using **per-user adaptive thresholds** (Tukey's
fences / IQR method). Unlike the fixed-threshold approach, each user gets
their own session boundary: Q3 + 1.5×IQR of their personal inter-arrival gap
distribution, with a 120-second floor and a configurable global fallback for
users with too few gaps.

Includes **likes** in addition to reposts and posts, making it a broader
engagement picture than `sessions_threshold` (which is core-events only).

**Source tables:** `bsky.records` (likes + reposts) and `bsky.posts` (posts authored).  
**Populated by:** `session-analysis/session_engagement_analysis.py`  
**Populated on:** 2026-05-16

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(128) | User identifier |
| `session_start` | bigint | Session start timestamp (microseconds) |
| `session_end` | bigint | Session end timestamp (microseconds) |
| `next_session_start` | bigint | Start of the next session, or NULL for the last one |
| `duration_s` | double | Session duration in seconds |
| `likes` | int | Likes given during session |
| `reposts` | int | Reposts made during session |
| `posts_authored` | int | Posts created during session |
| `interactions` | int | `likes + reposts` (convenience column) |
| `user_threshold_s` | double | Per-user adaptive gap threshold (seconds) |
| `user_threshold_fallback` | tinyint | 1 if fallback threshold was used (user had < 4 gaps) |
| `user_gap_count` | int | Number of inter-event gaps for this user (events − 1) |

Engine: OLAP, `DUPLICATE KEY(did, session_start)`, 32 buckets, distributed by `HASH(did)`.

### Key statistics

| Metric | Median | Mean |
|--------|--------|------|
| Session duration | 100 s | 8,126 s |
| Likes per session | 3 | 6.0 |
| Reposts per session | 0 | 0.4 |
| Posts authored per session | 0 | 0.3 |
| Interactions (likes + reposts) | 3 | 6.4 |
| Users on fallback threshold | — | 24% |

Computed across 3,086,991 DIDs → 2,281,225 active users (≥2 actions) →
~29.3M sessions. Default parameters: IQR multiplier = 1.5, fallback
threshold = 60 min, avg view time = 5 s/post.

### Adaptive threshold formula

```
Q1 = 25th percentile of user's inter-arrival gaps
Q3 = 75th percentile of user's inter-arrival gaps
IQR = Q3 − Q1

session_boundary = max(Q3 + 1.5 × IQR, 120 s)   -- 2-min floor

If user has < 4 gaps:  session_boundary = fallback (default 60 min)
```

### Example: how thresholds vary per user

| User profile | Typical gaps | Q1 | Q3 | IQR | Threshold |
|-------------|-------------|----|----|-----|-----------|
| Bursty poster | 10–60 s | 15 s | 50 s | 35 s | 102 s |
| Casual browser | 30–300 s | 60 s | 200 s | 140 s | 410 s |
| Once-a-day checker | hours apart | — | — | — | fallback: 60 min |

### Relationship to `sessions_threshold`

| Aspect | `sessions_threshold` (fixed) | `sessions_tukey` (adaptive) |
|--------|------------------------------|----------------------------|
| Threshold | 265s for all users | Per-user: Q3 + 1.5×IQR |
| Source | `user_core_events_dominant` (101–500) | `bsky.records` + `bsky.posts` |
| Includes likes? | No | Yes |
| Users covered | 95,795 (dominant stratum) | 2,281,225 (all with ≥2 actions) |
| Sessions | 8.47M | ~29.3M |
| Best for | Clean, comparable session boundaries | Realistic per-user rhythms |

### Example queries

```sql
-- Users with the tightest adaptive thresholds (most bursty)
SELECT did, user_threshold_s, user_gap_count, user_threshold_fallback
FROM pau_db.sessions_tukey
WHERE user_threshold_s < 120
GROUP BY did, user_threshold_s, user_gap_count, user_threshold_fallback
ORDER BY user_threshold_s
LIMIT 20;

-- Sessions per user
SELECT did, COUNT(*) AS n_sessions,
       SUM(likes) AS total_likes,
       SUM(reposts) AS total_reposts,
       SUM(posts_authored) AS total_posts,
       ROUND(AVG(duration_s), 1) AS avg_session_s
FROM pau_db.sessions_tukey
GROUP BY did
ORDER BY total_likes DESC
LIMIT 20;

-- Engagement rate distribution
SELECT
  CASE
    WHEN rate = 0 THEN '0% (never interact)'
    WHEN rate < 0.01 THEN '<1%'
    WHEN rate < 0.05 THEN '1–5%'
    WHEN rate < 0.10 THEN '5–10%'
    WHEN rate < 0.20 THEN '10–20%'
    WHEN rate < 0.30 THEN '20–30%'
    WHEN rate < 0.50 THEN '30–50%'
    ELSE '50%+'
  END AS engagement_bucket,
  COUNT(*) AS sessions
FROM (
  SELECT CAST(interactions AS DOUBLE) / NULLIF(
    GREATEST(duration_s / 5.0, CAST(interactions + posts_authored + 4 AS DOUBLE)), 0
  ) AS rate
  FROM pau_db.sessions_tukey
) t
GROUP BY engagement_bucket
ORDER BY MIN(rate);

-- Fallback usage: how many users relied on the global fallback?
SELECT user_threshold_fallback, COUNT(DISTINCT did) AS users
FROM pau_db.sessions_tukey
GROUP BY user_threshold_fallback;
```

---

## Table: `user_time_entropy`

Per-user **Shannon entropy** of inter-arrival gap distributions. Used for
bot/automation detection: low entropy → highly regular posting intervals
(suggesting automation), high entropy → irregular/varied intervals
(suggesting organic human behaviour).

**Formula (Kooti et al., SocInfo 2016):**
```
p(Δt_i) = n_{Δt_i} / N                    -- probability of gap value i
H_Δt    = −Σ p(Δt_i) · log₂(p(Δt_i))      -- Shannon entropy (bits)
```
Gaps are rounded to the nearest second so repeated patterns collapse into
a single symbol.

**Source table:** `pau_db.user_core_events` (all 1,750,802 users).  
**Populated by:** `session-analysis/user_time_entropy.py`  
**Populated on:** 2026-05-16

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `did` | varchar(128) | User identifier |
| `entropy_bits` | double | Shannon entropy of gap distribution (bits) |
| `num_gaps` | int | Number of inter-event gaps (events − 1) |
| `num_unique_gaps` | int | Number of distinct gap values (rounded to 1 s) |
| `is_automated` | tinyint | 0 (not set; filter by `entropy_bits < threshold`) |

Engine: OLAP, `DUPLICATE KEY(did)`, 32 buckets.

### Key statistics

| Statistic | Value |
|-----------|-------|
| Users with ≥5 gaps | ~1.3M |
| Near-zero entropy users | ~500 (0.06%) |
| Automated threshold (Kneedle) | ~1.0 bits |

Only ~500 users (0.06%) have near-zero entropy — these are unambiguous bots
with perfectly regular posting schedules. The entropy method is a principled
bot-detection approach, though the simpler events/day ceiling (>100/day)
captures the same population operationally.

### Example queries

```sql
-- Most regular users (likely automated)
SELECT did, entropy_bits, num_gaps, num_unique_gaps
FROM pau_db.user_time_entropy
ORDER BY entropy_bits
LIMIT 20;

-- Most irregular users (likely human)
SELECT did, entropy_bits, num_gaps, num_unique_gaps
FROM pau_db.user_time_entropy
ORDER BY entropy_bits DESC
LIMIT 20;
```

---

*Session tables populated 2026-05-16 / 2026-05-17*
