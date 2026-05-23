package main

import (
	"database/sql"
	"fmt"
	"time"
)

// AlreadyParsed returns true if filename was previously processed with status
// "COMPLETED".
func AlreadyParsed(db *sql.DB, filename string) (bool, error) {
	var exists int
	err := db.QueryRow(
		"SELECT 1 FROM parsed_files WHERE filename = ? AND status = 'COMPLETED'",
		filename,
	).Scan(&exists)
	if err == sql.ErrNoRows {
		return false, nil
	}
	return err == nil, err
}

// MarkFileParsed records a file's processing result in the parsed_files table.
func MarkFileParsed(db *sql.DB, filename string, recordCount int, status string) error {
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(
		`INSERT OR REPLACE INTO parsed_files (filename, parsed_at, record_count, status)
		 VALUES (?, ?, ?, ?)`,
		filename, now, recordCount, status,
	)
	if err != nil {
		return fmt.Errorf("markFileParsed(%q): %w", filename, err)
	}
	return nil
}

// FilterAlreadyParsed removes from files any path whose status is COMPLETED
// in parsed_files. Returns only files that still need processing.
func FilterAlreadyParsed(db *sql.DB, files []string) ([]string, error) {
	parsed := make(map[string]bool)

	rows, err := db.Query("SELECT filename FROM parsed_files WHERE status = 'COMPLETED'")
	if err != nil {
		return nil, fmt.Errorf("query parsed_files: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var fn string
		if err := rows.Scan(&fn); err != nil {
			return nil, fmt.Errorf("scan parsed_files: %w", err)
		}
		parsed[fn] = true
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate parsed_files: %w", err)
	}

	out := make([]string, 0, len(files))
	for _, f := range files {
		if !parsed[f] {
			out = append(out, f)
		}
	}
	return out, nil
}
