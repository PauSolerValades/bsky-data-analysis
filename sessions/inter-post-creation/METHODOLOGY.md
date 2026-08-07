# Inter-post creation time — methodology

## Layout

- `dump.py`, `run_all.sh`, `data/` — shared data pipeline (per-user gap chunks).
- `1-pair-histograms/` — posts per session/minute, split by (duration, gap) pair.
- `2-parametric-fits/` — per-user and pooled parametric fits + why they fail
  (n too small, ΔAIC margins, AD collapse; see `POOLED_FIT.md` there).
- `3-ecdf/` — the chosen route: pooled and per-pair within-session ECDFs
  (txt exports + plots, no global overlay).
- `4-first-post-offset/` — next step: first-post offset vs the within-gap law.
- `posts-powerlaw/` — posts-per-user power-law analysis (previous work).
- `experiments/` — superseded "avoid the ECDF" attempts (permutation test,
  truncation validation, per-global-family ECDFs); kept for the record, see
  `PERMUTATION_TEST.md` there.

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
`data_dir=inter-post-creation/data out_dir=inter-post-creation/2-parametric-fits/results`.
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

## Finding 2 — posting is session-coupled (permutation test + replay probes)

Full write-up: **`PERMUTATION_TEST.md`**.

Question for the simulator: can the within column be *derived* from the
global column (which is easy to fit) plus the session gate? If yes, no
separate within model is needed. H0: the post stream is independent of the
session windows — within gaps are just global gaps that happen to land
inside windows.

**Formal test (`independence_test.py`)** — permutation test on the
post↔session alignment:

- **Statistic**: per-user within-session pair count, one-sided (real > null).
- **Null**: R = 999 random circular time-shifts of the user's REAL post
  stream. Gaps, order, burstiness, and windows are all preserved; only the
  alignment is destroyed — this is the exact permutation for H0.
- **Result** (1,000 users from the ≥30-global pool, 920 with ≥5 real within
  pairs): **100% of users reject H0** at the resolution limit p ≤ 0.001;
  Fisher combined χ² = 12,679 on 1,840 df. Observed count / median null
  count: **21×**.

**Descriptive probes** (decompose the rejection; not part of the formal test):

1. **iid replay** (bootstrap own global gaps, replay through real windows):
   same ~19× count deficit → not an artifact of destroying serial structure.
   But among gate-surviving pairs, per-user median lengths match (ratio
   1.02). Caveat: this shape match is near-tautological — the gate selects
   the short component of the global mixture, which IS the within component
   (see ontology note below). The test's real content is the count rejection.
2. **Conditional-truncation replay** (the "within = truncated global" repair:
   inside each session, sample global gaps conditioned on ≤ remaining time):
   **rejected in the opposite direction** — ~3× too many pairs (ratio 0.3),
   gaps ~2× too short (median 49s vs real 95s; per-user median ratio 2.03).
   Repeated conditioning on shrinking remaining time over-weights the short
   end of the global law — a bias real posting doesn't have. No rate
   multiplier fixes this; it would worsen the count surplus.

Conclusion: within-session cadence is its **own law** — neither the global
process gated by windows nor the global law truncated by them. It must be
modeled as such (ECDF, below).

### Ontology note (which column is "real"?)

Neither. Users can only post while online, so the within cadence is
generative and the global column is a two-component **mixture** (within gaps
+ cross-session gaps = end-of-session censor + offline wait + next-session
offset), which is also why single-family global fits are a compression of a
mixture. The global column survives only as a user-level heterogeneity
*label* (which per-family ECDF to load). Conversely, the global
counterfactual was necessary to *prove* the coupling exists — rejecting the
session-blind model is what justifies the two-component design over the
cheaper one-component one.

## Decision — empirical (ECDF) sampling for the simulator

Three routes were considered:

1. **Session-independent posts in the sim** (sample global, let the session
   gate produce within) — **disproved** by Finding 2 (permutation test,
   p ≤ 0.001 for 100% of users; ~19× too few within-session posts).
2. **Conditional-truncation sampling** (sample global gaps conditioned on
   ≤ remaining session time) — **disproved** by the Finding-2 replay: ~3× too
   many posts, gaps ~2× too short.
3. **Truncated-likelihood fits** (per-pair ceiling = remaining session time,
   e.g. fitdistrplus + truncdist) — restores identifiability in principle,
   but the ceiling varies per pair (remaining session time, not a fixed b),
   requiring a custom likelihood, and estimates stay high-variance with the
   ceiling on the bulk (Finding 1 regime); rejected as out of scope.
4. **ECDF sampling** — chosen. The empirical within distribution is exactly
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

- `3-ecdf/ecdfs/within_interpost_ecdf.txt` — pooled, 1M uniform subsample of all
  11.1M within gaps (p25 33s / p50 86s / p90 281s / p99 663s).
- per-GLOBAL-family ECDFs (within gaps pooled over users whose global fit has
  n_obs ≥ 30; ≤250k lines each) — superseded, see `experiments/`.

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
| `plots/independence_ecdf.png` | real vs replay within ECDF overlay |
| `3-ecdf/ecdfs/within_interpost_ecdf.txt` | pooled within ECDF for the sim |
| `3-ecdf/ecdfs/within_ecdf__{dur}__{gap}.txt` | per-pair within ECDFs |
| `3-ecdf/ecdfs/offset_ecdf__{dur}__{gap}.txt` | per-pair first-post offset ECDFs |

## Caveats

1. Within composition numbers (the left column above) are kept for the record
   but are **not identifiable** — see Finding 1; do not cite as family shares.
2. Replay caveat: real sessions are built from all events *including* posts,
   so windows and post times are mildly coupled by construction. Likes
   dominate events (~5:1 over posts), and the permutation null preserves
   windows, gap order and all serial structure, so the rejection is
   attributed to the alignment (rate modulation), not window-formation
   artifacts. The conditional replay's first-post-at-session-start
   convention is conservative (fixing it only increases its surplus).
3. Singleton sessions (40.6% of sessions) can contain at most one post →
   contribute no within pairs.
4. Exact same-μs post bursts (124k) are excluded; sub-1s gaps otherwise
   nonexistent.
5. Global fits inherit `distribution-fit` caveats (n=1 units unfittable,
   power_tail sibling arbitrariness absorbed by the family grouping).
