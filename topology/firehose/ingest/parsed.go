package main

import (
	"database/sql"
	"fmt"
	"time"
)

func ClaimFile(db *sql.DB, filename string) (bool, error) {
	var exists int
	err := db.QueryRow(
		"SELECT 1 FROM parsed_files WHERE filename = ? AND status = 'COMPLETED' LIMIT 1",
		filename,
	).Scan(&exists)
	if err == nil {
		return false, nil
	}
	if err != sql.ErrNoRows {
		return false, fmt.Errorf("check: %w", err)
	}

	_, err = db.Exec(
		"INSERT INTO parsed_files (filename, status, record_count, parsed_at) VALUES (?, 'PROCESSING', 0, ?)",
		filename, time.Now().UTC(),
	)
	if err != nil {
		return false, fmt.Errorf("insert: %w", err)
	}
	return true, nil
}

func MarkFileFailed(db *sql.DB, filename string) error {
	_, err := db.Exec(
		"INSERT INTO parsed_files (filename, status, record_count, parsed_at) VALUES (?, 'FAILED', 0, ?)",
		filename, time.Now().UTC(),
	)
	return err
}

func LoadAlreadyParsed(db *sql.DB) (map[string]bool, error) {
	rows, err := db.Query("SELECT filename FROM parsed_files WHERE status = 'COMPLETED'")
	if err != nil {
		return nil, fmt.Errorf("query: %w", err)
	}
	defer rows.Close()

	done := make(map[string]bool)
	for rows.Next() {
		var fn string
		if err := rows.Scan(&fn); err != nil {
			return nil, err
		}
		done[fn] = true
	}
	return done, rows.Err()
}
