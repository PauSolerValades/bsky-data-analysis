# Post Lifetime — EDA Results

**Data:** 15,282,626 top-level posts from the Bluesky firehose (April 2026 snapshot).  
**Tables:** `pau_db.post_lifetime` (aggregated) + `pau_db.post_engagement_events` (140M individual events).  
**Date:** 2026-05-17

---

## Table of contents

1. [Phase 1 — Power-law fit on engagement counts](#phase-1--power-law-fit-on-engagement-counts)
2. [Phase 2a — Power-law fit on post lifetimes](#phase-2a--power-law-fit-on-post-lifetimes)
3. [Phase 2b — Temporal decay of engagement per post](#phase-2b--temporal-decay-of-engagement-per-post)
4. [Phase 3 — Time-to-first-engagement](#phase-3--time-to-first-engagement)
5. [Phase 6 — Engagement cascade ordering](#phase-6--engagement-cascade-ordering)
6. [Simulation calibration summary](#simulation-calibration-summary)

---

## Phase 1 — Power-law fit on engagement counts

**Script:** `eda/fit_powerlaw_counts.py`  
**Question:** Do engagement counts (reposts, likes, replies) follow a power-law distribution?

### Data

15.3M top-level posts. Half get no engagement at all:

| Metric | Posts with >0 | % of total |
|--------|:------------:|:----------:|
| Likes | 6,980,893 | 45.7% |
| Replies | 2,668,201 | 17.5% |
| Reposts | 2,493,377 | 16.3% |
| Any engagement | 7,531,161 | 49.3% |
| None | 7,751,465 | 50.7% |

### Method

- **Discrete power-law MLE:** α = 1 + n / Σ ln(x_i / (x_min − 0.5))
- **x_min selection:** minimise Kolmogorov–Smirnov distance over candidate thresholds (80 log-spaced unique values)
- **Goodness-of-fit:** bootstrap p-value (50 iterations, sub-sampled to 20K when tail > 20K)
- **Model comparison:** Vuong log-likelihood ratio test vs lognormal, Weibull, exponential

### Results

| Engagement type | α | x_min | n_tail | % of engaged | KS | p-value |
|:----------------|------:|------:|-------:|:------------:|------:|:-------:|
| **Reposts** | 2.21 | 84 | 30,610 | 1.2% | 0.0145 | 1.00 ✓ |
| **Likes** | 2.15 | 127 | 112,443 | 1.6% | 0.0093 | 1.00 ✓ |
| **Replies** | 2.26 | 42 | 9,206 | 0.3% | 0.0273 | 1.00 ✓ |
| **Combined (any)** | 2.14 | 152 | 115,128 | 1.5% | 0.0087 | 1.00 ✓ |

### Model comparison (LLR test, all p < 0.001)

Power-law is **strongly favoured** over all alternatives for every engagement type:

| Type | vs Lognormal | vs Exponential | vs Weibull |
|:-----|:------------:|:--------------:|:----------:|
| Reposts | R = +11,139 | R = +19,902 | R = +19,887 |
| Likes | R = +45,612 | R = +82,274 | R = +79,860 |
| Replies | R = +2,933 | R = +5,308 | R = +5,170 |
| Combined | R = +47,802 | R = +85,822 | R = +83,040 |

R > 0 favours power-law. The magnitudes are enormous — lognormal is the closest competitor but still overwhelmingly rejected.

### Interpretation

- Engagement counts follow a **classic discrete power-law** in the tail, with α ≈ 2.1–2.3. This is right in the range of finite mean, infinite variance (α ∈ (2,3) means mean exists but variance does not). The distribution is extremely heavy-tailed.
- **x_min is high** (42–152). Below x_min, the distribution deviates from power-law — the body of the distribution (posts with 1–100 engagements) follows a different regime. Only ~1% of engaged posts are in the power-law tail.
- For simulation: sample engagement counts from a **discrete power-law with α ≈ 2.15 and x_min matching the engagement type**, or use a **piecewise** distribution: empirical below x_min, power-law above.
- **Reposts are the most selective** — only 16.3% of posts get any, and of those only 1.2% reach the power-law regime (≥84 reposts). This makes reposts the best signal for viral content.

### Plots

- `powerlaw_ccdf_reposts.png` — CCDF of repost counts with power-law fit
- `powerlaw_ccdf_likes.png` — CCDF of like counts
- `powerlaw_ccdf_replies.png` — CCDF of reply counts
- `powerlaw_ccdf_engagement.png` — CCDF of combined engagement
- `powerlaw_counts_compare.png` — Overlay of all three types

---

## Phase 2a — Power-law fit on post lifetimes

**Script:** `eda/fit_powerlaw_lifetimes.py`  
**Question:** What distribution best describes post lifetime (creation → last engagement)?

### Data

Lifetime = `last_engagement_us − created_at` (seconds). From `post_lifetime`.  
7,514,483 posts with lifetime > 0. Trimmed to [p0.1, p99.9] to remove timestamp anomalies (7,499,453 remaining).

### Method

- Fit 4 continuous distributions: **Pareto** (power-law), **lognormal**, **Weibull**, **exponential**
- Pareto: MLE with x_min selection via KS minimisation
- Others: MLE via `scipy.stats`
- Ranked by log-likelihood (higher = better fit)
- KS statistic also reported

### Results

| Rank | Distribution | Log-likelihood | KS stat | n | Parameters |
|:----:|:-------------|:--------------:|:-------:|:--:|:-----------|
| **1** | **Pareto** | −23,828,827 | 0.0681 | 1.88M | α = 2.16, x_min = 15.6h |
| 2 | Weibull | −84,718,567 | 0.0598 | 7.50M | shape = 0.53, scale = 9.4h |
| 3 | Lognormal | −85,342,781 | 0.0821 | 7.50M | σ = 2.61, μ = 9.00, scale = 2.3h |
| 4 | Exponential | −88,812,246 | 0.2754 | 7.50M | scale = 14.2h |

### Analysis

- **Pareto is the best fit** by log-likelihood, but only for the **tail** (lifetimes > 15.6 hours, ~1.88M posts or 25% of engaged posts).
- The **body** of the distribution (lifetimes < 15.6h, ~75% of engaged posts) is better fit by **Weibull** with shape = 0.53. A Weibull shape < 1 means the hazard rate decreases over time — posts are actually *less* likely to "die" the longer they've been alive, consistent with the rich-get-richer dynamic.
- The combined lifetime has **median 3.8h** but the Pareto tail extends far: p99 = 133h (5.6 days), p99.9 = 175h (7.3 days).
- The exponential is a terrible fit (KS = 0.28) — post lifetimes are emphatically **not** memoryless.

### Practical takeaway

For simulation, use a **two-component model**:
1. **Body** (0–15.6h): Weibull with shape ≈ 0.53, scale ≈ 9.4h
2. **Tail** (> 15.6h): Pareto with α ≈ 2.16

Or fit the full distribution with a composite model. The power-law tail is what generates the extreme outliers (posts alive for weeks) that are critical for viral dynamics.

### Plots

- `powerlaw_lifetimes_ccdf.png` — Combined lifetime CCDF with Pareto/Weibull/lognormal fit overlays; per-type CCDFs on the right panel

---

## Phase 2b — Temporal decay of engagement per post

**Script:** `eda/temporal_decay.py`  
**Question:** How fast does engagement decay within a post's lifetime? Do events arrive evenly or cluster early? And is it better to fit per-post then aggregate, or aggregate then fit?

### Method

Two complementary approaches:

**Aggregate-first:** Pool all event times from 3,000 random engaged posts, bin by log-time, fit cumulative N(t) ∝ t^β. One β for the whole dataset.

**Per-post-first:** Sample 100 posts from each of 4 engagement buckets (posts with 20–99, 100–999, 1K–10K, 10K+ total events). For each post, fetch its ordered event timeline, fit N(t) ∝ t^β individually. Show distribution of fitted β per bucket.

### Results: Aggregate approach

- 136,478 events pooled from 3,000 posts
- **β = 0.339 ± 0.030**
- This is strongly sub-linear (β ≪ 1). It means engagement is **highly concentrated early**: most events arrive in the first few minutes/hours, and the rate drops off quickly.

### Results: Per-post approach

| Bucket | n fits | Median β | Mean β | Std β |
|:-------|:------:|:--------:|:------:|:-----:|
| 20–99 events | 98 | 0.486 | 0.569 | 0.272 |
| 100–999 events | 99 | 0.486 | 0.527 | 0.219 |
| 1K–10K events | 99 | 0.518 | 0.597 | 0.248 |
| 10K+ events | 99 | 0.608 | 0.644 | 0.183 |

### Analysis

- **β increases with engagement volume.** Low-engagement posts (20–99 events) have β ≈ 0.49, meaning events are more clustered at the start. High-engagement posts (10K+) have β ≈ 0.61 — still sub-linear but closer to constant rate. Viral posts spread their engagement more evenly over time.
- **Std β decreases with volume.** The fitted β is more consistent for high-engagement posts (std 0.18 for 10K+ vs 0.27 for 20–99). This makes sense: with more data points, the fit is more reliable and the underlying process is more stable.
- **All β values are well below 1.** No post has β ≥ 1 (linear or accelerating engagement). Engagement always decelerates. This is consistent with the bursty nature of social media.
- **Aggregate β (0.34) < any per-post median β (0.49–0.61).** This is a classic ecological fallacy: the aggregate curve is pulled down by the fact that most posts die quickly. The per-post approach reveals that individual posts actually decay slower than the pooled curve suggests. For simulation, the per-post distributions are more useful.

### Takeaway

For simulation, sample β from the per-post distributions: **β ~ N(0.49, 0.27) for typical posts, shifting toward N(0.61, 0.18) for viral posts.** The actual arrival times within a post's lifetime follow N(t) ∝ t^β, then differentiate to get the inter-arrival rate λ(t) ∝ β · t^(β−1).

### Plots

- `temporal_decay_aggregate.png` — Pooled events with power-law fit N(t) ∝ t^β
- `temporal_decay_per_post.png` — 4-panel histogram of fitted β per engagement bucket, with KDE overlays and median markers

---

## Phase 3 — Time-to-first-engagement

**Script:** `eda/time_to_first.py`  
**Question:** How long does it take for the *first* repost, like, or reply to arrive after a post is created? A.K.A. "time to ignition."

### Data

Uses precomputed `first_reposted_us`, `first_liked_us`, `first_replied_us` columns from `post_lifetime`. Only posts that received that engagement type are included:

| Type | Posts | 
|:-----|------:|
| First repost | 2,484,790 |
| First like | 6,951,297 |
| First reply | 2,652,604 |

### Percentile distribution

| Percentile | First repost | First like | First reply |
|:-----------|:-----------:|:----------:|:-----------:|
| **p1** | 9.7s | 6.4s | **0.9s** |
| p5 | 23.4s | 14.9s | 2.5s |
| p10 | 41.4s | 24.6s | 11.5s |
| p25 | 2.3 min | 1.2 min | 1.3 min |
| **p50 (median)** | **13.3 min** | **5.6 min** | **5.9 min** |
| p75 | 1.5 h | 35.0 min | 39.8 min |
| p90 | 7.2 h | 3.2 h | 3.8 h |
| p95 | 14.9 h | 8.0 h | 9.4 h |
| p99 | 49.1 h | 30.0 h | 32.7 h |

| Metric | First repost | First like | First reply |
|:-------|:-----------:|:----------:|:-----------:|
| Mean | 3.1 h | 2.9 h | 4.0 h |
| Median | 13.3 min | 5.6 min | 5.9 min |

### Analysis

- **Replies are the fastest** (p1 = 0.9 seconds, median 5.9 min). Conversation starts almost immediately.
- **Likes follow closely** (median 5.6 min). Passive engagement is nearly as fast as replies.
- **Reposts are the slowest** (median 13.3 min, p1 = 9.7 seconds). Amplification takes longer to kick in — people need time to decide to share.
- The distributions are extremely right-skewed: while the median is in minutes, the **mean is in hours** (2.9–4.0h). A small fraction of posts get their first engagement days later.
- **p1 for replies (0.9s)** is remarkable — some posts get a reply within a second. These are likely automated bots or pre-coordinated interactions.
- The ordering (reply < like < repost) is consistent with the **engagement ladder**: conversation → approval → amplification.

### Takeaway

For simulation, the time-to-first-engagement can be modeled as a **log-skewed distribution**. The simplest approach: sample from the empirical distribution, or fit a lognormal to each type. The key constraint is that replies arrive fastest (median ~6 min), reposts slowest (median ~13 min). This ordering should be preserved in any synthetic data.

### Plots

- `time_to_first_cdf.png` — Semilog CDF of time-to-first for all three types, with median markers
- `time_to_first_hist.png` — Log-log histogram overlay of all three types

---

## Phase 6 — Engagement cascade ordering

**Script:** `eda/cascade_ordering.py`  
**Question:** In what order do engagement types arrive on a post? What's the probability of one type following another?

### Method

Three analyses:

1. **First-event dominance:** For the 1,035,232 posts that received all three engagement types, which type arrived first? Uses `first_*_us` columns.
2. **Pairwise ordering:** For each pair of types, counts which arrives first. Covers all posts with both types (not just those with all three).
3. **Markov transition matrix:** Samples 3,000 posts with ≥3 events, fetches their ordered event timelines from `post_engagement_events`, counts transitions between consecutive event types. Reports P(next_type | current_type).

### Results: First-event dominance

Of the 1,035,232 posts that got all three engagement types:

| First event | Count | % |
|:------------|------:|:--:|
| **Like** | 788,827 | **76.2%** |
| Reply | 174,324 | 16.8% |
| Repost | 72,081 | 7.0% |

### Results: Pairwise ordering

| Comparison | A first | B first | Tie |
|:-----------|:-------:|:-------:|:---:|
| Repost vs Like | 295,430 (12.6%) | **2,056,265 (87.4%)** | 0 |
| Repost vs Reply | **582,358 (55.5%)** | 467,485 (44.5%) | 0 |
| Like vs Reply | **1,636,759 (72.9%)** | 608,245 (27.1%) | 0 |

### Results: Markov transition matrix

83,736 transitions from 3,000 posts. P(next | current):

| Current ↓ \ Next → | Repost | Like | Reply |
|:--------------------|:------:|:----:|:-----:|
| **Repost** | 0.098 | **0.840** | 0.039 |
| **Like** | 0.155 | **0.772** | 0.038 |
| **Reply** | 0.062 | **0.807** | 0.090 |

### Results: Conditional probabilities

From the full 15.3M post table:

| Condition | Probability |
|:----------|:-----------:|
| P(like \| repost) | **0.943** |
| P(repost \| like) | 0.337 |
| P(reply \| repost) | 0.421 |
| P(repost \| reply) | 0.393 |
| P(reply \| like) | 0.322 |
| P(like \| reply) | **0.841** |

### Analysis

**Likes dominate the cascade.**

- **76% of posts with all three types are liked first.** The typical engagement sequence is: like → like → … → (some replies, possibly reposts).
- In the transition matrix, **the most likely next event after anything is a like** (77–84%). Likes tend to come in bursts: the self-loop probability P(like → like) = 0.77 means that once a post starts getting likes, it keeps getting them.
- **Replies are the "stickiest" after themselves** (P(reply → reply) = 0.09 vs P(like → reply) = 0.04). If a conversation starts, it tends to continue.
- **Reposts almost never happen in isolation.** P(like | repost) = 0.94 — if a post is reposted, it's almost certainly liked too. This makes reposts a strong signal: they imply the post has already passed the "like threshold."
- **The converse is weaker:** P(repost | like) = 0.34. Only 1/3 of liked posts ever get reposted. Likes are necessary but not sufficient for reposts.
- **Repost vs Reply is the closest race** (55.5% repost first vs 44.5% reply first). These two engagement types compete for "second place" after likes.
- The Markov matrix reveals an **absorbing-like structure**: likes cluster, and once you leave the like burst it's usually to a repost (16%) rather than a reply (4%).

### Engagement ladder

The data supports a hierarchical model:

```
                    REPLY
                   /      \
    NOTHING  →  LIKE  →  REPOST
      (51%)      (46%)    (16%)
                   \      /
                    REPLY
                   (17%)
```

1. **~51% of posts** get nothing
2. **~46% get likes** (entry point to engagement)
3. Of liked posts, **34% get reposted**, **32% get replied to**
4. Of reposted posts, **94% were already liked** — reposts come after likes
5. **Only 7%** of fully-engaged posts have reposts as the first event

### Takeaway

For simulation, implement engagement as a **sequential process**:
1. First, decide if the post gets any engagement (p ≈ 0.49)
2. First engagement is almost always a like (p ≈ 0.76 for multi-type posts)
3. Subsequent events follow the transition matrix — mostly more likes (p ≈ 0.77)
4. Reposts occur with P ≈ 0.16 after a like, but P ≈ 0.06 after a reply
5. Replies are the "exit" from the like burst with P ≈ 0.04

### Plots

- `cascade_first_event.png` — Pie chart of first engagement type (left) + pairwise bar chart (right)
- `cascade_transitions.png` — Heatmap of the Markov transition matrix P(next | current)

---

## Simulation calibration summary

All numbers you need to calibrate an agent-based simulation:

### Engagement counts (how many?)

| Parameter | Value | Distribution |
|:----------|:------|:-------------|
| α (power-law exponent) | 2.14–2.26 | Discrete power-law, x_min = 42–152 |
| P(any engagement) | 0.493 | Bernoulli |
| P(like \| engaged) | 0.927 | Conditional |
| P(repost \| engaged) | 0.331 | Conditional |
| P(reply \| engaged) | 0.354 | Conditional |

### Lifetimes (how long?)

| Parameter | Value |
|:----------|:------|
| Median combined lifetime | 3.8 hours |
| p90 combined lifetime | 1.7 days |
| Body distribution | Weibull (shape=0.53, scale=9.4h) up to 15.6h |
| Tail distribution | Pareto (α=2.16) beyond 15.6h |
| x_min transition | 15.6 hours |

### Temporal decay (when within the lifetime?)

| Parameter | Value |
|:----------|:------|
| Aggregate β | 0.34 (strongly sub-linear) |
| Per-post median β (low eng.) | 0.49 |
| Per-post median β (high eng.) | 0.61 |
| Rate function | λ(t) = β · A · t^(β−1) |

### Time-to-first (how fast does it start?)

| Parameter | Repost | Like | Reply |
|:----------|:------:|:----:|:-----:|
| Median | 13.3 min | 5.6 min | 5.9 min |
| p1 | 9.7 s | 6.4 s | 0.9 s |
| p99 | 49.1 h | 30.0 h | 32.7 h |

### Cascade order (in what sequence?)

| Transition | Probability |
|:-----------|:-----------:|
| First event = like | 0.762 |
| like → like | 0.772 |
| like → repost | 0.155 |
| like → reply | 0.038 |
| repost → like | 0.840 |
| reply → like | 0.807 |

### Key constraints for synthetic data

1. **α ≈ 2.15** — engagement counts must follow a discrete power-law with finite mean, infinite variance
2. **β ≈ 0.5** — engagement arrives as t^β within the post's lifetime, strongly front-loaded
3. **Median lifetime ≈ 4h** — most posts die within hours
4. **Likes come first** — 76% probability, then the Markov transition matrix governs the cascade
5. **P(like | repost) ≈ 0.94** — reposts imply likes; the reverse is not true (0.34)
6. **~51% of posts get nothing** — zero-inflated at the start

---

## Plots

All images in `post-lifetime/eda/results/` and `post-lifetime/results/`:

### Basic analysis (`post-lifetime/results/`)

| Plot | File |
|------|------|
| Lifetime histogram | [`lifetime_histogram.png`](../../post-lifetime/results/lifetime_histogram.png) |
| Lifetime CDF | [`lifetime_cdf.png`](../../post-lifetime/results/lifetime_cdf.png) |
| Likes vs reposts | [`engagement_correlation.png`](../../post-lifetime/results/engagement_correlation.png) |

### Phase 1 — Power-law counts (`post-lifetime/eda/results/`)

| Plot | File |
|------|------|
| Repost CCDF | [`powerlaw_ccdf_reposts.png`](../../post-lifetime/eda/results/powerlaw_ccdf_reposts.png) |
| Like CCDF | [`powerlaw_ccdf_likes.png`](../../post-lifetime/eda/results/powerlaw_ccdf_likes.png) |
| Reply CCDF | [`powerlaw_ccdf_replies.png`](../../post-lifetime/eda/results/powerlaw_ccdf_replies.png) |
| Combined CCDF | [`powerlaw_ccdf_engagement.png`](../../post-lifetime/eda/results/powerlaw_ccdf_engagement.png) |
| Comparison overlay | [`powerlaw_counts_compare.png`](../../post-lifetime/eda/results/powerlaw_counts_compare.png) |

### Phase 2a — Lifetime distributions

| Plot | File |
|------|------|
| Lifetime CCDF with fits | [`powerlaw_lifetimes_ccdf.png`](../../post-lifetime/eda/results/powerlaw_lifetimes_ccdf.png) |

### Phase 2b — Temporal decay

| Plot | File |
|------|------|
| Aggregate N(t) curve | [`temporal_decay_aggregate.png`](../../post-lifetime/eda/results/temporal_decay_aggregate.png) |
| Per-post β distribution | [`temporal_decay_per_post.png`](../../post-lifetime/eda/results/temporal_decay_per_post.png) |

### Phase 3 — Time-to-first

| Plot | File |
|------|------|
| CDF | [`time_to_first_cdf.png`](../../post-lifetime/eda/results/time_to_first_cdf.png) |
| Histogram | [`time_to_first_hist.png`](../../post-lifetime/eda/results/time_to_first_hist.png) |

### Phase 6 — Cascade ordering

| Plot | File |
|------|------|
| First event + pairwise | [`cascade_first_event.png`](../../post-lifetime/eda/results/cascade_first_event.png) |
| Transition heatmap | [`cascade_transitions.png`](../../post-lifetime/eda/results/cascade_transitions.png) |
