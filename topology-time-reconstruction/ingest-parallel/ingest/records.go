package ingest

import (
	"encoding/json"
	"fmt"
	"time"
)

// --- Firehose record types ---

type FirehoseRecord struct {
	Kind   string       `json:"kind"`
	TimeUS int64        `json:"time_us"`
	DID    string       `json:"did"`
	Commit CommitRecord `json:"commit"`
}

type CommitRecord struct {
	Collection string          `json:"collection"`
	Operation  string          `json:"operation"`
	RKey       string          `json:"rkey"`
	Record     json.RawMessage `json:"record"`
}

type createPayload struct {
	Subject string `json:"subject"`
}

const (
	colFollow = "app.bsky.graph.follow"
	colBlock  = "app.bsky.graph.block"
)

// Event is a flattened row ready for INSERT into graph_events.
type Event struct {
	URI        string
	ActorDID   string
	SubjectDID string
	ActionType string // "follow", "unfollow", "block", "unblock"
	Timestamp  time.Time
}

// MicrosToTime converts a microsecond Unix epoch to time.Time (UTC).
func MicrosToTime(us int64) time.Time {
	return time.UnixMicro(us).UTC()
}

// AtURI builds an AT Protocol URI.
func AtURI(did, collection, rkey string) string {
	return fmt.Sprintf("at://%s/%s/%s", did, collection, rkey)
}

// MapAction maps collection + operation to an action_type string.
func MapAction(collection, operation string) (string, error) {
	switch collection {
	case colFollow:
		switch operation {
		case "create":
			return "follow", nil
		case "delete":
			return "unfollow", nil
		}
	case colBlock:
		switch operation {
		case "create":
			return "block", nil
		case "delete":
			return "unblock", nil
		}
	}
	return "", fmt.Errorf("unmapped: %s/%s", collection, operation)
}
