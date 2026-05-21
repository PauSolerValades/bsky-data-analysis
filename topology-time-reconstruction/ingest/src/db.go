package src

import (
	"database/sql"
	_ "embed"
	"fmt"
	"os"

	_ "github.com/mattn/go-sqlite3"
)

//go:embed schema.sql
var schemaSQL string

// InitDB creates or initializes the SQLite database.
//
// Policy (strict, no surprises):
//   - If dbPath does NOT exist: create it, run the schema, return open *sql.DB.
//   - If dbPath EXISTS and deleteDB is true: delete it first, create fresh.
//   - If dbPath EXISTS and deleteDB is false: return an error immediately.
func InitDB(dbPath string, deleteDB bool) (*sql.DB, error) {
	if fileExists(dbPath) {
		if !deleteDB {
			return nil, fmt.Errorf(
				"database %q already exists — pass --delete-db to recreate it",
				dbPath,
			)
		}
		if err := os.Remove(dbPath); err != nil {
			return nil, fmt.Errorf("failed to delete existing database %q: %w", dbPath, err)
		}
	}

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return nil, fmt.Errorf("open database: %w", err)
	}

	// Performance pragmas
	for _, pragma := range []string{
		"PRAGMA journal_mode=WAL",
		"PRAGMA synchronous=NORMAL",
	} {
		if _, err := db.Exec(pragma); err != nil {
			db.Close()
			return nil, fmt.Errorf("pragma %q: %w", pragma, err)
		}
	}

	if _, err := db.Exec(schemaSQL); err != nil {
		db.Close()
		return nil, fmt.Errorf("execute schema: %w", err)
	}

	return db, nil
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
