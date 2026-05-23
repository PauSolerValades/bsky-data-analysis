# All vs Engagement — two session tables, two different questions

Bluesky sessions are clustered in two ways. They are **not competitors** — they
answer two different questions about the same per-user adaptive (Tukey IQR) method.

> **Deprecated:** `pau_db.sessions_tukey` and `pau_db.sessions_threshold` were
> built from a buggy `user_core_events` intermediate table (incorrect filtering
> from `bsky.records` + `bsky.posts`). They are superseded by `sessions_all`
> and `sessions_engagement`, which read directly from the correct
> `all_events` / `engaged_events` source tables.

---

## The two tables at a glance

| | sessions_all | sessions_engagement |
|---|---|---|
| **Table** | `pau_db.sessions_all` | `pau_db.sessions_engagement` |
| **Question it answers** | "When is the user browsing / scrolling?" | "When is the user actively creating or curating content?" |
| **Events used** | All 6 major types (like, repost, follow, block, post, reply) | 5 engaged types (repost, follow, block, post, reply) — **no likes** |
| **Source table** | `pau_db.all_events` (≥8 events per user, α=1.56 xmin) | `pau_db.engaged_events` (≥4 events per user, α=1.67 xmin) |
| **Method** | Per-user adaptive Tukey IQR | Per-user adaptive Tukey IQR |
| **Threshold** | Q3 + 1.5×IQR per user (120 s floor, 60 min fallback) | Q3 + 1.5×IQR per user (120 s floor, 60 min fallback) |
| **Sessions** | ~47.4M | ~19.6M |
| **Median duration** | 23 s | 290 s (4 min 50 s) |
| **Median gap** | 36.5 min | 3 h 15 min |
| **Zero-duration sessions** | 33.2% | 22.7% |
| **Best for** | Browsing rhythm, like-dominated behaviour, complete activity picture | Content rhythm, action-sparse behaviour, active curation |

---

## 1. sessions_all — the browsing picture

### What question does it answer?

> *When is the user scrolling and engaging with content?*

`sessions_all` includes **all event types** — likes, reposts, follows, blocks,
posts, and replies. This gives a complete picture of the user's browsing
behaviour because every action they take is a "page view" that signals they're
actively on the platform.

### Key characteristics

- **59% of sessions are pure likes** — the user does nothing but scroll and tap like.
- **Median duration is 23 s** — dominated by rapid-fire micro-bursts.
- **Median gap is 36.5 min** — the typical break between browsing sessions is
  about half an hour.
- **33% zero-duration sessions** — multiple actions co-occur at the same
  microsecond (e.g., several likes fired in the same request).
- **Gap vs duration: ρ = 0.04** — essentially no correlation. A long liking
  session doesn't predict a long gap afterward.

### When to use it

- Modelling complete user behaviour (browsing + creating)
- Understanding like-dominated rhythms
- Simulating "the typical Bluesky user" (most of what they do is like)
- Any analysis where likes are relevant evidence of active browsing

---

## 2. sessions_engagement — the content/curation picture

### What question does it answer?

> *When is the user actively producing content or curating their feed?*

`sessions_engagement` excludes likes, following the Kooti et al. (2016)
methodology that treats likes as passive/low-effort actions. It answers: when
is the user doing something that requires effort — posting, replying,
reposting, following, blocking?

### Key characteristics

- **25% 1-action sessions** — a single post, reply, repost, or follow with no
  other activity in the Tukey window.
- **Median duration is 4 min 50 s** — 12.7× longer than sessions_all.
- **Median gap is 3 h 15 min** — 5.3× longer than sessions_all.
- **23% zero-duration sessions** — fewer micro-bursts without likes.
- **Gap vs duration: ρ = 0.42** — moderate positive correlation. Longer
  content-creation sessions tend to be followed by longer breaks ("session
  fatigue").
- **Per-user thresholds are much higher** — median 9.3 h vs 2.4 min for
  sessions_all. Without likes, gaps are large and sparse.

### When to use it

- Content creation and curation studies
- Comparing to Twitter/X session studies (which also exclude likes)
- Models that need "effortful" sessions
- Sessions where likes would be noise rather than signal

---

## 3. Why they look different — and why that's expected

The two tables differ because they cluster **different event sets**. When you
include likes, events are denser (median gap 2.4 min), thresholds are tighter,
and sessions are shorter. When you exclude likes, events are sparser (median
gap 9.3 h), thresholds are wider, and sessions are longer.

This is by design, not oversight:

- **sessions_all** answers "when is the user browsing?" — likes are valid
  evidence of active browsing. Every like is an intentional scroll → read →
  click action.
- **sessions_engagement** answers "when is the user producing?" — following
  the content-creation / curation framing of the Twitter literature.

---

## 4. Practical guide

### Use sessions_all when:

- You want a complete picture of browsing behaviour
- Likes are relevant to your analysis (engagement rate, feed consumption)
- You need dense session timelines for per-user fitting
- You're simulating generic user behaviour

### Use sessions_engagement when:

- You're studying content creation or curation specifically
- You're comparing to Twitter/X session studies (Kooti et al.)
- You need the "effortful action" framing (posts, reposts, follows, blocks)
- Likes would be a confound in your model

### Use both when:

- You want complementary views of the same users
- You're studying the relationship between browsing and creating rhythms
- You need to calibrate simulations with both passive and active parameters

---

## 5. The old tables (deprecated)

`pau_db.sessions_tukey` and `pau_db.sessions_threshold` were built incorrectly
from `user_core_events`, which had a filtering error in the SQL that extracted
events from `bsky.records` and `bsky.posts`. The bug propagated through the
pipeline. The new tables (`sessions_all`, `sessions_engagement`) are the
authoritative replacements.

---

*Rewritten 2026-05-23. Supersedes the earlier "Tukey vs Threshold" framing.*
