# Inter-post creation time — methodology

Per-user **inter-post gaps** (time between consecutive `post_top`/`post_reply`
events), in two columns:

- **interpost_global** — all consecutive post pairs, ignoring session boundaries.
- **interpost_within** — pairs where both posts fall inside the same session
  (`pau_db.sessions`, DBSCAN eps = 300s, min_samples = 2; see
  `sessions/final_parameters.md`).

Sessions: production table, full data. Posts: raw μs timestamps (not deduped
to seconds — sessions were, posts are not). Endpoint intervals (session start
→ first post, last post → session end) are censored and excluded.

## Data prep (`dump.py`)

Per 2k-DID batch: fetch sessions + post events, assign posts to session
intervals by binary search, emit both columns. 10 strided chunks over the
2.12M `sessions_users` DIDs → `data/chunk{0-9}.parquet` (did, col, value),
same format as `distribution-fit`. Totals: **25.2M global gaps, 11.1M within
gaps**; 124k exact same-μs duplicate posts (dropped by the `value > 0` filter
in the fitter). No −300s shift: within gaps are NOT left-truncated at eps
(`fit_chunk.R`'s `GAP_SHIFT` only touches `col == "gap"`).

## Fitting (`distribution-fit/fit_chunk.R`, unmodified methodology)

Same 8-candidate MLE battery as `distribution-fit` (natural support, no free
loc, closed-form KS/CvM/AD, AIC selection), run with
`data_dir=inter-post-creation/data out_dir=inter-post-creation/results`.
`build_best.py` = step2 analog, one change: the n_obs ≥ 30 filter is applied
**per column independently** (requiring both would restrict global to heavy
posters and bias its composition).

### Results (n_obs ≥ 30)

| family | within (65,311 users) | global (176,217 users) |
|---|---|---|
| lognorm | 46.3% | 47.2% |
| gamma | 22.7% | 0.06% |
| fisk | 10.1% | 8.3% |
| power_tail | 9.4% | **23.8%** |
| weibull_min | 9.3% | 20.7% |
| expon | 2.2% | ~0% |

Magnitudes: within median **87s**, p99 666s (the session ceiling); global
median **599s**, mean 4.3h, p99 2.6 days.

## Finding 1 — within-session families are unidentifiable *in principle*

Within gaps are bounded by session duration (median session: 109s). On such a
window the light-tailed candidates are likelihood-near-identical:

- **ΔAIC < 2** (coin-flip zone) for **56.9%** of within users (median margin
  1.6); nearly all ambiguity is cross-family. AIC-best == AD-best only 18.3%
  (25% at family level). Global is healthier: 48% ΔAIC<2 but only 24.8%
  cross-family (the rest is power_tail sibling shuffling, absorbed by the
  family grouping).
- **`validate_truncated.R`** mirrors the observation process: simulate each
  family, keep draws below a session-like ceiling T, fit with the same
  untruncated candidates. At T ≤ 300s light-tailed data is AIC-classified as
  power_tail **40–52%** of the time — truncation *manufactures* power_tail
  wins (this is the within "all users" column's 61.7% power_tail artifact).
  Light families only become distinguishable at T ≥ 900s; within gaps have
  p90 = 281s, so essentially the whole column lives in the unidentifiable
  regime.

Consequence: **no family composition is reported for within**. The column is
described by shape (quantiles) only. Global carries family identity (wide
support, validated recovery in `distribution-fit/validate.R`).

## Finding 2 — posting is session-modulated (independence replay test)

Question for the simulator: is the within distribution just the global process
viewed through session windows? If yes, no separate within model is needed.

**`independence_test.py`** (5,000 users sampled from the ≥30-global pool;
4,612 with ≥5 real within gaps). Two replays against the user's REAL session
intervals:

1. **iid replay**: bootstrap the user's own empirical global gaps into a new
   stream (t0 = first real post). → within-pair **count ratio real/synth:
   median 18×**; synth produces < half of real for 100% of users; only 11% of
   users get ≥5 synthetic within pairs.
2. **Shift replay**: real gaps in real order, random circular time offset —
   destroys only the post↔session alignment, preserves all serial structure.
   → same deficit (**19×**). Not burstiness; the coupling is the alignment
   itself.

But among comparable pairs, **lengths match**: per-user median(real)/median
(synth) = 1.02, p75 identical. Conclusion: posting is session-modulated in
**rate**, not in gap **shape**. The within-session cadence is its own process
and must be modeled as such.

## Decision — empirical (ECDF) sampling for the simulator

Three routes were considered:

1. **Session-independent posts in the sim** (sample global, let the session
   gate produce within) — **disproved** by Finding 2 (~18× too few
   within-session posts).
2. **Truncated-likelihood fits** (per-pair ceiling = remaining session time) —
   restores identifiability in principle, but requires a custom fitting
   pipeline; rejected as out of scope.
3. **ECDF sampling** — chosen. The empirical within distribution is exactly
   the truncated quantity the simulator must reproduce; 11.1M observations
   make the ECDF essentially exact, and no extrapolation beyond the session
   ceiling is ever needed. Same principle already used for parameter sampling
   (`METHODOLOGY.md` caveats 3–4 in `distribution-fit`: sample empirical rows,
   not fitted marginals).

Sim-side mechanics already exist: create events chain within a session and
the staleness gate drops creates that land past session end — the rejection
rule is free.

**Exports** (`export_ecdf.py`, `export_ecdf_by_family.py`; one gap per line,
seconds, plain text like the other `params/*.txt`):

- `results/within_interpost_ecdf.txt` — pooled, 1M uniform subsample of all
  11.1M within gaps (p25 33s / p50 86s / p90 281s / p99 663s).
- `results/within_ecdf__<family>.txt` — per-GLOBAL-family ECDFs (within gaps
  pooled over users whose global fit has n_obs ≥ 30; ≤250k lines each).

**Per-family heterogeneity (Finding 2 follow-up).** Within cadence differs by
the user's global family — median within gap: lognorm 118s, power_tail 76s,
weibull 71s, fisk 60s (~2× spread; heavy-tailed-global users are burstier
when online, lognorm users steady-and-slower). Hence the per-family exports:
the sim assigns each user a global family, then loads the matching within
ECDF. gamma (36 users) and expon (4 users) pools are too thin to sample —
map those users to the pooled file or a neighboring big family.

**bskysim changes (hand-written, not done here):** add `ecdf` to the
`ContinuousDistribution` union in the `distributions` package (1 line;
`ECDF.zig` already implements sample/format/jsonParse — it currently sits in
`DiscreteDistribution` only), load the per-family ECDF files, and point
`User.inter_creation_time` at the ECDF matching the user's global family.
`events.zig` needs no changes — same `.sample(rng)` API, and the staleness
gate remains the truncation rule. Optional upgrade if pooled/per-family
proves too coarse: K archetype ECDFs binned by user mean within gap.

## Files

| file | content |
|---|---|
| `dump.py` | DB → parquet chunks (did, col, value); both columns |
| `build_best.py` | AIC-best per (did, col), per-column n≥30 filter, summaries |
| `validate_truncated.R` | truncation-regime confusion matrix (Finding 1) |
| `independence_test.py` | iid + shift replay test (Finding 2) |
| `export_ecdf.py` | pooled within-ECDF export (1M subsample) |
| `export_ecdf_by_family.py` | per-global-family within-ECDF exports |
| `data/chunk{0-9}.parquet` | raw per-user gaps |
| `results/gof__chunk{0-9}.tsv`, `params__chunk{0-9}.tsv` | all fits |
| `results/best_per_user.tsv`, `best_params.tsv` | AIC winners + params |
| `results/family_summary.tsv` | composition per col (all / n≥30) |
| `results/pair_summary.tsv` | within × global family crosstab (both ≥30) |
| `results/independence_ecdf.png` | real vs replay within ECDF overlay |
| `results/within_interpost_ecdf.txt` | pooled within ECDF for the sim |
| `results/within_ecdf__{family}.txt` | per-global-family within ECDFs |

## Caveats

1. Within composition numbers (the left column above) are kept for the record
   but are **not identifiable** — see Finding 1; do not cite as family shares.
2. Replay caveat: real sessions are built from all events *including* posts,
   so windows and post times are mildly coupled by construction. Likes
   dominate events (~5:1 over posts), and the shift replay controls for serial
   structure, so the 18× deficit is attributed to rate modulation, not
   window-formation artifacts.
3. Singleton sessions (40.6% of sessions) can contain at most one post →
   contribute no within pairs.
4. Exact same-μs post bursts (124k) are excluded; sub-1s gaps otherwise
   nonexistent.
5. Global fits inherit `distribution-fit` caveats (n=1 units unfittable,
   power_tail sibling arbitrariness absorbed by the family grouping).
