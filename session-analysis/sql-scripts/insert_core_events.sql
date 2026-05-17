-- =============================================================================
-- Populate pau_db.user_core_events
-- =============================================================================
-- Run create_core_events_table.sql first.
--
-- Inserts three event types for every user:
--   1. 'post'   – top-level posts (original content, including quote posts)
--   2. 'reply'  – replies to other posts (conversation engagement)
--   3. 'repost' – reposts / retweets (content amplification)
--
-- Quote posts are just app.bsky.feed.post records with an embedded reference;
-- they are NOT a separate protocol action. They naturally fall into 'post'
-- or 'reply' depending on whether they have a reply parent.
-- =============================================================================

INSERT INTO pau_db.user_core_events (did, time_us, event_type)

-- 1. Top-level posts (original content, NOT replies)
--------------------------------------------------------------------------------
SELECT did,
       time_us,
       'post' AS event_type
FROM bsky.posts
WHERE reply_root_uri IS NULL

UNION ALL

-- 2. Replies (posts that are part of a reply chain)
--------------------------------------------------------------------------------
SELECT did,
       time_us,
       'reply' AS event_type
FROM bsky.posts
WHERE reply_root_uri IS NOT NULL

UNION ALL

-- 3. Reposts (retweets / amplifications)
--------------------------------------------------------------------------------
SELECT did,
       time_us,
       'repost' AS event_type
FROM bsky.records
WHERE collection = 'app.bsky.feed.repost'
  AND operation   = 'create';
