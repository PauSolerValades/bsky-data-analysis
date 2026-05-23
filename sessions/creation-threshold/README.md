# session-creation-threshold

Self-contained pipeline: Bluesky firehose → `pau_db.sessions_threshold`.

**Method:** Fixed-threshold session clustering (Kooti et al., SocInfo 2016).
All users share the same gap boundary — two events ≤ Δt apart belong to the same
session. The threshold is discovered empirically via the Kneedle elbow algorithm
on the inter-arrival gap histogram of the dominant user stratum (101–500 events).

Only three event types are used (posts, replies, reposts) — no likes, follows,
or other record types.

---

## Quick start

```bash
# Prerequisite: user_core_events must exist
#   (run sessions/creation-tukey/01_core_events.sql if you haven't)

# 1. Filter to dominant stratum (101–500 events, 96K users)
mysql -h 10.18.74.14 -P 9030 -u pau -p < 01_core_events_dominant.sql

# 2. Detect threshold via elbow method → Δt = 265 s
uv run 02_detect_threshold.py

# 3. Cluster into sessions → pau_db.sessions_threshold
uv run 03_cluster_fixed.py --summary
```

---

## Files

| File | What |
|------|------|
| `01_core_events_dominant.sql` | CREATE + INSERT `pau_db.user_core_events_dominant` — 101–500 event bucket (96K users). Filters from `user_core_events`. |
| `02_detect_threshold.py` | Kneedle elbow on the dominant stratum's inter-arrival gap histogram. Prints the threshold (265 s / 4.4 min) and saves a plot to `results/`. |
| `03_cluster_fixed.py` | Clusters each user's events into sessions using the fixed threshold. Reads from `user_core_events_dominant`, writes `pau_db.sessions_threshold`. |

---

## Parameters

| Parameter | Default | Where it comes from |
|-----------|---------|---------------------|
| Dominant range | 101–500 events | EDA §5 — this bucket supplies 37.4% of all gaps |
| Threshold | 265 s (4.4 min) | `02_detect_threshold.py` (Kneedle on dominant stratum) |

---

## Output table

```
pau_db.sessions_threshold
├── did, session_start, session_end, next_session_start
├── duration_s
├── reposts
├── posts_authored
└── threshold_s               — always 265.0
```

---

## Regeneration

```bash
mysql -h 10.18.74.14 -P 9030 -u pau -p -e "TRUNCATE TABLE pau_db.sessions_threshold;"
uv run 03_cluster_fixed.py --summary
```

`01_core_events_dominant.sql` is idempotent — run once.
