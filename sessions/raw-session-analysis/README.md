# Session definition & validation

## What is a session?

> A session is an interval of continuous time in which the user is engaging
> with any feature of the platform.

This is a **behavioural** definition, not an arbitrary timeout.  It captures the
full spectrum: a 2-second notification check, a 5-minute scroll through the feed,
a 30-minute reply thread.  If the user was actively doing *something*, it's a session.

## Validation criteria

A good session-clustering method must pass five checks:

### 1. Singleton acceptance

Micro-engagements are real.  Checking a notification, liking one post, then leaving
is a legitimate session.  A method that silently drops these is hiding real behaviour.
Singletons should be *visible and tagged*, not discarded.

### 2. Upper-bound coherence

8-hour sessions are not browsing — they're the user leaving the tab open.
A good method produces sessions that cluster under a few hours.
2+ hours is plausible for deep work / doomscrolling; 8+ is not.

### 3. Circadian validation (by language)

Session start/end times should follow human waking hours for the user's timezone.
We validate this per-language:
- DE → CET (UTC+1/+2) → peaks 7–22h local
- EN → US East/West → peaks 8–23h local
- JA → JST (UTC+9) → peaks 7–23h local
- ES → LATAM/ES → peaks 8–23h local

If a method produces uniform (non-circadian) distributions, it's failing.

### 4. Distributional fit

The session-duration distribution should follow a known heavy-tailed form
(lognormal, power-law, or Weibull).  If it's uniform or unstructured,
the method is producing random boundaries.

### 5. Parameter stability

Small changes in hyperparameters should not flip the results.
A good operating point sits on a **plateau**:
- ±20% in ε shouldn't change median duration by >2x
- ±20% in k (Tukey) shouldn't change solo rate drastically

## Methods compared

| Method | Knobs | Noise handling |
|--------|-------|---------------|
| Tukey (per-user IQR) | k (1.2, 1.5, 1.7) | Produces 0-duration sessions for isolated events |
| HDBSCAN | ε (30–300s), min_samples (1–10) | Density-certified noise → tagged singletons |

## Output tables

All results in `pau_db.sessions_raw_{method}_{params}` with columns:
- `did`, `session_start`, `session_end`, `duration_s`
- `is_singleton` — 1 if HDBSCAN noise or Tukey solo event, 0 otherwise

## Running

```bash
# Cluster
uv run session-creation/create-sessions.py all_tukey --did-from-file sample_dids.txt
uv run session-creation/create-sessions.py all_hdbscan --hdbscan-epsilon 120 --summary

# Analyze
uv run raw-session-analysis/main.py
```
