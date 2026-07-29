# EDA — Bluesky Firehose Raw Dataset

Exploratory analysis of the raw Bluesky firehose dump (April 11–18, 2026). Reads directly from the `bsky.records` and `bsky.posts` tables in StarRocks.

## Scripts

**`main.py`** is an EDA which:
1. computes distinct number of users and posts.
2. percentages of events (how many of each)
3. 

## Source tables

| Table | Rows | Description |
|-------|------|-------------|
| `bsky.records` | ~212.5M | All AT Protocol record events (`create`, `update`, `delete`) |
| `bsky.posts` | ~28.1M | Normalized post content (text, language, reply chain) |


## Events table

To build a unified events table — one row per action, one user-per-timestamp view of
the entire firehose — you merge the two source tables:

```
bsky.records  (WHERE collection != 'app.bsky.feed.post')
∪
bsky.posts    (annotated as post_top / post_reply)
```

### Resulting event types (merged view)

All operations (`create`, `update`, `delete`) are kept — every one is a proxy for
"user was interacting with Bluesky at this timestamp." Merged total: **240.6M events**.

| Event type | Count | % | Description |
|-----------|--------|---|-------------|
| `feed_like` | 161.7M | 67.2% | Like or unlike a post |
| `feed_repost` | 26.4M | 11.0% | Repost or un-repost |
| `graph_follow` | 18.8M | 7.8% | Follow or unfollow another user |
| `post_top` | 15.3M | 6.4% | Original post (starts a thread or stands alone) |
| `post_reply` | 12.8M | 5.3% | Reply in a thread |
| `graph_block` | 1.7M | 0.7% | Block or unblock another user |
| `feed_threadgate` | 1.5M | 0.6% | Set or change who can reply to a post |
| `actor_profile` | 0.8M | 0.3% | Create or edit display name, bio, avatar |
| `feed_postgate` | 0.8M | 0.3% | Set or change who can quote/embed a post |
| `graph_listitem` | 0.5M | 0.2% | Add or remove a user from a list |
| `actor_status` | 0.3M | 0.1% | App open/heartbeat signal |
| `labeler_service` | 32K | <0.1% | Register, update, or delete a labeling service |
| `feed_generator` | 9K | <0.1% | Create or edit a custom feed |
| `graph_list` | 9K | <0.1% | Create, edit, or delete a user list |
| `graph_listblock` | 7K | <0.1% | Block or unblock an entire list |
| `notification_declaration` | 6K | <0.1% | Set notification preferences |
| `graph_starterpack` | 2K | <0.1% | Create or edit a starter pack |

**67% of all events are likes.** The next biggest categories — reposts (11%),
follows (8%), and posts (12% combined) — are dramatically smaller. Posts
(top-level + replies) together account for about the same volume as reposts alone.

### Events table: `pau_db.events`

Built by `build_events.py`. The merged view above, persisted to StarRocks with one
additional filter:

**Users with <2 events per active day are excluded.**

Rationale: the events-per-day distribution follows a lognormal (μ=1.43, σ=1.28;
lognormal beats power law with R=298K, p=0.0000). Users below 2 events/day
(~P30) are tourists — they open the app, like one thing, and leave. There isn't
enough signal to reconstruct sessions from their activity. HDBSCAN will handle
remaining noise via its built-in outlier detection (cluster label = -1).

| Metric | Before filter | After filter |
|--------|--------------|-------------|
| Users | 3,086,990 | 2,190,491 |
| Events | 240,564,824 | 238,302,200 |
| % users kept | 100% | 71.0% |
| % events kept | 100% | 99.1% |

Only 29% of users are dropped, but they account for just 0.9% of events — confirming
they're low-activity tourists. The event-type proportions are unchanged.

### What to exclude

**Only the miscellaneous tail** — `graph.repost`, `graph.verification`, `lexicon.collection`,
`graph.cancellation`, `draft.createDraft` — totals **309 events** (0.00015% of the
firehose). Protocol fossils: deprecated features, experimental leftovers, and edge
cases that don't signal real user activity. Dropped entirely.

Everything else — including `[delete]` and `[update]` operations — stays. An unlike
is still a user with the app open hitting a button. An unfollow is a deliberate action.
A profile edit is activity. The goal is "was the user interacting with Bluesky at this
timestamp?" — and any record in the firehose answers yes.

### Why not just `bsky.records`?

`bsky.records` contains posts too (`collection = 'app.bsky.feed.post'`), but the post
content — text, language, reply chain — is buried in a `record_json` column. Using that
would require parsing JSON just to tell a top-level post from a reply. (note: locally this record_json has been removed.)

`bsky.posts` is a **pre-unpacked convenience view** of those same post records, with
clean columns (`post_text`, `lang`, `reply_root_uri`) that make the top-level vs reply
split trivial (`reply_root_uri IS NULL` → `post_top`, otherwise → `post_reply`).

So the merge strategy is: exclude `feed.post` from records to avoid double-counting,
and pull posts from `bsky.posts` instead.

## General metrics values 

| Metric | Value |
|--------|-------|
| Distinct users in `records` | 2,844,778 |
| Distinct users in `posts` | 1,455,391 |
| Distinct users total (union) | 3,086,991 |
| Total record events | 212,491,458 |
| Total post events | 28,073,675 |
