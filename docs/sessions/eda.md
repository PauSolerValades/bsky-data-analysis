# EDA — Session tables

**Date:** 2026-05-23  
**Script:** `sessions/eda/run_eda.py`  
**Tables:** `pau_db.sessions_all`, `pau_db.sessions_engagement`  
**Method:** Per-user adaptive Tukey's IQR clustering (Q3 + 1.5×IQR, 120s floor, 60min fallback)

---

## Table of contents

1. [§1 — Global summary statistics](#1--global-summary-statistics)
2. [§2 — Duration & gap histograms](#2--duration--gap-histograms)
3. [§3 — Per-user aggregates](#3--per-user-aggregates)
4. [§4 — CCDF plots](#4--ccdf-plots)
5. [§5 — Session composition](#5--session-composition)
6. [§6 — Gap vs duration](#6--gap-vs-duration)

---

## §1 — Global summary statistics

### sessions_all (all events incl. likes)

| Percentile | Duration (s) | Gap (s) |
|:----------:|:------------:|:-------:|
| P1 | 0 | 124.8 |
| P5 | 0 | 147.7 |
| P10 | 0 | 185.7 |
| P25 | 0 | 414.4 |
| **P50** | **22.9** | **2,191.0** |
| P75 | 133.8 | 13,149.6 |
| P90 | 379.0 | 47,442.4 |
| P95 | 770.8 | 79,428.9 |
| P99 | 11,435.4 | 180,738.7 |
| **Mean** | **881.9** | **16,197.9** |

- **47,424,114 sessions**, 45,849,961 inter-session gaps
- **33.2% of sessions have zero duration** — these are single-timestamp micro-bursts where all actions share the same `time_us` (e.g., multiple likes fired at the same microsecond)
- Median duration: **23 s** — short, dominated by rapid-fire actions
- Median gap: **36.5 min** (2,191 s)
- Mean is 38× the median for both duration and gap — heavily right-skewed

### sessions_engagement (no likes)

| Percentile | Duration (s) | Gap (s) |
|:----------:|:------------:|:-------:|
| P1 | 0 | 144.0 |
| P5 | 0 | 273.3 |
| P10 | 0 | 520.7 |
| P25 | 0 | 2,240.5 |
| **P50** | **290.0** | **11,700.9** |
| P75 | 3,753.0 | 45,281.7 |
| P90 | 38,039.1 | 95,010.4 |
| P95 | 143,918.6 | 171,807.7 |
| P99 | 561,602.7 | 346,558.3 |
| **Mean** | **25,025.2** | **38,733.7** |

- **19,623,374 sessions**, 17,021,700 inter-session gaps
- **22.7% zero-duration** — lower than sessions_all, fewer micro-bursts
- Median duration: **4 min 50 s** (290 s) — 12.7× longer than sessions_all
- Median gap: **3 h 15 min** (11,701 s) — 5.3× longer than sessions_all
- Right tail is extreme: P99 duration = 6.5 days, P99 gap = 4 days

---

## §2 — Duration & gap histograms

**Plot:** `02_histograms.png`

### Duration (left panel)
Log-log histograms of session durations for both tables, overlaid.

- **sessions_all** forms a peak at very short durations (0–30 s) with a smooth power-law-like decay above ~100 s
- **sessions_engagement** is shifted right by roughly one order of magnitude: the peak is at 100–1,000 s, and the tail extends further
- Both distributions are heavy-tailed with no obvious cutoff — compatible with lognormal, gamma, or power-law families

### Gap (right panel)
Log-log histograms of inter-session gaps.

- **sessions_all** gaps concentrate around 1,000–10,000 s (17 min–2.8 h), tapering smoothly
- **sessions_engagement** gaps are broader and shifted right, peaking around 10,000–100,000 s (2.8–28 h)
- The engagement-gap histogram is flatter than the all-events one — the gap distribution without likes is more diffuse

---

## §3 — Per-user aggregates

**Plot:** `03_per_user.png` (6 panels)

### (a) Sessions per user
Both tables show power-law-like distributions. sessions_all users have more sessions per user (denser activity timeline when likes are included).

### (b) Mean session duration per user
Wide power-law distributions spanning 8+ orders of magnitude. sessions_engagement users have systematically longer mean durations by ~1 order of magnitude.

### (c) Mean inter-session gap per user
Heavy-tailed. sessions_all users have tighter gaps (median user has ~500 s mean gap). sessions_engagement users span much wider.

### (d) Mean actions per session per user
Clusters around 2–20 actions. sessions_all has a narrower, higher peak (likes inflate action counts). sessions_engagement is more spread out.

### (e) Total actions per user
Reflects the underlying power-law from the event-count distribution in `docs/EDA.md` §4.

### (f) Per-user Tukey threshold distribution

| | sessions_all | sessions_engagement |
|---|---|---|
| Mean | 7,671 s (128 min) | 85,950 s (23.9 h) |
| Median | **142 s (2.4 min)** | **33,431 s (9.3 h)** |
| P25 | 120 s | 3,600 s (1 h) |
| P75 | 1,187 s (20 min) | 141,234 s (39.2 h) |
| Fallback | 0.0% | 10.7% |

**Key insight:** The threshold distributions are radically different.
- **sessions_all**: median threshold is only 142 s — when likes are included, most users have dense, short-gap timelines, so Tukey finds a tight boundary. Almost nobody hits the fallback.
- **sessions_engagement**: median threshold is 33,431 s (9.3 h!) — without likes, gaps are large and sparse. The IQR often fails (all gaps look similar to a sparse-popper), so many users fall back to the 60 min default. 10.7% of users use the fallback.

The 120 s floor (minimum threshold) is binding for a large fraction of sessions_all users but almost never for sessions_engagement users.

---

## §4 — CCDF plots

**Plot:** `04_ccdf.png`

Log-log complementary CDF (P(X ≥ x)) for durations and gaps.

### Duration CCDF (left panel)
- **sessions_all**: concave-down curve on log-log — not a pure power-law. The shape suggests a **lognormal** or **stretched exponential** (Weibull with k < 1). The steep drop below ~10 s reflects the pileup of zero-duration sessions.
- **sessions_engagement**: smoother, more linear on log-log above ~100 s — closer to a power-law shape for the body, but the tail bends. Also consistent with lognormal.

### Gap CCDF (right panel)
- Both tables show smoother CCDFs than durations. The curves are gently concave on log-log — consistent with **Weibull** (shape < 1) or **gamma** distributions.
- No sharp kinks; there is no single "natural" session boundary visible at the population level.

**Implication for fitting:** Durations likely need flexible distributions (lognormal, Weibull, gamma). Gaps look more regular — possibly Weibull or gamma with moderate shape parameters. Both tables need separate fits; the distributions are not just shifted versions of each other.

---

## §5 — Session composition

**Plot:** `05_composition.png` (6 panels)

### (a–b) Actions per session

| Bucket | sessions_all | sessions_engagement |
|--------|:-----------:|:-------------------:|
| 1 action | 0% | 25.3% |
| 2 actions | 33.2% | 14.1% |
| 3–5 actions | 30.2% | 24.5% |
| 6–10 actions | 18.8% | 17.6% |
| 11–20 actions | 10.9% | 10.4% |
| 21–50 actions | 5.8% | 6.2% |
| 51+ actions | 1.1% | 1.9% |

- **sessions_all has zero 1-action sessions**: every session contains ≥2 actions. This is because likes dominate and a user never fires a single like — they come in bursts. The minimum session has at least 2 co-occurring actions at the same timestamp.
- **sessions_engagement has 25.3% 1-action sessions**: a single post, reply, repost, or follow with no other activity within the Tukey window is a valid session.
- sessions_all: mean 9.7 actions, median 4
- sessions_engagement: mean 7.1 actions, median 4

### (c) Duration vs actions (hexbin, sessions_all)
Weak positive trend: more actions → longer sessions, but with enormous variance. Sessions with identical action counts can span from 0 s to hours.

### (d) Session type composition — sessions_all
| Type | % |
|------|---|
| Likes only | 59.2% |
| Mixed with likes | 33.0% |
| Mixed no likes | 3.8% |
| Posts/replies only | 3.1% |
| Reposts only | 0.7% |
| Network (follow/block) | 0.2% |
| Other | 0.0% |

**59.2% of sessions are pure likes** — the user does nothing but scroll and tap like. Another 33.0% mix likes with other actions. Only ~7% of sessions have no likes at all. The typical Bluesky session is a liking session.

### (e) Session type composition — sessions_engagement
| Type | % |
|------|---|
| Reposts only | 28.3% |
| Posts/replies only | 27.2% |
| Mixed (≥2 types) | 25.2% |
| Follows/blocks only | 16.7% |
| Empty | 2.5% |

Much more balanced than sessions_all. Reposting, posting/replying, and mixed sessions are all roughly equal. The "empty" 2.5% comes from sessions that captured only non-core event types (feed_threadgate, graph_listitem, etc.).

### (f) Duration bucket breakdown
| Bucket | sessions_all | sessions_engagement |
|--------|:-----------:|:-------------------:|
| 0 s | 33.2% | 22.7% |
| (0, 1 s) | 0.9% | 0.2% |
| [1 s, 5 s) | 4.8% | 1.1% |
| [5 s, 60 s) | 20.4% | 9.7% |
| [1 min, 5 min) | 20.7% | 22.8% |
| ≥5 min | 20.1% | 43.6% |

The big difference: sessions_all is split between zero-duration bursts (33%) and short sessions <5 min (46%). sessions_engagement has fewer zeroes (23%) and many more long sessions ≥5 min (44%).

---

## §6 — Gap vs duration

**Plot:** `06_gap_vs_duration.png`

Hexbin scatter: for each session that is followed by another, the next inter-session gap plotted against session duration.

- **sessions_all**: Spearman ρ = **0.043** — essentially no correlation. A long liking session does not predict a long gap afterward; a short one doesn't predict a short gap. Gaps and durations are independent when likes are included.
- **sessions_engagement**: Spearman ρ = **0.424** — moderate positive correlation. Longer content-creation sessions tend to be followed by longer breaks. This is consistent with a "session fatigue" model: the more effort a user puts into creating/curating, the longer they rest before the next session.

The hexbin shows sessions_all forming a dense blob at short durations with no directional trend, while sessions_engagement shows a visible diagonal structure — users cluster along a weak positive slope.

---

## Design implications for distribution fitting

The downstream analysis in `sessions/analysis/` fits per-user distributions to session durations and inter-session gaps. These EDA results inform the fitting:

1. **Both tables are needed, and they are not interchangeable.** sessions_all captures browsing rhythm (rapid, like-dominated). sessions_engagement captures content rhythm (slower, action-sparse). Fitting both gives complementary views of user behaviour.

2. **Zero-duration sessions are non-trivial.** 33% of sessions_all and 23% of sessions_engagement have duration = 0. These are real events (co-occurring actions at the same microsecond), not errors. The fitting code should either model them separately (zero-inflated model) or filter them from the duration fits and report the fraction.

3. **Durations are not pure power-laws.** The CCDF curvature suggests lognormal, Weibull (shape < 1), or gamma as more natural families. A pure power-law fit will likely be rejected by LLR tests for most users.

4. **Gaps are smoother than durations.** The gap CCDFs are more regular and may fit well with Weibull or gamma. The sessions_all gaps are tighter and more peaked; the sessions_engagement gaps are broader.

5. **The gap–duration correlation differs by table.** For sessions_engagement (ρ = 0.42), modelling gaps and durations as independent is questionable — a bivariate or copula-based model may be more appropriate. For sessions_all (ρ = 0.04), independence is a reasonable assumption.

6. **Thresholds span 5 orders of magnitude across users.** The per-user Tukey method produces thresholds from 120 s (the floor) to ~500,000 s for sparse engagement users. The fitting code already handles this by fitting per-user — no global threshold assumption is made.

---

*EDA run 2026-05-23 | `sessions/eda/run_eda.py` | ~36 minutes | 5 plots*
