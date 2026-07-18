-- Dump post creations + reposts for cascade tree reconstruction.
-- Run from the command line:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p'...' -N -B < 01_dump_reposts.sql > cascades.tsv
--
-- Columns (tab-separated):
--   subject_uri   – AT URI of the original post (identifies which cascade)
--   repost_uri    – AT URI of this repost record (\N for creation events)
--   via_uri       – AT URI of the repost this user saw (\N for creation / direct)
--   actor_did     – DID of the actor (author or reposter)
--   time_us       – Firehose event timestamp (microseconds)
--   is_repost     – 0 for creation, 1 for repost (ensures creation sorts first on tie)

SELECT
    CONCAT('at://', did, '/app.bsky.feed.post/', rkey) AS subject_uri,
    NULL   AS repost_uri,
    NULL   AS via_uri,
    did    AS actor_did,
    time_us,
    0      AS is_repost
FROM bsky.posts

UNION ALL

SELECT
    subject_uri,
    CONCAT('at://', did, '/app.bsky.feed.repost/', rkey) AS repost_uri,
    via_uri,
    did    AS actor_did,
    time_us,
    1      AS is_repost
FROM bsky.records
WHERE collection  = 'app.bsky.feed.repost'
  AND operation   = 'create'
  AND time_us     > 0
  AND subject_uri IS NOT NULL

ORDER BY subject_uri, time_us, is_repost;
