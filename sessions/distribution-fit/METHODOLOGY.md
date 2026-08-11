# Per-user distribution fitting — methodology

Fitting parametric distributions to per-user **session durations** and **inter-session
gaps** from `pau_db.sessions` (DBSCAN, eps = 300s, min_samples = 2; see
`sessions/final_parameters.md`). All users (2.12M), all their durations and gaps.

## Phase 1 — Python / distfit (abandoned)

Initial approach: `distfit` per user, candidates
`expon, gamma, lognorm, weibull_min, pareto, lomax, genpareto`, scored with
`wasserstein` and `goodness_of_fit`. Abandoned after discovering three
methodological defects:

1. **Invalid GOF scores.** distfit's `wasserstein` (and `energy`, `ks`) computes
   `wasserstein_distance(y_obs, pdf)` — the distance between the data values and
   the fitted density *ordinates* evaluated at those points. Units do not match;
   the statistic is meaningless. (Confirmed by reading distfit 2.0.2 source,
   `_compute_fit_score`.)
2. **Unusable `goodness_of_fit`.** Wraps `scipy.stats.goodness_of_fit` with
   `n_mc_samples=1000` — ~1000 refits per candidate distribution (hours per
   chunk) and p-values quantized to 1/1001, useless as a ranking score.
3. **`loc` artifacts.** scipy MLE fits a free location parameter that clamps to
   `min(data) − ε` (aggregate fits showed `loc = 300.99999…`), gaming the
   likelihood instead of modeling physics.

Intermediate fix (closed-form Anderson–Darling against the scipy fits) kept the
run alive briefly, but the loc pathology and the invalid scores motivated the
switch to R.

## Phase 2 — R / fitdistrplus (final)

R 4.5.3 (miniconda), packages: `fitdistrplus` (Delignette-Muller & Dutang 2015,
JSS 64(4)), `actuar`, `evd`, `arrow`, `data.table`.

**Data prep** (`dump_data.py`, Python → parquet, 10 strided user chunks):
durations = non-singleton sessions only (`duration_s > 0`, micro sessions
discarded); gaps = consecutive `session_start − session_end`; gaps below eps
**cannot exist by construction** (DBSCAN absorbs events < eps apart into the
same session — there is no censored mass below eps), so they are fitted after a
known shift of −300s. This is a location shift, not a truncation correction.

**Per-user fitting** (`fit_lib.R`, `fit_chunk.R`): for each user × column,
`fitdist` (MLE, natural support, no free loc) over 7 candidates:

| canonical name | R distribution | package |
|---|---|---|
| expon | `exp` | stats |
| gamma | `gamma` | stats |
| lognorm | `lnorm` | stats |
| weibull_min | `weibull` | stats |
| pareto_i | `pareto1`, threshold fixed at `min(x)` | actuar |
| lomax | `pareto2` (min = 0) | actuar |
| genpareto | `gpd` (loc = 0) | evd |

Pareto I's threshold is a support-bound parameter whose free MLE collapses to
the boundary `min(x)` (singular Hessian, CRAN-verified API: `dpareto1(x, shape,
min)`). It is therefore fixed at `min(x)` and only the shape is MLE'd — the
textbook estimator for the single-parameter Pareto. Since `min(x)` is the
boundary MLE and not a known constant, AIC/BIC count it as an estimated
parameter (+1 df). (fisk / log-logistic was
dropped from the battery.)

GOF statistics (KS, Cramér–von Mises, Anderson–Darling) computed in closed form
against the fitted CDF — `gofstat` errors on small n due to internal chi-square
binning; our implementation was verified identical to `gofstat` to 6 decimals
where both work. Selection by **AIC** (complexity-penalized); AD kept as
heavy-tail GOF descriptor. Validated on simulated data before the production
run (light-tail families recovered; pareto/lomax/genpareto cross-confuse —
see below). Parallelism: `mclapply`, 64 cores; ~3 min per 212k-user chunk.

## Post-processing

- **Step 2** (`step2_build_best.py`): AIC-best distribution per (user, column).
  pareto_i/lomax/genpareto grouped as family **`pareto`** — Lomax ≡
  reparametrized GPD, Pareto-I is its boundary case, so AIC's sibling choice
  is partly arbitrary (simulation confirmed).
- **Pairing** (`pair_params.py`): the step the thesis samples from. Selects the
  AIC winner per (user, column) from the raw fits, converts every power-tail
  win to **canonical GPD(ξ, σ, μ)** under the single family **`pareto`**
  (lomax → ξ=1/α, σ=λ/α, μ=0; pareto_i → ξ=1/α, σ=xₘ/α, μ=xₘ; genpareto
  unchanged), and emits one wide row per user:
  `params__{col1}__{col2}.tsv` with one column per parameter
  (shape, scale, rate, meanlog, sdlog, xi, sigma, mu), blanks where a family
  has no such parameter. Conversion verified |ΔCDF| ≤ 1e-16 and against the
  old canonical table on real fits.
- **Step 4** (`step4_fit_parameters.R`): distributions fitted to the
  across-user parameter values (trustworthy subset **n_obs ≥ 30**), AIC-selected
  among exp/gamma/lnorm/weibull (+norm for unbounded), plus Spearman
  correlations between each family's parameter pair.
- **Step 5** (`step5_plot_param_distributions.R`): per-series histograms with
  fitted PDF overlay (AGENTS.md thesis style; ggplot2 because no LaTeX on this
  machine). Axes clipped to the 1–99% central mass.

## Files

| file | content |
|---|---|
| `data/chunk{0-9}.parquet` | per-user durations & gaps (raw; gaps unshifted) |
| `results/gof__chunk{0-9}.tsv` | all fits: n_obs, loglik, AIC, BIC, KS, CvM, AD |
| `results/params__chunk{0-9}.tsv` | MLE parameters of every candidate |
| `results/best_per_user.tsv` | AIC-best distribution + family per user |
| `results/best_params.tsv` | parameters of the winning distribution |
| `results/pareto_canonical.tsv` | GPD(ξ, σ, μ) per pareto user |
| `results/param_distributions.tsv` | across-user parameter meta-fits |
| `results/param_correlations.tsv` | parameter-pair Spearman ρ |
| `plots/param__*.png` | 22 parameter histograms + overlay |

## Caveats

1. Units (user × column) with n_obs = 1 are unfittable and absent (~20%).
2. Model selection at n_obs < 30 is unreliable; meta-fits use n_obs ≥ 30.
3. The ξ meta-distribution is **multimodal** (bounded / heavy / very-heavy
   subpopulations) — the normal overlay is a poor model; sample ξ empirically.
4. Within-family parameters are strongly correlated (e.g., gap pareto
   ξ–σ ρ = −0.86): sample joint empirical parameter rows, not marginals.
