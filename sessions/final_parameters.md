# Final sessionization parameters: DBSCAN (eps = 300s, min_samples = 2)

**Decision:** user sessions are produced by DBSCAN on 1D per-user event timestamps with
`eps = 300` seconds and `min_samples = 2`. Isolated events (no other event within 300s)
are stored as flagged singletons (`is_singleton = 1`).

**Production table:** `pau_db.sessions`
**Per-user summary:** `pau_db.sessions_users`

All evidence below was computed on a 5% DID sample (`pau_db.events_sample`, seed 42,
106,392 users, 11.8M events) before the full-data run.

---

## 1. Problem framing

Sessionization is 1D temporal clustering per user: every method reduces to deciding
*which gaps between consecutive events mean "the user left"*. Methods are compared on:

- **C1** plausible session durations (minutes, not hours)
- **C2** no degenerate mega-sessions (a "session" spanning 8h is offline time mislabeled as online)
- **C3** honest treatment of isolated events (flagged, not silently counted as sessions)
- **C4** stability: small parameter change ⇒ small output change
- **C5** interpretable parameters (each knob defensible in one sentence)

Structural fact: on 1D sorted timestamps, the classic fixed-gap threshold is exactly
DBSCAN with `min_samples = 1` (density-reachability in 1D is gap chaining). Verified
numerically: DBSCAN clusters reproduce the fixed method's positive-duration sessions to
the decimal (see §4). So the "fixed threshold" baseline is DBSCAN with noise detection
turned off — not a separate method.

## 2. Eliminated methods

### Tukey IQR (per-user adaptive threshold) — fails C2, C5

Per-user fence Q3 + k·IQR over inter-event gaps. For heavy/regular users the IQR is
large, the fence becomes hours, and nothing splits:

| k | sessions | mean duration | %>8h |
|---|---|---|---|
| 1.2 | 1.81M | 6594s | 3.03% |
| 1.5 | 3.38M | 7669s | 3.35% |
| 1.7 | 1.63M | 8340s | 3.54% |

Source: `hyperparameter/plots/tukey/k*/duration_metrics__*.tsv` (old sample).
Capping the fence fixes the tail, but capped Tukey is the fixed method with an extra
estimation step — unjustifiable complexity (C5). Dropped.

### HDBSCAN — fails C2, C3, C5

Full 36-combo grid (`mcs ∈ {2,3,5} × ms ∈ {1,2,3} × ε ∈ {0,60,180,300}`):

- `ms ≥ 2` or `mcs ≥ 3`: **28–52% of all sessions are singletons** — up to half of all
  user activity declared "not a session" (fails C3).
  Source: `hyperparameter/plots/hdbscan/*/duration_metrics__*.tsv` (old sample).
- Only viable corner (`mcs=2, ms=1`), re-run on the new sample with ε up to 600:
  over-merging grows with ε — at ε=600, **11.4% of sessions exceed 1h, p99 ≈ 20h**
  (fails C2). `cluster_selection_epsilon` operates in mutual-reachability units, not
  seconds (fails C5).
  Source: `hyperparameter/plots_new/hdbscan/mcs2_ms1_e*/duration_metrics__*.tsv`.

## 3. DBSCAN parameter selection

### min_samples = 2

ms=1 disables noise detection (= fixed threshold). ms≥3 in 1D only erodes cluster
edges. ms=2 is the minimal noise-detecting value, defensible in one sentence:
*an event must have at least one other event within eps to belong to a session.*

### eps: two scales, two questions

**Burst scale (5–60s)** — swept once, closed. Sessions have median 3–35s with 52–80%
singletons: this is intra-burst (typing-speed) structure, not online/offline.
Source: `hyperparameter/plots_new/dbscan/e{5,15,30,60}_ms2/duration_metrics__*.tsv`.

**Session scale (300–600s)** — the stability plateau:

