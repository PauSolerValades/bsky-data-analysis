-- Bluesky Social Graph Database Schema

CREATE TABLE IF NOT EXISTS users (
    did             TEXT PRIMARY KEY,
    first_seen_at   TEXT NOT NULL,
    first_seen_uri  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS follow_edges (
    uri         TEXT PRIMARY KEY,
    actor_did   TEXT NOT NULL,
    subject_did TEXT NOT NULL,
    valid_from  TEXT NOT NULL,
    valid_to    TEXT
);

CREATE TABLE IF NOT EXISTS block_edges (
    uri         TEXT PRIMARY KEY,
    actor_did   TEXT NOT NULL,
    subject_did TEXT NOT NULL,
    valid_from  TEXT NOT NULL,
    valid_to    TEXT
);

CREATE TABLE IF NOT EXISTS parsed_files (
    filename     TEXT PRIMARY KEY,
    parsed_at    TEXT NOT NULL,
    record_count INTEGER,
    status       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_follow_edges_actor_did   ON follow_edges(actor_did);
CREATE INDEX IF NOT EXISTS idx_follow_edges_subject_did ON follow_edges(subject_did);
CREATE INDEX IF NOT EXISTS idx_follow_edges_valid_range ON follow_edges(valid_from, valid_to);

CREATE INDEX IF NOT EXISTS idx_block_edges_actor_did    ON block_edges(actor_did);
CREATE INDEX IF NOT EXISTS idx_block_edges_subject_did  ON block_edges(subject_did);
CREATE INDEX IF NOT EXISTS idx_block_edges_valid_range  ON block_edges(valid_from, valid_to);
