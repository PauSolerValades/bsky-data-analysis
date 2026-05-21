package ingest

import (
	"database/sql"
	"fmt"
	"time"
)

// ClaimFile inserts a PROCESSING row. Returns true if we should proceed.
// If the file already has a COMPLETED row, we skip (return false).
func ClaimFile(db *sql.DB, filename string) (bool, error) {
	// Check if already COMPLETED
	var exists int
	err := db.QueryRow(
		"SELECT 1 FROM parsed_files WHERE filename = ? AND status = 'COMPLETED' LIMIT 1",
		filename,
	).Scan(&exists)
	if err == nil {
		return false, nil // already done
	}
	if err != sql.ErrNoRows {
		return false, fmt.Errorf("check completed: %w", err)
	}

	// Insert PROCESSING row
	_, err = db.Exec(
		`INSERT INTO parsed_files (filename, status, record_count, parsed_at)
		 VALUES (?, 'PROCESSING', 0, ?)`,
		filename, time.Now().UTC(),
	)
	if err != nil {
		return false, fmt.Errorf("insert processing: %w", err)
	}
	return true, nil
}

// MarkFileDone inserts a COMPLETED row with the record count.
func MarkFileDone(db *sql.DB, filename string, count int64) error {
	_, err := db.Exec(
		`INSERT INTO parsed_files (filename, status, record_count, parsed_at)
		 VALUES (?, 'COMPLETED', ?, ?)`,
		filename, count, time.Now().UTC(),
	)
	return err
}

// MarkFileFailed inserts a FAILED row.
func MarkFileFailed(db *sql.DB, filename string) error {
	_, err := db.Exec(
		`INSERT INTO parsed_files (filename, status, record_count, parsed_at)
		 VALUES (?, 'FAILED', 0, ?)`,
		filename, time.Now().UTC(),
	)
	return err
}

// LoadAlreadyParsed returns filenames that have at least one COMPLETED row.
func LoadAlreadyParsed(db *sql.DB) (map[string]bool, error) {
	rows, err := db.Query(
		"SELECT filename FROM parsed_files WHERE status = 'COMPLETED'")
	if err != nil {
		return nil, fmt.Errorf("query: %w", err)
	}
	defer rows.Close()

	done := make(map[string]bool)
	for rows.Next() {
		var fn string
		if err := rows.Scan(&fn); err != nil {
			return nil, fmt.Errorf("scan: %w", err)
		}
		done[fn] = true
	}
	return done, rows.Err()
}
