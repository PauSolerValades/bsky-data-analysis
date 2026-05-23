package main

import (
	"encoding/json"
	"fmt"
	"time"
)

// --- Firehose record types ---

// FirehoseRecord mirrors a single JSONL line from the Bluesky firehose.
type FirehoseRecord struct {
	Kind   string       `json:"kind"`
	TimeUS int64        `json:"time_us"`
	DID    string       `json:"did"`
	Commit CommitRecord `json:"commit"`
}

// CommitRecord is the "commit" block inside a FirehoseRecord.
type CommitRecord struct {
	Collection string          `json:"collection"`
	Operation  string          `json:"operation"`
	RKey       string          `json:"rkey"`
	Record     json.RawMessage `json:"record"` // lazy — we only extract .subject
}

// createPayload is the inner record for follow/block creates.
type createPayload struct {
	Subject string `json:"subject"`
}

// Lexicon collections we care about.
const (
	CollectionFollow = "app.bsky.graph.follow"
	CollectionBlock  = "app.bsky.graph.block"
)

// TableForCollection maps a lexicon collection to its edge table name.
func TableForCollection(c string) (string, error) {
	switch c {
	case CollectionFollow:
		return "follow_edges", nil
	case CollectionBlock:
		return "block_edges", nil
	}
	return "", fmt.Errorf("unknown collection %q", c)
}

// MicrosToISO converts a microsecond Unix epoch to an ISO 8601 UTC string.
func MicrosToISO(us int64) string {
	return time.UnixMicro(us).UTC().Format(time.RFC3339)
}

// AtURI builds an AT Protocol URI: at://<did>/<collection>/<rkey>.
func AtURI(did, collection, rkey string) string {
	return fmt.Sprintf("at://%s/%s/%s", did, collection, rkey)
}
