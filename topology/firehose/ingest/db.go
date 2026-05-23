package main

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

// Connect opens a connection to StarRocks and ensures both tables exist.
// Each worker calls this independently so connections are not shared.
// Connect opens a connection to StarRocks and ensures all tables exist.
// maxConns caps the number of concurrent queries to respect StarRocks limits.
func Connect(dsn string, maxConns int) (*sql.DB, error) {
	if !strings.Contains(dsn, "interpolateParams") {
		sep := "&"
		if !strings.Contains(dsn, "?") {
			sep = "?"
		}
		dsn += sep + "interpolateParams=true"
	}

	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, fmt.Errorf("open: %w", err)
	}

	db.SetMaxOpenConns(maxConns)
	db.SetMaxIdleConns(maxConns)
	db.SetConnMaxLifetime(5 * time.Minute)

	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("ping: %w", err)
	}

	// CREATE TABLE one at a time (StarRocks doesn't batch DDL)
	for _, ddl := range []string{createParsedFiles, createGraphEvents} {
		if _, err := db.Exec(ddl); err != nil {
			db.Close()
			return nil, fmt.Errorf("create table: %w", err)
		}
	}

	return db, nil
}

const createParsedFiles = `
CREATE TABLE IF NOT EXISTS parsed_files (
    filename     VARCHAR(512)  NOT NULL,
    status       VARCHAR(16)   NOT NULL,
    record_count BIGINT        NOT NULL,
    parsed_at    DATETIME      NOT NULL
) ENGINE=OLAP DUPLICATE KEY(filename)
DISTRIBUTED BY HASH(filename) BUCKETS 8
`

const createGraphEvents = `
CREATE TABLE IF NOT EXISTS graph_events (
    event_timestamp DATETIME      NOT NULL,
    uri             VARCHAR(256)  NOT NULL,
    actor_did       VARCHAR(128)  NOT NULL,
    subject_did     VARCHAR(128)  NOT NULL,
    action_type     VARCHAR(16)   NOT NULL
) ENGINE=OLAP DUPLICATE KEY(event_timestamp, uri)
DISTRIBUTED BY HASH(uri) BUCKETS 32
`
