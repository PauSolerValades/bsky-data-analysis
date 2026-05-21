-- Dump all repost events for cascade tree reconstruction.
-- Run from the command line:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p'...' -N -B < dump_reposts.sql > results/reposts.tsv
--
-- Columns (tab-separated):
--   subject_uri   – AT URI of the original post (identifies which cascade)
--   repost_uri    – AT URI of this repost record (used as parent key for via reposts)
--   via_uri       – AT URI of the repost this user saw; NULL if direct
--   actor_did     – DID of the user who reposted
--   time_us       – Firehose event timestamp (microseconds)

SELECT
    subject_uri,
    CONCAT('at://', did, '/app.bsky.feed.repost/', rkey) AS repost_uri,
    via_uri,
    did AS actor_did,
    time_us
FROM bsky.records
WHERE collection  = 'app.bsky.feed.repost'
  AND operation   = 'create'
  AND time_us     > 0
  AND subject_uri IS NOT NULL
ORDER BY subject_uri, time_us;
