# EDA — Session-Oriented Exploratory Data Analysis

Bluesky firehose data | 8-day window (April 2026) | 1,750,802 unique users

---

## What it does

The EDA investigates **session-related properties** of Bluesky user activity
*before* imposing any clustering or threshold. It answers:

- How many events do users generate, and what shape does that distribution have?
- What *kind* of user are they — creator, engager, curator, or tourist?
- Are they bingers or consistent? How spread out is their activity?
- What do the raw inter-arrival gaps look like per user?
- Whose gaps dominate the dataset — and whose are irrelevant?
- What should we filter, and how?

The EDA does **not** look at *when* activity happens (hour of day, day of week)
— it is purely about structural, session-relevant properties.

---

## Structure

```
session-analysis/
├── eda.py                          ← orchestrator
└── eda/
    ├── _common.py                   ← shared: DB connection, caching, helpers
    ├── powerlaw_binning.py          ← §1  Events-per-user + power-law fit
    ├── user_classification.py       ← §2  Event-type ratios → archetypes
    ├── activity_span.py             ← §3  Active days, density, binge vs consistent
    ├── gap_analysis.py              ← §4  Per-user median gap, IQR, skewness
    ├── coverage.py                  ← §5  Who contributes the gaps?
    ├── event_type_dist.py           ← §6  Distributions per event type
    ├── composite_score.py           ← §7  Density × Breadth × Consistency × Span
    ├── recommend.py                 ← §8  Filtering & threshold recommendations
    └── results/                     ← all output (plots + text)
```

Each section is a standalone script runnable with `uv run`. The orchestrator
runs them all in order and feeds results into §8 for the final recommendation.

---

## Sections & Results

### §1 — Events-per-user & power-law binning

*Script:* `eda/powerlaw_binning.py`  
*Output:* `01_events_per_user.png`, `01_events_per_active_day.png`, `01_summary.txt`

**Goal:** Understand the event-count distribution and find data-driven bin
boundaries instead of arbitrary 6–10, 11–25, 26–50 buckets.

**Method:** Log-spaced histograms + power-law tail fitting via MLE with
KS-based xmin selection (Clauset-Shalizi-Newman method, scipy).

**Results:**

| Percentile | Events |
|------------|--------|
| P1 | 1 |
| P5 | 1 |
| P10 | 1 |
| P25 | 2 |
| P50 | 5 |
| P75 | 18 |
| P90 | 61 |
| P95 | 124 |
| P99 | 424 |

**Power-law fit:** xmin = 5, α = 1.68, KS = 0.085, n_tail = 906,196 users

The tail starts at **xmin = 5**, validating the established "≥6 events"
tourist-removal cutoff. Half of all users (P50 = 5) do ≤5 events in 8 days.
This is a heavily right-skewed, power-law distribution — typical for social
media.

The events-per-active-day histogram confirms that the density metric is
more honest than raw event count. A user with 50 events across 1 day is
fundamentally different from one with 50 events across 8 days.

---

### §2 — User classification / archetypes

*Script:* `eda/user_classification.py`  
*Output:* `02_ratio_scatters.png`, `02_archetype_distribution.png`, `02_summary.txt`

**Goal:** Classify users by their event-type composition — are they creators,
engagers, curators, or tourists?

**Method:** Per-user counts of posts, replies, reposts, likes, and follows.
Ratio-based rules assign each user to an archetype.

**Results:**

| Archetype | Users | % | Description |
|-----------|-------|---|-------------|
| Tourist | 922,044 | 52.7% | ≤5 events total |
| Engager | 418,525 | 23.9% | Like-heavy, little content creation |
| Balanced | 148,994 | 8.5% | Mix of creation and engagement |
| Creator | 142,948 | 8.2% | Post/reply-heavy, little liking |
| Curator | 90,655 | 5.2% | Repost-heavy |
| Balanced-Curator | 27,636 | 1.6% | Balanced with repost tendency |

