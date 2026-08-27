-- Dump post creations + reposts for cascade tree reconstruction.
-- Reads from pau_db.actions (built by eda-raw-dataset/create_actions_table.py),
-- the firehose "action trace" with the same user-side clock as sessions.
-- Cascade roots are TOP-LEVEL posts only: reposts of replies are subcascades
-- and are filtered out (their subject_uri is not a post_top uri).
--
-- Run from the command line:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p'...' -N -B < 01_dump_reposts.sql > cascades.tsv
--
-- Columns (tab-separated):
--   subject_uri   – AT URI of the original post (identifies which cascade)
--   repost_uri    – AT URI of this repost record (\N for creation events)
--   via_uri       – AT URI of the repost this user saw (\N for creation / direct)
--   actor_did     – DID of the actor (author or reposter)
--   time_us       – Action timestamp in microseconds (user-side created_at clock)
--   is_repost     – 0 for creation, 1 for repost (ensures creation sorts first on tie)

SELECT
    subject_uri,
    NULL   AS repost_uri,
    NULL   AS via_uri,
    did    AS actor_did,
    time_us,
    0      AS is_repost
FROM pau_db.actions
WHERE event_type = 'post_top'

UNION ALL

SELECT
    subject_uri,
    CONCAT('at://', did, '/app.bsky.feed.repost/', rkey) AS repost_uri,
    via_uri,
    did    AS actor_did,
    time_us,
    1      AS is_repost
FROM pau_db.actions
WHERE event_type = 'feed_repost'
  AND time_us > 0
  AND subject_uri IN (SELECT subject_uri FROM pau_db.actions WHERE event_type = 'post_top')

ORDER BY subject_uri, time_us, is_repost;
