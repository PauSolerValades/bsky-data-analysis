# Inter-Post Gap Analysis — Bluesky Firehose

**How much time passes between consecutive posts authored by the same user?**  
Measured in two ways: *globally* (all posts) and *within-session* (posts belonging to the same browsing session). Each user's gap vector is then fitted to 5 candidate distributions (power-law, lognormal, Weibull, gamma, exponential) to determine the best generative model.

**Data sources:** `pau_db.engaged_events` (post_top + post_reply) joined to  
`pau_db.sessions_engagement` (Tukey IQR, no likes).  
**Date:** 2026-05-23 (re-run from corrected tables)

---

## Table of contents

1. [Motivation](#motivation)
2. [Approach](#approach)
3. [Scripts involved](#scripts-involved)
4. [Data flow](#data-flow)
5. [Results](#results)
6. [How to regenerate](#how-to-regenerate)
7. [Calibration summary (for simulation)](#calibration-summary-for-simulation)

---

## Motivation

- **Simulation calibration:** Agent-based models of Bluesky need realistic inter-post arrival times. Should posts come from a power-law (bursty, heavy-tailed), a lognormal (multiplicative), or a Weibull (hazard-driven) process?
- **Bot detection baseline:** Automation often produces unusually regular or unusually tight inter-post gaps. Knowing the "human" distribution helps flag outliers.
- **Session context:** Do within-session gaps differ in shape and scale from global (cross-session) gaps? Yes — see results.

---

## Approach

### Step 1 — Extract post/reply events

From `pau_db.engaged_events`, select all events where `event_type IN ('post_top', 'post_reply')`. This gives 912,684 users with ≥2 posts/replies. Event timestamps are joined in Python against `pau_db.sessions_engagement` interval boundaries to tag which session each event belongs to.

### Step 2 — Compute inter-post gaps (2 modes)

For each user, sort all their post/reply events by `time_us`:

| Mode | Definition | Sessions used |
|------|-----------|---------------|
| **Global** | Gap to the immediately preceding post/reply by the same user, regardless of session boundary. | No |
| **Within-session** | Gap to the immediately preceding post/reply **within the same `sessions_engagement` session**. Uses precomputed per-user adaptive session boundaries (Tukey's fences / IQR). | `pau_db.sessions_engagement` |

### Step 3 — Distribution fitting (per-user)

For each user with ≥10 positive gaps, fit five distributions using **MLE**:

| Distribution | Method | Parameters |
|-------------|--------|------------|
| **Power-law** | Clauset et al. 2009 — MLE with KS-minimisation for x<sub>min</sub> (`poweRlaw`) | α (exponent), x<sub>min</sub> (threshold) |
| **Exponential** | MLE via `fitdistrplus` | rate λ |
| **Lognormal** | MLE via `fitdistrplus` | meanlog μ, sdlog σ |
| **Weibull** | MLE via `fitdistrplus` | shape k, scale λ |
| **Gamma** | MLE via `fitdistrplus` | shape k, rate θ |

Best model selection via **Vuong's log-likelihood ratio test** (power-law vs alternatives) followed by **AIC** as tiebreaker.

---

## Scripts involved

| Script | Language | Purpose |
|--------|----------|---------|
| `inter-post-gaps/extract.py` | Python | Extracts inter-post gaps from `engaged_events` + `sessions_engagement`, writes `data/inter_post_gaps.csv` |
| `inter-post-gaps/fit.R` | R | Reads the CSV, fits 5 distributions per user, writes `results/inter_post_gap_fits.csv` + summary |
| `inter-post-gaps/plot.py` | Python | Generates parameter-distribution plots from the fit results (alpha, xmin, meanlog, shape, etc.) |

### Dependencies

- **Python:** `pymysql`, `numpy`, `matplotlib`
- **R:** `poweRlaw`, `fitdistrplus`, `data.table`, `tidyverse`, `broom`, `parallel`

---

## Data flow

```
pau_db.engaged_events (post_top + post_reply)
       +
pau_db.sessions_engagement
       │
       ▼
  extract.py        → data/inter_post_gaps.csv
       │              (26.4M global + 21.3M within-session gaps)
       ▼
  fit.R             → results/inter_post_gap_fits.csv
       │              (41,670 users, 50K sample)
       ▼
  plot.py           → results/*.png
```

---

## Results

### Sample configuration

- **Sample:** 50,000 users (from 912,684 with ≥2 posts)
- **Min gaps per user:** 10
- **Cores:** 8
- **Run time:** ~166s (R fitting)

### Global gaps (all posts, same user)

| Statistic | Value |
|-----------|-------|
| Total gaps | 26,399,951 |
| Users | 912,684 |
| Median gap | **9.7 min** |
| Mean gap | 4.2 hours (253.8 min) |
| P75 | 107.3 min |
| P90 | 13.0 hours (780.4 min) |
| P99 | 62.1 hours |

### Within-session gaps (same user, same session)

| Statistic | Value |
|-----------|-------|
| Total gaps | 21,342,010 |
| Users | 841,414 |
| Median gap | **4.8 min** |
| Mean gap | 2.1 hours (128.2 min) |
| P75 | 32.4 min |
| P90 | 4.0 hours (237.0 min) |
| P99 | 37.4 hours |

Within-session gaps are **2.0× smaller** than global gaps (median 4.8 min vs 9.7 min), confirming that users post in bursts during sessions and the multi-hour gaps are inter-session pauses. The ratio is slightly lower than the old (buggy) pipeline's 2.3× — sessions_engagement produces tighter within-session clustering.

### Best-fit distribution breakdown

#### Global (22,248 users with fits)

| Distribution | Users | % |
|-------------|------:|:--:|
| **Power-law** | 11,861 | **53.3%** |
| Lognormal | 5,876 | 26.4% |
| Weibull | 4,495 | 20.2% |
| Gamma | 13 | 0.1% |
| Exponential | 3 | 0.0% |

#### Within-session (19,422 users with fits)

| Distribution | Users | % |
|-------------|------:|:--:|
| **Power-law** | 12,263 | **63.1%** |
| Lognormal | 4,147 | 21.3% |
| Weibull | 2,679 | 13.8% |
| Exponential | 301 | 1.5% |
| Gamma | 32 | 0.2% |

### Key insight

**Power-law dominates in both modes, and even more so within sessions (63.1% vs 53.3%).** This means inter-post gaps are heavy-tailed and bursty — most gaps are short (seconds to minutes), but occasional long tails stretch to hours. Removing inter-session pauses via session clustering concentrates the distribution in the power-law regime.

Compared to the old pipeline (71.6% within-session power-law), the proportion is lower. This is expected — `sessions_engagement` has wider per-user thresholds (median 9.3 h), so it splits content sessions more aggressively, increasing the proportion of large within-session gaps that fit lognormal or Weibull better.

#### Power-law parameters (users where power-law is best)

| Parameter | Global | Within-session |
|-----------|--------|----------------|
| α (mean) | 6.68 | 21.41 |
| α (median) | **1.80** | **2.39** |
| x<sub>min</sub> (median) | 0.73 h (44 min) | 0.19 h (11 min) |

**α medians are more representative than means** — a few users with unstable fits at extreme α values inflate the mean. The true typical α is **1.80–2.39**, consistent with a heavy-tailed bursty process. α < 2 means infinite variance (extreme burstiness) for the global case. Within sessions α is slightly higher (2.39), indicating somewhat less extreme bursts when session boundaries are removed.

#### Lognormal parameters (2nd best)

| Parameter | Global | Within-session |
|-----------|--------|----------------|
| meanlog (median) | 7.98 | 7.74 |
| sdlog (median) | 2.15 | 1.02 |
| → median gap | 48.8 min | 38.3 min |

Lognormal users have more centralized gaps — multiplicative noise around a typical posting cadence. Within-session sdlog is half the global value, confirming that session boundaries concentrate posting into a tighter regime.

#### Weibull parameters (3rd place)

| Parameter | Global | Within-session |
|-----------|--------|----------------|
| shape k (median) | 0.53 | 0.68 |
| k < 1 (decr. hazard) | 87% | 68% |

87% of global Weibull users have k < 1 — **decreasing hazard**: the longer a user hasn't posted, the *less* likely they are to post soon. Within sessions this drops to 68%, reflecting that within-session gaps are more regular.

#### Gap sizes by best-fit distribution

| Best fit | Global median | Within-session median |
|----------|:------------:|:---------------------:|
| Power-law | 5.2 min | **3.8 min** |
| Lognormal | 8.5 min | 3.7 min |
| Weibull | 152.6 min | 86.5 min |
| Exponential | 0.5 min | 1.1 min |
| Gamma | 30.0 min | 0.0 min |

Users whose gaps follow a Weibull distribution tend to post much slower (median 2.5h globally, 1.4h within-session) — these are casual/infrequent posters. Power-law users are the bursty core (median 5.2 min globally, 3.8 min within sessions).

---

## How to regenerate

```bash
# 1. Extract inter-post gaps from StarRocks (~12 min)
uv run inter-post-gaps/extract.py --summary

# 2. Fit distributions (per-user, parallel)
Rscript inter-post-gaps/fit.R --sample 50000 --cores 8

# For ALL users (heavy, may take hours):
Rscript inter-post-gaps/fit.R --cores 16

# 3. Plot parameter distributions
uv run inter-post-gaps/plot.py
```

---

## Calibration summary (for simulation)

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Dominant distribution** | Power-law | 53% global, 63% within-session |
| **Typical α** | 1.8 – 2.4 | Median across users; heavy-tailed |
| **Within-session median gap** | **4.8 min** | Bursty posting |
| **Global median gap** | **9.7 min** | Includes inter-session pauses |
| **Secondary distributions** | Lognormal (21–26%), Weibull (14–20%) | For non-bursty users |
| **Sample α from** | Median α ≈ 2.0, or mix of power-law + lognormal + Weibull depending on user archetype |

For simulation, the simplest approach:
1. Draw a user's inter-post gap distribution type from the empirical mix (53% power-law, 26% lognormal, 20% Weibull for global)
2. If power-law: sample gap ~ Pareto(α ≈ 1.8, x<sub>min</sub> ≈ 44 min) for global, or Pareto(α ≈ 2.4, x<sub>min</sub> ≈ 11 min) for within-session
3. If lognormal: sample gap ~ logN(μ ≈ 8.0, σ ≈ 2.2) for global, or logN(μ ≈ 7.7, σ ≈ 1.0) for within-session
4. If Weibull: sample gap ~ Weibull(shape ≈ 0.53, from the decreasing-hazard regime)

---

## Plots

Generated by `inter-post-gaps/plot.py` from the fit results:

| Plot | File | Description |
|------|------|-------------|
| Best distribution | [`inter_post_gap_best_dist.png`](../inter-post-gaps/results/inter_post_gap_best_dist.png) | Bar chart: best-fit distribution per gap type |
| Power-law α | [`inter_post_gap_alpha_hist.png`](../inter-post-gaps/results/inter_post_gap_alpha_hist.png) | Histogram of α for power-law users |
| Power-law x<sub>min</sub> | [`inter_post_gap_xmin_hist.png`](../inter-post-gaps/results/inter_post_gap_xmin_hist.png) | Histogram of x<sub>min</sub> (hours) |
| Lognormal params | [`inter_post_gap_lognormal_scatter.png`](../inter-post-gaps/results/inter_post_gap_lognormal_scatter.png) | Hexbin of meanlog vs sdlog |
| Weibull shape | [`inter_post_gap_weibull_shape.png`](../inter-post-gaps/results/inter_post_gap_weibull_shape.png) | Histogram of Weibull shape k |
| Parameter summary | [`inter_post_gap_param_summary.png`](../inter-post-gaps/results/inter_post_gap_param_summary.png) | 6-panel grid: α, x<sub>min</sub>, meanlog, sdlog, shape, scale |

---

*Analysis run: 2026-05-23. Data: April 2026 Bluesky firehose snapshot.  
Tables: `pau_db.engaged_events` + `pau_db.sessions_engagement`.*
