-- topology-crawl/sql-scripts/insert_firehose_follows.sql
-- =============================================================================
-- Extracts all distinct (follower → followee) edges from the Bluesky firehose
-- (bsky.records, collection = 'app.bsky.graph.follow') and inserts them into
-- pau_db.follow — but only edges where BOTH users are in our 3M-user set.
--
-- This gives us the follow topology that was directly observed in the firehose
-- snapshot, without making a single API call.  The API crawler can then fill
-- in users whose follows happened outside the capture window.
--
-- Note: pau_db.follow is a DUPLICATE KEY table, so re-running this script is
-- safe — it will append duplicates (deduplicate later if needed).
--
-- Run:
--   mysql -h 10.18.74.14 -P 9030 -u pau -p < topology-crawl/sql-scripts/insert_firehose_follows.sql

USE pau_db;

-- ── Before/after sanity check ───────────────────────────────────────────────

SELECT 'edges before' AS stage, COUNT(*) AS cnt FROM follow
UNION ALL
SELECT 'distinct edges before', COUNT(DISTINCT CONCAT(follower_did, '|', followee_did)) FROM follow;

-- ── Insert all firehose follow edges (both users in pau_db.users) ───────────

INSERT INTO follow (follower_did, followee_did)
SELECT DISTINCT r.did, r.subject_did
FROM bsky.records r
JOIN pau_db.users u1 ON r.did = u1.did
JOIN pau_db.users u2 ON r.subject_did = u2.did
WHERE r.collection = 'app.bsky.graph.follow';

-- ── After counts ────────────────────────────────────────────────────────────

SELECT 'edges after' AS stage, COUNT(*) AS cnt FROM follow
UNION ALL
SELECT 'distinct edges after', COUNT(DISTINCT CONCAT(follower_did, '|', followee_did)) FROM follow
UNION ALL
SELECT 'distinct followers after', COUNT(DISTINCT follower_did) FROM follow
UNION ALL
SELECT 'distinct followees after', COUNT(DISTINCT followee_did) FROM follow;
