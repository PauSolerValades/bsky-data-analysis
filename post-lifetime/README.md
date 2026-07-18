# Post Lifetime

Analysis of post engagement timing: how long posts stay alive, when they peak,
and how reposts propagate through the social graph.

## Data

Reads from `../cascade-metrics/results/` (produced by `cascade-creation/`):

| File | Contents |
|---|---|
| `post_lifetime.parquet` | Per-post: T_50, T_95, T_99, time_to_peak (deltas from creation) |
| `repost_gaps.parquet` | Per-repost: global_gap, topology_gap, parent_did |

## Quick SQL (DuckDB)

```sql
-- T_50 distribution: how fast do cascades reach half their reposts?
SELECT
    CASE
        WHEN T_50_us < 60e6     THEN '< 1 min'
        WHEN T_50_us < 3600e6   THEN '1 min – 1 hr'
        WHEN T_50_us < 86400e6  THEN '1 hr – 1 day'
        WHEN T_50_us < 604800e6 THEN '1 day – 1 week'
        ELSE '> 1 week'
    END AS T_50_bucket,
    COUNT(*) AS posts
FROM read_parquet('../cascade-metrics/results/post_lifetime.parquet')
WHERE total_reposts >= 2
GROUP BY T_50_bucket ORDER BY MIN(T_50_us);

-- Topology gaps: how long between reposts from the same parent?
SELECT
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY topology_gap_us)/1e6 AS p50_gap_s,
    PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY topology_gap_us)/1e6 AS p90_gap_s,
    AVG(topology_gap_us)/1e6 AS avg_gap_s
FROM read_parquet('../cascade-metrics/results/repost_gaps.parquet')
WHERE topology_gap_us >= 0;

-- Time to peak by cascade size
SELECT
    CASE
        WHEN total_reposts BETWEEN 2 AND 5 THEN '2-5'
        WHEN total_reposts BETWEEN 6 AND 20 THEN '6-20'
        WHEN total_reposts BETWEEN 21 AND 100 THEN '21-100'
        ELSE '100+'
    END AS bucket,
    COUNT(*) AS n,
    MEDIAN(time_to_peak_us)/1e6 AS median_peak_s
FROM read_parquet('../cascade-metrics/results/post_lifetime.parquet')
WHERE total_reposts >= 2
GROUP BY bucket ORDER BY MIN(total_reposts);
```
