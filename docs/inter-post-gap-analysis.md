# Inter-Post Gap Analysis — Bluesky Firehose

**How much time passes between consecutive posts authored by the same user?**  
Measured in two ways: *globally* (all posts) and *within-session* (posts belonging to the same browsing session). Each user's gap vector is then fitted to 5 candidate distributions (power-law, lognormal, Weibull, gamma, exponential) to determine the best generative model.

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

From `pau_db.user_core_events`, select all events where `event_type IN ('post', 'reply')`. This gives 28M events from 1.45M users. Each row is `(did, time_us, event_type)`.

### Step 2 — Compute inter-post gaps (2 modes)

For each user, sort all their post/reply events by `time_us`:

| Mode | Definition | Sessions used |
|------|-----------|---------------|
| **Global** | Gap to the immediately preceding post/reply by the same user, regardless of session boundary. | No |
| **Within-session** | Gap to the immediately preceding post/reply **within the same `sessions_tukey` session**. Uses precomputed per-user adaptive session boundaries (Tukey's fences / IQR). | `pau_db.sessions_tukey` |

The SQL uses `LAG(time_us) OVER (PARTITION BY did, session_start ORDER BY time_us)` with a `LEFT JOIN` to `sessions_tukey`.

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
| `post-lifetime/inter_post_gaps.py` | Python | Extracts inter-post gaps from `user_core_events` + `sessions_tukey`, writes `data/inter_post_gaps.csv` |
| `post-lifetime/inter_post_gap_fit.R` | R | Reads the CSV, fits 5 distributions per user, writes `results/inter_post_gap_fits.csv` + summary |
| `post-lifetime/plot_inter_post_gap_params.py` | Python | Generates parameter-distribution plots from the fit results (alpha, xmin, meanlog, shape, etc.) |

### Dependencies

- **Python:** `pymysql`, `numpy`, `matplotlib`
- **R:** `poweRlaw`, `fitdistrplus`, `data.table`, `tidyverse`, `broom`, `parallel`

---

## Data flow

```
bsky.posts + bsky.records  ──→  pau_db.user_core_events
                                      │
                         pau_db.sessions_tukey
                                      │
                         ┌────────────┴────────────┐
                         │  inter_post_gaps.py     │
                         │  JOIN + LAG → (did,     │
                         │  gap_s, gap_type)       │
                         └────────────┬────────────┘
                                      │
                              data/inter_post_gaps.csv
                              (51.9M rows, 2.8 GB)
                                      │
                         ┌────────────┴────────────┐
                         │  inter_post_gap_fit.R   │
                         │  Per-user MLE:          │
                         │  powerlaw / lognormal / │
                         │  Weibull / gamma / exp  │
                         └────────────┬────────────┘
                                      │
                         results/inter_post_gap_fits.csv
                         (31K users, 50 columns)
                                      │
                         ┌────────────┴────────────┐
                         │  plot_inter_post_gap_   │
                         │  params.py              │
                         │  Parameter histograms   │
                         └─────────────────────────┘
```

---

## Results

### Sample configuration

- **Sample:** 50,000 users (from ~1.08M with ≥2 posts)
- **Min gaps per user:** 10
- **Cores:** 8
- **Run time:** ~107s (R fitting)

### Global gaps (all posts, same user)

| Statistic | Value |
|-----------|-------|
| Total gaps | 33,051,710 |
| Users | 1,080,171 |
| Median gap | **9.9 min** |
| Mean gap | 4.5 hours |
| P75 | 112 min |
| P90 | 13.6 hours |
| P99 | 68.7 hours |

### Within-session gaps (same user, same session)

| Statistic | Value |
|-----------|-------|
| Total gaps | 18,839,503 |
| Users | 660,169 |
| Median gap | **4.3 min** |
| Mean gap | 1.5 hours |
| P75 | 28 min |
| P90 | 2.5 hours |
| P99 | 25.7 hours |

Within-session gaps are **2.3× smaller** than global gaps (median 4.3 min vs 9.9 min), confirming that users post in bursts during sessions and the multi-hour gaps are inter-session pauses.

### Best-fit distribution breakdown

#### Global (18,631 users with fits)

| Distribution | Users | % |
|-------------|------:|:--:|
| **Power-law** | 9,771 | **52.4%** |
| Lognormal | 5,053 | 27.1% |
| Weibull | 3,790 | 20.3% |
| Gamma | 13 | 0.1% |
| Exponential | 4 | 0.0% |

#### Within-session (12,551 users with fits)

| Distribution | Users | % |
|-------------|------:|:--:|
| **Power-law** | 8,989 | **71.6%** |
| Lognormal | 1,966 | 15.7% |
| Weibull | 1,271 | 10.1% |
| Exponential | 255 | 2.0% |
| Gamma | 70 | 0.6% |

### Key insight

**Power-law dominates in both modes, and even more so within sessions (71.6% vs 52.4%).** This means inter-post gaps are heavy-tailed and bursty — most gaps are short (seconds to minutes), but occasional long tails stretch to hours. Removing inter-session pauses via session clustering concentrates the distribution in the power-law regime.

#### Power-law parameters (users where power-law is best)

| Parameter | Global | Within-session |
|-----------|--------|----------------|
| α (mean) | 4.96 | 21.17 |
| α (median) | **1.81** | **2.65** |
| x<sub>min</sub> (median) | 0.77 h (46 min) | 0.08 h (5 min) |

**α medians are more representative than means** — a few users with unstable fits at extreme α values inflate the mean. The true typical α is **1.8–2.7**, consistent with a heavy-tailed bursty process. α < 2 means infinite variance (extreme burstiness).

#### Lognormal parameters (2nd best)

| Parameter | Global | Within-session |
|-----------|--------|----------------|
| meanlog (median) | 7.94 | 7.18 |
| sdlog (median) | 2.16 | 0.88 |
| → median gap | 46.6 min | 21.9 min |

Lognormal users have more centralized gaps — multiplicative noise around a typical posting cadence.

#### Weibull parameters (3rd place)

| Parameter | Global | Within-session |
|-----------|--------|----------------|
| shape k (median) | 0.53 | 0.80 |
| k < 1 (decr. hazard) | 87% | 61% |

87% of Weibull users have k < 1 — **decreasing hazard**: the longer a user hasn't posted, the *less* likely they are to post soon. This is the hallmark of engagement fatigue or session-ending behavior.

#### Gap sizes by best-fit distribution

| Best fit | Global median | Within-session median |
|----------|:------------:|:---------------------:|
| Power-law | 7.4 min | **3.6 min** |
| Lognormal | 10.0 min | 8.3 min |
| Weibull | 147.5 min | 60.0 min |
| Exponential | 0.2 min | 1.6 min |
| Gamma | 60.0 min | 1.6 min |

Users whose gaps follow a Weibull distribution tend to post much slower (median 2.5h globally) — these are casual/infrequent posters. Power-law users are the bursty core (median 7.4 min globally, 3.6 min within sessions).

---

## How to regenerate

```bash
# 1. Extract inter-post gaps from StarRocks (~10 min)
uv run post-lifetime/inter_post_gaps.py --summary

# 2. Fit distributions (per-user, parallel)
Rscript post-lifetime/inter_post_gap_fit.R --sample 50000 --cores 8

# For ALL users (heavy, may take hours):
Rscript post-lifetime/inter_post_gap_fit.R --cores 16

# 3. Plot parameter distributions
uv run post-lifetime/plot_inter_post_gap_params.py
```

---

## Calibration summary (for simulation)

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Dominant distribution** | Power-law | 52% global, 72% within-session |
| **Typical α** | 1.8 – 2.7 | Median across users; heavy-tailed |
| **Within-session median gap** | **4.3 min** | Bursty posting |
| **Global median gap** | **9.9 min** | Includes inter-session pauses |
| **Secondary distributions** | Lognormal (22%), Weibull (15%) | For non-bursty users |
| **Sample α from** | Median α ≈ 2.0, or mix of power-law + lognormal + Weibull depending on user archetype |

For simulation, the simplest approach:
1. Draw a user's inter-post gap distribution type from the empirical mix (52% power-law, 27% lognormal, 20% Weibull for global)
2. If power-law: sample gap ~ Pareto(α ≈ 2.0, x<sub>min</sub> ≈ 46 min)
3. If lognormal: sample gap ~ logN(μ ≈ 7.9, σ ≈ 2.2)
4. If Weibull: sample gap ~ Weibull(shape ≈ 0.53, from the decreasing-hazard regime)

---

*Analysis run: 2026-05-20. Data: April 2026 Bluesky firehose snapshot.*
