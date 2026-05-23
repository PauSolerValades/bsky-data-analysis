# Exploratory Data Analysis: Bluesky Firehose Coverage

## Overview

The `bsky_topology.graph_events` table in StarRocks was built from Bluesky
firehose JSONL exports stored on NFS at:

```
/data/nfs/datasets/bluesky/firehose/non-posts/YYYY-MM/DD/records_*.jsonl
```

This document catalogs what data is present, what's missing, and why.

## Data Range

| Property | Value |
|----------|-------|
| Earliest event | 2025-02-03 |
| Latest event | 2026-05-12 |
| Calendar days in range | 464 |
| Days with data | 410 |
| Coverage | **88.4%** |

## Known Gaps

Two collection outages were identified from the directory tree (no file reads
needed — just directory listings via `check_gaps.py`).

| # | Start | End | Duration | Likely cause |
|---|-------|-----|----------|---------------|
| 1 | 2025-07-17 | 2025-08-31 | **46 days** | Firehose collector down for ~7 weeks |
| 2 | 2026-03-25 | 2026-04-01 | **8 days** | Shorter outage, ~1 week |

**Total missing: 54 days** (11.6% of the calendar range).

The full list of missing dates is in `missing_days.csv` (one date per row).

## Monthly Breakdown

| Month | Days present | Days in range | Missing |
|-------|-------------|---------------|---------|
| 2025-02 | 26 | 26 | 0 |
| 2025-03 | 31 | 31 | 0 |
| 2025-04 | 30 | 30 | 0 |
| 2025-05 | 31 | 31 | 0 |
| 2025-06 | 30 | 30 | 0 |
| **2025-07** | **16** | 31 | **15** |
| **2025-08** | **0** | 31 | **31** |
| 2025-09 | 30 | 30 | 0 |
| 2025-10 | 31 | 31 | 0 |
| 2025-11 | 30 | 30 | 0 |
| 2025-12 | 31 | 31 | 0 |
| 2026-01 | 31 | 31 | 0 |
| 2026-02 | 28 | 28 | 0 |
| **2026-03** | **24** | 31 | **7** |
| **2026-04** | **29** | 30 | **1** |
| 2026-05 | 12 | 12 | 0 |

## Impact on the Social Graph

The SCD2 tables (`follow_edges`, `block_edges`) built from this data are
affected as follows:

| Scenario | Correct? | Notes |
|----------|-----------|-------|
| Edge created before gap, deleted after gap | ✅ | `valid_from` and `valid_to` both captured |
| Edge created before gap, still active after gap | ✅ | `valid_from` captured, `valid_to = NULL` correct |
| Edge created AND deleted within a gap | ❌ | **Lost forever** — never recorded |
| Edge created before gap, deleted DURING gap | ❌ | `valid_to = NULL` looks active but isn't |
| Edge created DURING gap, deleted after gap | ❌ | Edge missing entirely |
| Users who only appeared during a gap | ❌ | **Lost forever** — missing from `users` table |

### Mitigation

For graph analyses sensitive to the gap periods:

- **Time-travel queries at a specific point-in-time** (e.g., "who did Alice
  follow on 2025-08-15?") will look at edges with `valid_from <= '2025-08-15'`
  and `valid_to > '2025-08-15' OR valid_to IS NULL`. Edges that were deleted
  during the gap will incorrectly appear as still active.
- **Growth/retention analyses** spanning the gap should note that any
  unfollows from July-August 2025 are undercounted, and any entirely new
  edges from that period are absent.
- **Degree counts** (followers, following) at dates during the gap will be
  biased high (edges that should have been closed remain open).

## Reproducibility

To re-check for gaps or verify against new data:

```bash
python3 topology-time-reconstruction/process-data/check_gaps.py
```

Outputs:
- Console: gap summary and monthly breakdown
- `missing_days.csv`: every missing date, one per row

Reads only directory names — no file I/O, no StarRocks queries. Runs in
under a second.