The hexbin scatter plots (likes vs posts, reposts vs posts) show that
**creators and engagers form distinct populations**, not a single continuum.
They likely have different session rhythms — creators may have tighter
intra-session gaps (burst posting), while engagers browse more passively
with wider gaps.

---

### §3 — Activity span & density

*Script:* `eda/activity_span.py`  
*Output:* `03_activity_span.png`, `03_summary.txt`

**Goal:** How spread out is user activity? Single-day bingers vs multi-day
consistent users. The span tells us whether inter-session gaps are meaningful.

**Results:**

| Active days | Users | % |
|-------------|-------|---|
| 1 day only | 570,645 | 32.6% |
| 2 days | 273,933 | 15.6% |
| 3+ days | 906,224 | 51.8% |
| 7–8 days | 352,560 | 20.1% |

- **Median active days:** 3
- **Median events/active day:** 2
- **874 high-activity 1-day bingers** (≥50 events in a single day) — these are likely bots or scheduled accounts. They produce 239,334 events.

A third of users appear on only one day — their inter-arrival "gaps"
are really intra-session bursts, not between-session pauses. For session
analysis, **active_days ≥ 2** should be required so that inter-session
gaps actually mean something.

---

### §4 — Per-user gap distributions

*Script:* `eda/gap_analysis.py`  
*Output:* `04_per_user_gaps.png`, `04_summary.txt`  
*Note:* Samples 50,000 users stratified by event-count bucket (heaviest section).

**Goal:** Before imposing *any* session threshold, understand what the raw
inter-arrival gaps look like per user. Do different user classes have
different rhythms?

**Results** (38,659 sampled users with ≥2 events):

| Percentile | Median gap (per user) |
|------------|----------------------|
| P1 | 1s |
| P5 | 25s |
| P10 | 63s (1.1 min) |
| P25 | 338s (5.6 min) |
| P50 | 6,791s (113.2 min) |
| P75 | 61,000s (1,017 min) |
| P90 | 158,331s (2,639 min) |
| P99 | 450,362s (7,506 min) |

- **9.6%** of users have median gap < 1 min
- **23.9%** have median gap < 5 min
- **30.0%** have median gap < 10 min

**Key insight:** The per-user median gaps span **six orders of magnitude**
(from seconds to days). The median of medians (113 min) is mostly driven
by low-volume users who have only a few events spread across many days.
High-volume users have much tighter medians.

The CDF of median gaps shows **systematic separation by event-count bucket**
— active users have consistently tighter rhythms than casual users. This
supports the case for **per-user adaptive thresholds** rather than a single
global one.

---

### §5 — Coverage: who contributes the gaps?

*Script:* `eda/coverage.py`  
*Output:* `05_coverage.png`, `05_summary.txt`

**Goal:** Quantify which users the session analysis is actually about.
If 80% of gaps come from 5% of users, the elbow/session boundary is
really that slice's threshold.

**Results:**

| Events per user | % Users | % Events | % Gaps | Role |
|-----------------|---------|----------|--------|------|
| 1 | 22.6% | 0.7% | 0.0% | Irrelevant (no gaps) |
| 2–5 | 30.1% | 3.0% | 2.1% | Negligible |
| 6–25 | 27.7% | 11.1% | 10.6% | Meaningful |
| 26–100 | 13.4% | 22.0% | 22.3% | Meaningful |
| **101–500** | **5.5%** | **36.3%** | **37.4%** | **DOMINANT** |
| 501+ | 0.8% | 26.8% | 27.7% | Meaningful (bot-heavy) |

**52.7% of users** (≤5 events) contribute **only 2.1% of gaps**. They are
invisible in any gap-based analysis.

The **101–500 bucket** (5.5% of users) supplies **37.4% of all gaps** —
this cohort dominates the elbow and the session threshold. Combined with
the 501+ bucket, **6.3% of users drive 65% of the analysis**.

