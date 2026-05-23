# Bluesky Social Graph Database Specification

This document outlines the database schema and ingestion logic for tracking the AT Protocol (Bluesky) social graph over time. The schema uses a Slowly Changing Dimension (SCD) Type 2 approach to handle flip-flopping (e.g., follow -> unfollow -> follow) while maintaining exact historical states.

## 1. Schema Definition

### 1.1 `users`
Tracks unique Decentralized Identifiers (DIDs) and the specific event that first introduced them to the database.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `did` | String | Primary Key | The unique user identifier (e.g., `did:plc:...`) |
| `first_seen_at` | Timestamp | Not Null | When the event that introduced them occurred |
| `first_seen_uri` | String | Not Null | The specific ATProto record URI where they were first spotted |

### 1.2 `follow_edges`
Stores the exact lifespan of every follow relationship.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `uri` | String | Primary Key | The `app.bsky.graph.follow` record URI |
| `actor_did` | String | Indexed | The user clicking follow |
| `subject_did` | String | Indexed | The user being followed |
| `valid_from` | Timestamp | Indexed (Composite) | When the follow was created |
| `valid_to` | Timestamp | Indexed (Composite), Nullable | When the follow was deleted (NULL if active) |

### 1.3 `block_edges`
Stores the exact lifespan of every block relationship.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `uri` | String | Primary Key | The `app.bsky.graph.block` record URI |
| `actor_did` | String | Indexed | The user issuing the block |
| `subject_did` | String | Indexed | The user being blocked |
| `valid_from` | Timestamp | Indexed (Composite) | When the block was created |
| `valid_to` | Timestamp | Indexed (Composite), Nullable | When the block was deleted (unblocked) |

### 1.4 `parsed_files`
Maintains a ledger of all processed data files to ensure idempotency and prevent duplicate processing.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `filename` | String | Primary Key | The full name or path of the processed file |
| `parsed_at` | Timestamp | Not Null | When the parser finished processing this file |
| `record_count` | Integer | Nullable | Total number of relevant records processed |
| `status` | String | Not Null | E.g., 'COMPLETED', 'FAILED' |

---

## 2. Recommended Indexes
To ensure fast ingestion and instant graph snapshots:

* **Users:** PK on `did`.
* **Follows/Blocks Snapshots:** Composite index on `(valid_from, valid_to)` for time-travel queries.
* **Follows/Blocks Traversals:** Separate indexes on `actor_did` and `subject_did` for graph traversals and degree counting.

---

## 3. Ingestion Script Logic (The "Dumb" Script)

The parser should operate as a simple, stateless loop that processes events chronologically.

### Step 1: Initialization & File Check
1. Read the target directory for data files.
2. Query `parsed_files` to retrieve a list of already processed filenames.
3. Filter out any files that have a 'COMPLETED' status.

### Step 2: Record Processing (Per File)
For each unprocessed file, read records chronologically:

1.  **Filter by Lexicon:** Ignore all records except `app.bsky.graph.follow` and `app.bsky.graph.block`.
2.  **Extract Core Data:**
    Extract `uri`, `actor_did`, `subject_did` (if available), and timestamp.
3.  **Handle 'Create' Events:**
    * **Nodes:** `UPSERT` `actor_did` into `users` (ON CONFLICT DO NOTHING). `UPSERT` `subject_did` into `users` (ON CONFLICT DO NOTHING).
    * **Edges:** `INSERT` into `follow_edges` (or `block_edges` based on lexicon) with `valid_to` set to `NULL`. Use `ON CONFLICT (uri) DO NOTHING` for safety.
4.  **Handle 'Delete' Events:**
    * **Edges:** `UPDATE` `follow_edges` (or `block_edges`) where `uri = <event_uri>` and `valid_to IS NULL`. Set `valid_to` = `<event_timestamp>`. (No need to update the `users` table).

### Step 3: Finalization
1. Once EOF is reached successfully, `INSERT` the file's metadata into `parsed_files` with status 'COMPLETED' and the current `TIMESTAMP`.
2. Commit the transaction (if batching) and move to the next file.
