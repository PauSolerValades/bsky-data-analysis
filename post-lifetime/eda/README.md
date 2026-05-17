# EDA — Post Lifetime Deep-Dive

This folder contains the exploratory data analysis scripts for post lifetime.

## Quick run

```bash
# All scripts (will install deps via uv on first run)
uv run post-lifetime/eda/fit_powerlaw_counts.py
uv run post-lifetime/eda/fit_powerlaw_lifetimes.py
uv run post-lifetime/eda/time_to_first.py
uv run post-lifetime/eda/temporal_decay.py
uv run post-lifetime/eda/cascade_ordering.py
```

Or run everything at once:

```bash
for script in post-lifetime/eda/*.py; do
    echo "=== $script ==="
    uv run "$script"
    echo
done
```

## Scripts

| Script | Phase | Data source | What it does |
|--------|-------|-------------|--------------|
| `fit_powerlaw_counts.py` | 1 | `post_lifetime` | Discrete power-law fit on `total_reposts`, `total_likes`, `total_replies`, `total_engagement`. MLE for α + KS-minimisation for x_min. LLR test vs lognormal, Weibull, exponential. |
| `fit_powerlaw_lifetimes.py` | 2a | `post_lifetime` | Continuous distribution fit on combined lifetime. Compares Pareto, lognormal, Weibull, exponential via log-likelihood. CCDF overlays. |
| `time_to_first.py` | 3 | `post_lifetime` | Time-to-first-engagement distributions. How many seconds/minutes/hours until the first repost/like/reply? Percentile tables + CDF/hist plots. |
| `temporal_decay.py` | 2b | `post_engagement_events` | Two approaches: (1) Aggregate: pool all events, fit N(t) ∝ t^β. (2) Per-post: fit individual decay curves for sampled posts, show β distribution per engagement bucket. |
| `cascade_ordering.py` | 6 | `post_lifetime` + `post_engagement_events` | Which engagement type comes first? Markov transition matrix P(next_type | current_type). Conditional probabilities. |

## Output

All results go to `post-lifetime/eda/results/`:

```
results/
├── powerlaw_ccdf_reposts.png
├── powerlaw_ccdf_likes.png
├── powerlaw_ccdf_replies.png
├── powerlaw_ccdf_engagement.png
├── powerlaw_counts_compare.png
├── powerlaw_lifetimes_ccdf.png
├── time_to_first_cdf.png
├── time_to_first_hist.png
├── temporal_decay_aggregate.png
├── temporal_decay_per_post.png
├── cascade_first_event.png
└── cascade_transitions.png
```

## Tuning

| Script | Flag | Default | Effect |
|--------|------|---------|--------|
| `temporal_decay.py` | `--sample N` | 200 | Posts per engagement bucket for per-post fitting |
| `cascade_ordering.py` | `--sample N` | 5000 | Posts for transition matrix computation |

More samples = better statistics but slower.

## Methodology notes

### Power-law fitting

- **Discrete** (counts): MLE α = 1 + n / Σ ln(x_i / (x_min − 0.5)). x_min selected by minimising KS distance. Goodness-of-fit via bootstrap p-value.
- **Continuous** (lifetimes): MLE α = 1 + n / Σ ln(x_i / x_min). Compared vs lognormal/Weibull/exponential via log-likelihood.

### Temporal decay

- `N(t) ∝ t^β` where β < 1 means deceleration (most events early), β ≈ 1 means constant rate, β > 1 means acceleration (rare).
- Aggregate approach pools all events, bins by log-time, fits cumulative.
- Per-post samples from 4 buckets (20–99, 100–999, 1K–10K, 10K+ events), fits each separately, shows β distribution.

### Cascade ordering

- First-event: uses `first_*` columns to determine ordering.
- Transition matrix: samples posts, fetches ordered event sequences, counts transitions between consecutive event types.
- Conditional probs: `P(like | repost)` etc. from the full `post_lifetime` table.
