-- 02_compute_post_lifetime.sql
-- Computes pau_db.post_lifetime from cascade_edges + cascades.
--
-- This is the SAME definition as the simulation-side query
-- (des-ctic/dataset/queries/post_lifetime.sql.tmpl), adapted to StarRocks
-- table/column names (post_uri, time_us). Keep the two files in sync.
--
-- Run on server:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p pau_db < 02_compute_post_lifetime.sql
-- Runs locally against DuckDB unchanged via running_locally.local_db
-- (sqlglot transpiles it).

INSERT INTO pau_db.post_lifetime
WITH
raw AS (
  SELECT
    e.post_uri,
    c.author_did,
    c.creation_time_us,
    e.time_us AS repost_time
  FROM pau_db.cascade_edges e
  JOIN pau_db.cascades c ON e.post_uri = c.post_uri
),

-- Running cumulative repost count per post
cumulative AS (
  SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY post_uri ORDER BY repost_time) AS cum_rep,
    COUNT(*)     OVER (PARTITION BY post_uri)                      AS total_rep
  FROM raw
),

-- T_50, T_95, T_99: first repost_time where cumulative reaches the percentile
percentiles AS (
  SELECT
    post_uri,
    MAX(author_did)       AS author_did,
    MAX(creation_time_us) AS creation_time_us,
    MAX(repost_time)      AS last_repost_time_us,
    MAX(total_rep)        AS total_reposts,
    -- T_50: time of the repost at position ceil(50% * total)
    MIN(CASE WHEN cum_rep >= CEIL(total_rep * 0.50) THEN repost_time END) AS T_50,
    MIN(CASE WHEN cum_rep >= CEIL(total_rep * 0.95) THEN repost_time END) AS T_95,
    MIN(CASE WHEN cum_rep >= CEIL(total_rep * 0.99) THEN repost_time END) AS T_99
  FROM cumulative
  GROUP BY post_uri
),

-- Time to peak: first repost_time of the bin_idx with the most reposts.
-- Bin index clamped to 99 (FLOOR alone would put the last repost in a
-- spurious bin 100).
bins AS (
  SELECT
    post_uri,
    LEAST(FLOOR((repost_time - creation_time_us) / NULLIF(last_repost - creation_time_us, 0) * 100), 99) AS bin_idx,
    repost_time
  FROM (
    SELECT
      r.*,
      MAX(repost_time) OVER (PARTITION BY r.post_uri) AS last_repost
    FROM raw r
  ) sub
  WHERE last_repost > creation_time_us
),
peak AS (
  SELECT post_uri, bin_idx, COUNT(*) AS bin_count
  FROM bins
  GROUP BY post_uri, bin_idx
),
best AS (
  SELECT post_uri, MAX(bin_count) AS max_count
  FROM peak
  GROUP BY post_uri
),
-- Tie-break: lowest bin wins
peak_bin AS (
  SELECT p.post_uri, MIN(p.bin_idx) AS bin_idx
  FROM peak p
  JOIN best b ON p.post_uri = b.post_uri AND p.bin_count = b.max_count
  GROUP BY p.post_uri
),
peak_time AS (
  SELECT
    b.post_uri,
    MIN(b.repost_time) AS time_to_peak
  FROM bins b
  JOIN peak_bin p ON b.post_uri = p.post_uri AND b.bin_idx = p.bin_idx
  GROUP BY b.post_uri
)

SELECT
  p.post_uri,
  p.author_did,
  p.creation_time_us,
  p.last_repost_time_us,
  p.total_reposts,
  p.T_50 - p.creation_time_us AS T_50_us,
  p.T_95 - p.creation_time_us AS T_95_us,
  p.T_99 - p.creation_time_us AS T_99_us,
  -- 0 when there is a single repost or all reposts share one timestamp
  CASE WHEN p.total_reposts <= 1 THEN 0
       ELSE COALESCE(pt.time_to_peak - p.creation_time_us, 0)
  END AS time_to_peak_us
FROM percentiles p
LEFT JOIN peak_time pt ON p.post_uri = pt.post_uri
WHERE p.total_reposts > 0
ORDER BY p.post_uri;