---

### §6 — Event-type distributions

*Script:* `eda/event_type_dist.py`  
*Output:* `06_event_type_distributions.png`, `06_type_ratios.png`, `06_summary.txt`

**Goal:** Each event type (post, reply, repost, like, follow) has a different
distribution. Understanding these separately clarifies the creator/engager split.

**Results:**

| Event type | Non-zero users | Median | Mean | Max |
|------------|---------------|--------|------|-----|
| Posts | 1,170,746 | 3 | 13.1 | 229,860 |
| Replies | 902,219 | 3 | 14.2 | 69,258 |
| Reposts | 959,057 | 4 | 26.5 | 42,410 |
| Likes | 1,386,153 | 18 | 103.2 | 70,701 |
| Follows | 777,837 | 2 | 18.8 | 123,670 |

Likes dominate in both reach (1.39M users) and volume (median 18).
Posts and replies have similar shapes but reach fewer users. Follows
are the rarest core action.

The complementary CDF overlay shows that likes and reposts are
heavy-tailed but with different exponents — likes have a thicker
tail (more users with very high like counts than very high post counts).

---

### §7 — Composite engagement score

*Script:* `eda/composite_score.py`  
*Output:* `07_composite_score.png`, `07_summary.txt`

**Goal:** Combine multiple dimensions into a single scalar for ranking users
on a tourist → power-user spectrum.

**Components (equal weight):**

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| Density | 30% | Events per active day (normalized 0–1) |
| Breadth | 20% | Number of distinct event types used (0–5) |
| Consistency | 25% | active_days / 8 |
| Span | 25% | log(active_days) — proxy for "real user" longevity |

**Score distribution:**

| Percentile | Score | Tier |
|------------|-------|------|
| P1 | 0.15 | — |
| P10 | 0.19 | — |
| **P25** | **0.23** | **Tourist cutoff** |
| **P50** | **0.35** | **Casual → Active boundary** |
| P75 | 0.54 | — |
| **P90** | **0.67** | **Power user threshold** |
| P99 | 0.76 | — |

The score distribution is fairly smooth with no sharp natural breaks —
a continuous spectrum from tourist to power user, as expected in social
media. The P25 and P90 boundaries are reasonable tier cutoffs.

---

### §8 — Recommendations

*Script:* `eda/recommend.py`  
*Output:* `08_recommendation.txt`

Synthesizes all previous results into an actionable filtering & threshold
strategy for session analysis.

**Filtering strategy:**

1. **REQUIRED:** `total_events ≥ 6` — removes 52.7% of users (tourists, 2.1% of gaps)
2. **REQUIRED:** `active_days ≥ 2` — removes single-day bingers whose "gaps" aren't real inter-session pauses
3. **OPTIONAL:** `events_per_active_day ≤ 100` — removes 0.33% likely bots
4. **OPTIONAL:** `score ≥ 0.23` (P25) — removes the lowest-engagement quartile

**Threshold strategy:**

- **Single global:** use elbow-method result (~285s / 4.8 min for human-filtered data)
- **Per-user adaptive (recommended):** Tukey's IQR method (Q3 + 1.5×IQR), 2-min floor, 60-min fallback — adapts to each user's natural rhythm
- **Per-archetype:** creators (tighter gaps) vs engagers (wider gaps) may benefit from separate thresholds

---

## Usage

```bash
# Run all sections
uv run session-analysis/eda.py

# Skip the heavy gap analysis
uv run session-analysis/eda.py --skip 4

# Re-fetch from DB (bypass cache)
uv run session-analysis/eda.py --force

# Run individual sections
uv run session-analysis/eda/powerlaw_binning.py
uv run session-analysis/eda/gap_analysis.py --sample 30000
```

---

*EDA run 2026-05-17 | 1,750,802 users | 8 sections | ~54 seconds*
