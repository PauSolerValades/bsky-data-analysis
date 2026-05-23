# session-analysis

Analysis of the session tables (`sessions_all`, `sessions_engagement`).

The sessions themselves are created in the sibling folders:
- `../creation-tukey/` → `pau_db.sessions_all` (all events, incl. likes — Tukey IQR)
- `../creation-tukey/` → `pau_db.sessions_engagement` (engaged events, no likes — Tukey IQR)

> **Deprecated:** `pau_db.sessions_tukey` and `pau_db.sessions_threshold` were built
> from a buggy `user_core_events` intermediate table (incorrect filtering).
> They are superseded by `sessions_all` and `sessions_engagement`.

---

## Pipeline

```
pau_db.sessions_all + pau_db.sessions_engagement
        │
        ▼
  export_sessions.py        → data_new/sessions_all.csv
                            → data_new/sessions_engagement.csv
        │
        ▼
  fit_distributions.R       → results_new/distribution_fit_results.csv
        │
        ▼
  plot_parameters.py        → results_new/params/ (density plots + CSVs)
```

---

## Quick start

```bash
# 1. Export sessions from StarRocks → CSV (~3 min, 5 GB + 2.2 GB)
uv run sessions/analysis/export_sessions.py --tables sessions_all,sessions_engagement --output-dir data_new

# 2. Fit distributions per user (~27 min on 32 cores)
Rscript sessions/analysis/fit_distributions.R \
  --sample 0 --cores 32 --tables sessions_all,sessions_engagement \
  --data-dir data_new --output-dir results_new

# 3. Plot parameter distributions
uv run sessions/analysis/plot_parameters.py --input results_new/distribution_fit_results.csv --output-dir results_new/params
```

---

## Files

| File | What |
|------|------|
| `export_sessions.py` | Exports `pau_db.sessions_all` and `pau_db.sessions_engagement` to CSV. Run this first. |
| `fit_distributions.R` | Per-user MLE fitting of power-law, lognormal, Weibull, gamma, exponential to session durations and inter-session gaps. The authoritative distribution-fitting script. |
| `plot_parameters.py` | Reads the fit results CSV, generates parameter density plots and per-distribution CSV exports. |
| `entropy.py` | Supplementary: per-user time-interval entropy for bot detection → `pau_db.user_time_entropy`. |

---

## Output

| Output | From | Description |
|--------|------|-------------|
| `data_new/sessions_all.csv` | `export_sessions.py` | 47.4M session rows, ~5 GB |
| `data_new/sessions_engagement.csv` | `export_sessions.py` | 19.6M session rows, ~2.2 GB |
| `results_new/distribution_fit_results.csv` | `fit_distributions.R` | 1.7M users × ~80 columns (best dist, params, LLR, AIC) |
| `results_new/params/` | `plot_parameters.py` | Density plots + per-distribution CSVs |

---

## Regeneration

```bash
# Re-export and re-fit from scratch
uv run sessions/analysis/export_sessions.py --tables sessions_all,sessions_engagement --output-dir data_new
Rscript sessions/analysis/fit_distributions.R --sample 0 --cores 32 --tables sessions_all,sessions_engagement --data-dir data_new --output-dir results_new
uv run sessions/analysis/plot_parameters.py --input results_new/distribution_fit_results.csv --output-dir results_new/params
```
