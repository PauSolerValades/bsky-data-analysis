# EDA — Bluesky Firehose

**Date:** 2026-05-23  
**Data:** `bsky.records` (212.5M rows) + `bsky.posts` (28.1M rows)  
**Script:** `EDA/run.py` — reads only from the two base tables, no pre-filtered inputs.  
**Window:** 2026-04-11 → 2026-04-18 (8 days)

---

## Table of contents

1. [§1 — Event types](#1--event-types)
2. [§2 — Temporal distribution](#2--temporal-distribution)
3. [§3 — User counts](#3--user-counts)
4. [§4 — Events per user](#4--events-per-user)
5. [§5 — Events per day per user](#5--events-per-day-per-user)
6. [§6 — Events per hour per user](#6--events-per-hour-per-user)
7. [§7 — Ratio-based categorization](#7--ratio-based-categorization)

---

## §1 — Event types

### §1a — Collections × operation

**20 distinct collections** in `bsky.records`, totalling 212.5M records. Each
collection is split by operation (`create`, `delete`, `update`). The posts table
adds another 28.1M rows (not part of `bsky.records`).

| Collection | Total records | % | Creates | Deletes (% of total) |
|-----------|-------------|----|---------|---------------------|
| `feed.like` | 161,700,519 | 76.1% | 159,708,526 | 1,991,993 (1%) |
| `feed.repost` | 26,372,977 | 12.4% | 25,388,590 | 984,387 (4%) |
| `graph.follow` | 18,774,479 | 8.8% | 16,244,345 | 2,530,133 (13%) |
| `graph.block` | 1,740,380 | 0.8% | 1,635,802 | 104,578 (6%) |
| `feed.threadgate` | 1,472,652 | 0.7% | 1,463,636 | 8 (≈0%) |
| `actor.profile` | 841,651 | 0.4% | 171,313 | 14 (≈0%) |
| `feed.postgate` | 801,586 | 0.4% | 799,568 | 3 (≈0%) |
| `graph.listitem` | 451,747 | 0.2% | 372,642 | 79,105 (18%) |
| `actor.status` | 270,489 | 0.1% | 86,782 | 86,381 (32%) |
| Others (11 collections) | <33K each | <0.1% | — | — |

**Posts table** (separate from records):
- Total: 28,073,675
- Top-level (no reply parent): 15,282,626 (54.4%)
- Replies (has reply parent): 12,791,049 (45.6%)

**Key observations:**
- **Likes are 76% of all records.** The platform is overwhelmingly passive engagement.
- **graph.follow has 13% deletes** — unfollows are common enough to matter. Any table that counts follows must filter `operation = 'create'`.
- **actor.status has 32% deletes** — these are ephemeral status messages, frequently created and deleted.
- **No `app.bsky.feed.post` in records** — posts were extracted to their own normalized table.

### §1b — Major events (by user reach)

*Definition:* an event type is **major** if ≥1% of all users (≥30,870 users)
have at least one `create` operation of that type.

| Event type | Users | % of 3.09M | Events | Major? |
|-----------|------:|:----------:|------:|:------:|
| `feed.like` | 2,313,275 | 74.9% | 161.7M | ✓ |
| `feed.post` (all) | 1,455,391 | 47.1% | 28.1M | ✓ |
| `graph.follow` | 1,390,036 | 45.0% | 16.2M | ✓ |
| `feed.post` (top-level) | 1,170,746 | 37.9% | 15.3M | ✓ |
| `feed.repost` | 959,057 | 31.1% | 25.4M | ✓ |
| `feed.post` (reply) | 902,219 | 29.2% | 12.8M | ✓ |
| `graph.block` | 259,530 | 8.4% | 1.6M | ✓ |
| `actor.profile` | 171,303 | 5.5% | 0.2M | ✓ |
| `feed.threadgate` | 54,036 | 1.8% | 1.5M | ✓ |
| `feed.postgate` | 31,197 | 1.0% | 0.8M | ✓ |
| 13 minor types | <22K each | <1% | — | |

**10 major event types.** The rest (list items, starter packs, labeler services,
etc.) each reach <1% of users and can be treated as noise for most analyses.

**Three-quarters of users have liked something.** Only 47% have posted.
Liking is the universal activity; posting is a minority sport.

---

## §2 — Temporal distribution

```
╔══════════════════════════════════════════╗
║  DATA WINDOW                            ║
║  2026-04-11  →  2026-04-18              ║
║  8 days                                 ║
║  WARNING: all per-day / per-hour stats   ║
║  are bound to this 8-day snapshot.       ║
╚══════════════════════════════════════════╝
```

**8 days exactly** — April 11 through April 18, 2026. The day-level volume is
roughly flat across the week (no strong weekday/weekend pattern visible at this
resolution), with a slight dip on the last day (partial data).

**Per-hour distribution (UTC):** activity peaks between 14:00–22:00 UTC and
hits a trough around 06:00–10:00 UTC. Posts and records follow the same
circadian rhythm.

**Implication:** "Active days" (P50 = 3) means active on 3 out of 8 possible
days, not 3 out of 365. "Events per day" distributions are similarly compressed.
This is a snapshot, not a longitudinal study.

---

## §3 — User counts

| Source | Distinct DIDs |
|--------|:------------:|
| `bsky.records` only | 2,844,778 |
| `bsky.posts` only | 1,455,391 |
| **Either table** | **3,086,991** |
| In records but not posts | 1,631,600 |
| In posts but not records | 242,213 |

**3.09 million unique users.** Over half (1.63M) never authored a post — they
only appear in records (likes, reposts, follows, blocks, etc.). These are
lurkers, consumers, and connector-type users. They are real users with real
activity, invisible to any post-only analysis.

---

## §4 — Events per user

**The fundamental distribution.** Computed from the UNION ALL of both tables,
counting every event (all record types + all posts) per user.

| Percentile | Events |
|:----------:|:------:|
| P1 | 1 |
| P10 | 1 |
| P25 | 3 |
| **P50** | **8** |
| P75 | 39 |
| P90 | 159 |
| P95 | 341 |
| P99 | 1,185 |

**Half of all users have ≤8 events in 8 days.** This is a heavily right-skewed
power-law distribution. The top 1% (P99 = 1,185 events) produce 148× more than
the median user.

### Power-law fit

Using the Clauset-Shalizi-Newman method (MLE with KS-based xmin selection):

| Parameter | Value |
|-----------|-------|
| **α (exponent)** | **1.56** |
| **xmin (cutoff)** | **8 events** |
| Tail users | 1,601,920 (51.9%) |
| KS statistic | 0.084 |

The event-count distribution follows a power-law for users with **≥8 events**.
Below 8, the distribution includes tourists, one-time visitors, and users whose
entire presence in the firehose is a handful of actions. α = 1.56 means the
distribution is very heavy-tailed — finite mean, infinite variance.

**xmin = 8 is the principled cutoff.** Users with <8 events are in the "body"
regime; users with ≥8 events are in the power-law tail. This is used as the
filter for the `all_events` table.

### Engaged-events power-law fit

The same method applied to engaged events only (posts + replies + reposts +
follows + blocks — **no likes**):

| Parameter | Value |
|-----------|-------|
| **α (exponent)** | **1.67** |
| **xmin (cutoff)** | **4 events** |
| Users in tail | 1,281,959 (53.0%) |
| Total raw users | 2,420,803 |
| KS statistic | 0.105 |

**xmin = 4 is the filter for the `engaged_events` table.** The engaged-event
distribution has a lower xmin than the all-events distribution because engaged
events are rarer — a user with 4 posts/replies/reposts/follows/blocks in 8 days
is already in the power-law regime.

### Per-collection breakdown (creates only)

| Collection | Users | Median | P90 | P99 | Max |
|-----------|------:|:------:|:---:|:---:|:---:|
| `feed.like` | 2,313,275 | 8 | 151 | 1,008 | 70,092 |
| `feed.repost` | 959,057 | 4 | 50 | 403 | 42,410 |
| `graph.follow` | 1,390,036 | 2 | 12 | 142 | 62,913 |
| `graph.block` | 259,530 | 1 | 9 | 68 | 28,865 |
| `actor.profile` | 171,303 | 1 | 1 | 1 | 4 |

**Posts table** (all posts, including replies):

| Percentile | Posts |
|:----------:|:-----:|
| P50 | 4 |
| P75 | 12 |
| P90 | 38 |
| P99 | 236 |

**Likes dominate in both reach and volume.** The median liking user likes 8
posts; the median posting user posts 4. At P99: 1,008 likes vs 236 posts.
The platform generates 14× more likes than posts at the median, and 4× more
at P99. Every downstream analysis that mixes likes and posts without
normalization is being driven by likes.

### ⚠️ Known gaps in the database dump

This EDA reads from `bsky.records` and `bsky.posts` — a **processed dump**
of the raw firehose. The dump has gaps that limit what event types we can
distinguish:

**1. No embed information → quotes are invisible.**

The `bsky.posts` table assumed text-only posts and did not extract the
`record.embed` field. A spot-check of the raw firehose shows ~5.6% of posts
are quote posts (`embed.$type` = `app.bsky.embed.record` or `recordWithMedia`).
These are misclassified as plain `post` or `reply`. This was an error in the
dump processing — quote posts are text content and should have been preserved.

**2. Non-Bluesky collections were purposely discarded.**

The raw firehose contains records from other AT Protocol applications
(`site.standard.document`, `at.podping.records.podping`, `social.grain.favorite`,
`community.blacksky.assembly.vote`, etc.). These were intentionally excluded
from the database dump to focus on Bluesky-specific activity.

**3. Reposts have `via` information.** ~29% of reposts in the raw firehose
carry a `record.via` field linking to the parent repost. This is preserved
in `bsky.records.record_json` and used by the structural virality analysis,
but not surfaced in the core event tables.

These are limitations of the database dump pipeline, not of this EDA.

---

## §5 — Events per day per user

Events ÷ active days (where active day = any day with ≥1 event from either table).

| Percentile | Events/day | Active days |
|:----------:|:----------:|:-----------:|
| P10 | 1.0 | 1 |
| P25 | 1.5 | 1 |
| **P50** | **3.0** | **3** |
| P75 | 8.8 | 6 |
| P90 | 26.8 | 8 |
| P99 | 167.9 | 8 |

**The median user does 3 events per active day, and is active on 3 of 8 days.**
At P90: 27 events/day across all 8 days. At P99: 168 events/day — these are
power users or bots (>20 events per waking hour).

**51.7% of users are active on ≤2 days.** These are transient visitors or
single-session users whose "inter-day gaps" are meaningless. At the other end,
20% of users are active on 7–8 days — consistent daily users.

---

## §6 — Events per hour per user

Events ÷ active hours (active hour = any distinct year-month-day-hour with ≥1 event).

| Percentile | Events/hour | Active hours |
|:----------:|:-----------:|:------------:|
| P25 | 1.1 | — |
| **P50** | **2.0** | — |
| P75 | 4.0 | — |
| P90 | 8.9 | — |
| P99 | 39.5 | — |

**The median user does 2 events per active hour.** The distribution is tight:
even at P90, users do only 9 events/hour. This means most browsing sessions
involve very few actions per hour — a few likes, maybe a repost or a reply.
Bursts of rapid activity (>40 events/hour, P99) are rare and likely represent
automated or power-user behavior.

---

## §7 — Ratio-based categorization

For each user, four activity ratios are computed from the merged records+posts:

| Ratio | Definition | % Zero | Median | P75 |
|-------|-----------|:------:|:------:|:---:|
| **Create** | (posts + replies) / total | 52.9% | 0.000 | 0.235 |
| **Engage** | likes / total | 25.0% | 0.610 | 0.935 |
| **Amplify** | reposts / total | 68.8% | 0.000 | 0.040 |
| **Connect** | follows / total | 53.1% | 0.000 | 0.151 |

**Dominant activity** (which ratio is highest per user):

| Dominant type | Users | % |
|--------------|------:|:--:|
| **Engage** (like-heavy) | 1,915,878 | **62.1%** |
| Create (post-heavy) | 610,787 | 19.8% |
| Connect (follow-heavy) | 487,231 | 15.8% |
| Amplify (repost-heavy) | 73,095 | 2.4% |

**62% of users are like-dominant.** Their primary activity on Bluesky is
passive engagement — scrolling and liking. Only 20% are creators who primarily
post or reply. Repost-dominant users are rare (2.4%) — amplification is a
secondary behavior for nearly everyone.

**More than half of users have zero create ratio** (52.9%) — they never post
or reply. 68.8% have zero amplify ratio — they never repost. But only 25% have
zero engage ratio — three-quarters of all users have liked at least one post.

These ratios are **descriptive, not prescriptive.** They tell you what users
actually do, not what archetype someone predetermined they should belong to.
The labels "engage / create / connect / amplify" come directly from the data.
