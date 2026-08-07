# Pooled (homogeneous) parametric fits of within-session inter-post gaps

Question: instead of per-user fits (rejected in `METHODOLOGY.md` — Finding 1,
unidentifiable on the session window) or ECDF sampling (the chosen route),
can **one distribution with one parameter set** describe the within-session
gaps of a whole user group?

Two groupings tried:

1. **Everyone pooled** — all 11.1M within gaps (1M uniform subsample,
   `../3-ecdf/results/within_interpost_ecdf.txt`).
2. **Per (duration × gap) pair** — users grouped by their settled parametric
   type pair from `distribution-fit/results/pair_params_wide.tsv`, within
   gaps pooled per pair (the existing `../3-ecdf/results/within_ecdf__<dur>__<gap>.txt`
   exports, 250k gaps each). Users inside a pair are (supposedly)
   homogeneous, so this is the strongest case for a single parametric law.

## Procedure

`pooled_fit.py` fits the **same 8-candidate MLE battery** as
`distribution-fit/fit_lib.R` (expon, gamma, lognorm, weibull_min, fisk,
pareto, lomax, genpareto; natural support, no free loc) with **closed-form
KS / CvM / AD** (identical formulas to `fit_lib.R::gof_stats`) and ranks by
AIC. Implementation is scipy instead of fitdistrplus; fisk = log-logistic,
pareto = Pareto I, lomax = Pareto II with min 0, genpareto loc fixed at 0.

    uv run inter-post-creation/pooled_fit.py [file ...]

`pooled_fit_plot.py` overlays the pooled ECDF with the top fitted CDFs
(`plots/pooled_fit_ecdf.png`).

## Result 1 — everyone pooled (1,000,000 gaps)

| family | k | ΔAIC | KS | CvM | AD | params |
|---|---|---|---|---|---|---|
| **gamma** | 2 | 0 | 0.0351 | 216.19 | 2,082 | shape 0.845, scale 151.1 |
| weibull_min | 2 | 1,310 | 0.0390 | 293.20 | 2,674 | 0.902, 121.6 |
| expon | 2 | 4,008 | 0.0626 | 501.07 | 384,841 | scale 126.7 |
| lomax | 2 | 7,285 | 0.0516 | 308.93 | 4,094 | 11.19, 1298 |
| genpareto | 2 | 7,285 | 0.0516 | 308.93 | 4,094 | 0.089, 116.0 |
| fisk | 2 | 167,728 | 0.0722 | 1,387.84 | 15,063 | 1.268, 74.95 |
| lognorm | 2 | 203,187 | 0.0993 | 3,690.51 | 23,583 | σ 1.448, scale 63.6 |
| pareto | 2 | 1,472,452 | 0.3419 | 41,017.27 | 573,517 | degenerate (shape 0.24, scale → min) |

The AIC winner is **gamma — a light tail**. The heavy-tail look of the
pooled histogram is a **mixture artifact**: pooling heterogeneous users
manufactures an apparent heavy tail that no single heavy-tailed family can
fit (lognorm/fisk lose by >150k AIC; pareto degenerates). AD rejects every
family catastrophically (best AD ≈ 2,082 vs critical ≈ 2.5): the real ECDF
has a burst floor at 1–10s and a sharp kink at the session ceiling
(~200–600s) that no 2-parameter family reproduces
(`plots/pooled_fit_ecdf.png`).

## Result 2 — per (duration × gap) pair (250k gaps each)

Within-session gaps pooled over users of each settled type pair. AIC-best
and AD-best per pair (AD of the AIC winner in bold when catastrophic):

| pair (dur × gap) | AIC-best | ΔAIC to 2nd | AD of AIC-best | AD-best family (AD) |
|---|---|---|---|---|
| expon × lognorm | gamma | 5,745 | 224 | gamma (224) |
| expon × power_tail | weibull_min | 640 | 277 | weibull_min (277) |
| expon × weibull_min | gamma | 4,825 | 264 | gamma (264) |
| gamma × lognorm | gamma | 206 | 211 | weibull_min (161) |
| gamma × power_tail | weibull_min | 1,487 | 626 | weibull_min (626) |
| gamma × weibull_min | weibull_min | 358 | 213 | weibull_min (213) |
| lognorm × lognorm | expon | 741 | **13,628** | weibull_min (255) |
| lognorm × power_tail | expon | 2,944 | **93,414** | gamma (844) |
| lognorm × weibull_min | expon | 2,305 | **20,143** | gamma (210) |
| power_tail × lognorm | fisk | 2,788 | 724 | gamma (716) |
| power_tail × power_tail | expon | 2,151 | **15,957** | gamma (274) |
| power_tail × weibull_min | expon | 413 | **22,156** | fisk (692) |
| weibull_min × fisk | expon | 1,552 | **13,902** | gamma (266) |
| weibull_min × lognorm | lognorm | 747 | 819 | fisk (745) |
| weibull_min × power_tail | expon | 980 | **8,677** | gamma (344) |
| weibull_min × weibull_min | expon | 35 (coin-flip vs lomax) | **67,685** | lomax (599) |

Full 8-family detail for any pair:
`uv run inter-post-creation/2-parametric-fits/pooled_fit.py 3-ecdf/results/within_ecdf__<dur>__<gap>.txt`

### The fit degenerates — worse than pooled

1. **AIC and AD contradict each other in ~9/16 pairs.** Expon wins AIC in 8
   pairs (the bulk really is near-exponential), but expon's AD there is
   catastrophic (8k–93k, often the *worst* serious family): it fits the
   bulk and misses the tail, and AD weights exactly the tail. No winner is
   criterion-robust.
2. **AD rejects everything everywhere.** Best AD across all pairs ≈ 161 vs
   critical ≈ 2.5. Restricting to (supposedly) homogeneous users does not
   fix it — the session-ceiling kink exists inside every pair.
3. **Winner identity is unstable across pairs** (gamma, weibull, expon,
   fisk, lognorm each win somewhere; several near-ties) although the shapes
   are visually near-identical — family choice is noise, not signal.

## Verdict — keep ECDF

Per-pair ECDFs already exist and are exactly the files the sim loads
(`../3-ecdf/results/within_ecdf__<dur>__<gap>.txt`). A parametric law per pair is
formally rejected everywhere and adds nothing the ECDF doesn't already
encode exactly (burst floor, ceiling kink). The pair split is the
finer-grained replacement for the per-global-family ECDFs of
`METHODOLOGY.md`.

## Files

| file | content |
|---|---|
| `pooled_fit.py` | 8-family MLE battery + closed-form KS/CvM/AD on any gap export |
| `pooled_fit_plot.py` | pooled ECDF vs top fitted CDFs (log-x) |
| `results/pooled_fit.npz` | pooled sample + fitted params |
| `plots/pooled_fit_ecdf.png` | ECDF overlay, everyone pooled |
