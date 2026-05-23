package main

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

// Event is a flattened row for INSERT into graph_events.
type Event struct {
	URI        string
	ActorDID   string
	SubjectDID string
	ActionType string
	Timestamp  time.Time
}

// --- Helpers ---

func MicrosToTime(us int64) time.Time { return time.UnixMicro(us).UTC() }

func AtURI(did, collection, rkey string) string {
	return fmt.Sprintf("at://%s/%s/%s", did, collection, rkey)
}

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

func ExtractEvent(rec *FirehoseRecord) (Event, bool) {
	if rec.Kind != "commit" {
		return Event{}, false
	}
	coll := rec.Commit.Collection
	if coll != colFollow && coll != colBlock {
		return Event{}, false
	}

	action, err := MapAction(coll, rec.Commit.Operation)
	if err != nil {
		return Event{}, false
	}

	var inner createPayload
	if err := json.Unmarshal(rec.Commit.Record, &inner); err != nil {
		return Event{}, false
	}

	return Event{
		URI:        AtURI(rec.DID, coll, rec.Commit.RKey),
		ActorDID:   rec.DID,
		SubjectDID: inner.Subject,
		ActionType: action,
		Timestamp:  MicrosToTime(rec.TimeUS),
	}, true
}
