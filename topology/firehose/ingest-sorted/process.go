package main

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"
)

// ProcessFile reads a single JSONL file and ingests all relevant
// follow/block records into the database.
//
// Commits happen every batchSize records. Returns the count of relevant
// records processed.
func ProcessFile(db *sql.DB, filepath string, batchSize int) (int, error) {
	fh, err := os.Open(filepath)
	if err != nil {
		return 0, fmt.Errorf("open %q: %w", filepath, err)
	}
	defer fh.Close()

	scanner := bufio.NewScanner(fh)
	scanner.Buffer(make([]byte, 0, 1<<20), 4<<20) // 4 MiB max line

	tx, err := db.Begin()
	if err != nil {
		return 0, fmt.Errorf("begin tx: %w", err)
	}

	var recordCount, lineCount int

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		var rec FirehoseRecord
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			continue // skip unparseable lines
		}

		handled, err := dispatchRecord(tx, &rec)
		if err != nil {
			log.Printf("  WARN: record error in %s (line ~%d): %v", filepath, lineCount+1, err)
			lineCount++
			continue
		}
		if handled {
			recordCount++
		}
		lineCount++

		if lineCount%batchSize == 0 && lineCount > 0 {
			if err := tx.Commit(); err != nil {
				return recordCount, fmt.Errorf("commit @ line %d: %w", lineCount, err)
			}
			tx, err = db.Begin()
			if err != nil {
				return recordCount, fmt.Errorf("begin tx: %w", err)
			}
		}
	}

	if err := scanner.Err(); err != nil {
		tx.Rollback()
		return recordCount, fmt.Errorf("scan %q: %w", filepath, err)
	}

	if err := tx.Commit(); err != nil {
		return recordCount, fmt.Errorf("final commit: %w", err)
	}

	return recordCount, nil
}

// dispatchRecord inspects a FirehoseRecord and applies the appropriate
// database mutation.
func dispatchRecord(tx *sql.Tx, rec *FirehoseRecord) (bool, error) {
	if rec.Kind != "commit" {
		return false, nil
	}
	coll := rec.Commit.Collection
	if coll != CollectionFollow && coll != CollectionBlock {
		return false, nil
	}

	did := rec.DID
	op := rec.Commit.Operation
	rkey := rec.Commit.RKey
	timestamp := MicrosToISO(rec.TimeUS)
	uri := AtURI(did, coll, rkey)

	switch op {
	case "create":
		return true, handleCreate(tx, uri, did, coll, timestamp, &rec.Commit)
	case "delete":
		return true, handleDelete(tx, uri, coll, timestamp)
	}
	return false, nil
}

// handleCreate upserts actors and inserts an edge row.
func handleCreate(tx *sql.Tx, uri, did, collection, timestamp string, commit *CommitRecord) error {
	table, err := TableForCollection(collection)
	if err != nil {
		return err
	}

	var inner createPayload
	if err := json.Unmarshal(commit.Record, &inner); err != nil {
		return fmt.Errorf("parse inner record: %w", err)
	}
	subjectDID := inner.Subject

	// Upsert actor
	if did != "" {
		if _, err := tx.Exec(
			`INSERT INTO users (did, first_seen_at, first_seen_uri)
			 VALUES (?, ?, ?) ON CONFLICT(did) DO NOTHING`,
			did, timestamp, AtURI(did, "app.bsky.actor.profile", "self"),
		); err != nil {
			return fmt.Errorf("upsert actor %q: %w", did, err)
		}
	}

	// Upsert subject
	if subjectDID != "" {
		if _, err := tx.Exec(
			`INSERT INTO users (did, first_seen_at, first_seen_uri)
			 VALUES (?, ?, ?) ON CONFLICT(did) DO NOTHING`,
			subjectDID, timestamp, uri,
		); err != nil {
			return fmt.Errorf("upsert subject %q: %w", subjectDID, err)
		}
	}

	// Insert edge
	if _, err := tx.Exec(
		fmt.Sprintf(
			`INSERT INTO %s (uri, actor_did, subject_did, valid_from, valid_to)
			 VALUES (?, ?, ?, ?, NULL) ON CONFLICT(uri) DO NOTHING`,
			table,
		),
		uri, did, subjectDID, timestamp,
	); err != nil {
		return fmt.Errorf("insert edge: %w", err)
	}

	return nil
}

// handleDelete closes an edge row by setting valid_to.
func handleDelete(tx *sql.Tx, uri, collection, timestamp string) error {
	table, err := TableForCollection(collection)
	if err != nil {
		return err
	}
	if _, err := tx.Exec(
		fmt.Sprintf(`UPDATE %s SET valid_to = ? WHERE uri = ? AND valid_to IS NULL`, table),
		timestamp, uri,
	); err != nil {
		return fmt.Errorf("delete edge: %w", err)
	}
	return nil
}