| table | sessions | %sing | %>1h | %>4h | median | mean | p99 |
|---|---|---|---|---|---|---|---|
| dbscan e300 | 2.25M | 40.6 | 0.07 | 0.00 | 109s | 230s | 1715s |
| dbscan e600 | 1.90M | 37.0 | 0.36 | 0.01 | 162s | 383s | 2932s |

Source: `hyperparameter/plots_new/dbscan/e{300,600}_ms2/duration_metrics__*.tsv`.
Above 600s over-merging begins (fixed sweep: 1.6–3.7% >1h at 1200–1800s). Between 60
and 300s lies a cliff (session counts 3.7M → 2.2M); any eps there fails C4.

### Short-session composition (both plateau points identical)

% of short (<60s) sessions containing N events —
source: `analysis/results/short_session_events.tsv` (script: `analysis/short_session_events.py`):

| table | 1 ev | 2 ev | 3–4 ev | 5–9 ev | 10+ ev |
|---|---|---|---|---|---|
| dbscan e300 | 63.2 | 21.6 | 11.3 | 3.5 | 0.5 |
| dbscan e600 | 63.4 | 21.4 | 11.2 | 3.5 | 0.5 |

Short DBSCAN sessions are honestly flagged isolated events and pairs — not hidden
dense activity (10+ event bursts ≤ 1%). HDBSCAN's short sessions, by contrast, are
mostly 2–4 event micro-clusters carved from sparse regions (56% at e300) — the same
gluing mechanism that produces its 20h tail.

### Inter-session gap distribution

Clean lower edge at exactly 300s — the eps cutoff working as intended:
`hyperparameter/plots_new/dbscan/e300_ms2/hist_gap__sessions_dbscan_e300_ms2.png`
(compare HDBSCAN e300, where noise points sit arbitrarily close to clusters:
`hyperparameter/plots_new/hdbscan/mcs2_ms1_e300/hist_gap__sessions_hdbscan_mcs2_ms1_e300.png`).

## 4. Stability evidence (C4)

Script: `analysis/stability.py`. Figures in `analysis/results/`.

**Plateau (e300 vs e600)** — `stability__sessions_dbscan_e300_ms2_vs_sessions_dbscan_e600_ms2.png`:
r = 0.975, median per-user relative diff = 0.000, 54.9% of users exactly identical,
P90 diff ≤ 0.25. The +18.5% sessions at e300 is the mechanical absorption of 300–600s
gaps — intended behavior, not instability.

**Cliff (e60 vs e300, contrast figure)** — `stability__sessions_dbscan_e60_ms2_vs_sessions_dbscan_e300_ms2.png`:
r = 0.902, exact matches collapse to 27.7%, median diff 0.231, +66.8% sessions.
Crossing into the burst regime reshapes per-user counts; moving within the plateau
does not.

**Internal consistency check:** DBSCAN e300/e600 clusters reproduce the fixed 300s/600s
positive-duration statistics to the decimal (median 109/162s, mean 230.2/382.8s,
p99 1715/2932s) — confirming DBSCAN ≡ fixed threshold + noise flagging, by construction.

## 5. Why eps = 300 and not 600

Both pass C1–C5; the choice is error asymmetry. A *split* creates two short adjacent
sessions (visible, correctable downstream). A *merge* stitches a real absence into
"online" time (invisible, corrupts every downstream duration statistic). The
conservative splitter is preferable: **eps = 300s** (median session 109s, 0.07% >1h,
40.6% of sessions flagged singletons).

## 6. Limitations

1. **Global threshold** — no per-user adaptation. This was Tukey's motivation; it
   failed empirically (§2). Stated as an explicit tradeoff.
2. **Same-second events** — produce duration-0 non-singleton sessions (clustering
   dedups to unique seconds); visible as part of the "2 ev" bucket in §3.
3. **Observation-window edges** — sessions censored at data boundaries.

---

*Reproduction: `table_creation/dbscan_cluster.py --table-name sessions --epsilon 300
--min-samples 2 --summary` (full data); sweeps in `hyperparameter/{dbscan,hdbscan}_sweep.py`;
analysis battery in `analysis/`.*
