package ingest

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// BatchWriter accumulates Event rows and flushes them as a multi-row INSERT.
// One BatchWriter per worker goroutine. Not safe for concurrent use.
type BatchWriter struct {
	db    *sql.DB
	rows  []Event
	cap   int
	total int64
}

func NewBatchWriter(db *sql.DB, cap int) *BatchWriter {
	return &BatchWriter{db: db, cap: cap}
}

// Add queues an event. Flushes automatically when the batch is full.
func (bw *BatchWriter) Add(ev Event) error {
	bw.rows = append(bw.rows, ev)
	if len(bw.rows) >= bw.cap {
		return bw.Flush()
	}
	return nil
}

// Flush sends all queued rows to StarRocks in a single multi-row INSERT.
func (bw *BatchWriter) Flush() error {
	if len(bw.rows) == 0 {
		return nil
	}

	const row = "(?,?,?,?,?)"
	placeholders := make([]string, len(bw.rows))
	args := make([]interface{}, 0, len(bw.rows)*5)

	for i, ev := range bw.rows {
		placeholders[i] = row
		args = append(args, ev.Timestamp, ev.URI, ev.ActorDID, ev.SubjectDID, ev.ActionType)
	}

	query := "INSERT INTO graph_events (event_timestamp, uri, actor_did, subject_did, action_type) VALUES " +
		strings.Join(placeholders, ",")

	_, err := bw.db.Exec(query, args...)
	if err != nil {
		// One retry after a brief pause (transient network/load spike)
		time.Sleep(500 * time.Millisecond)
		_, err = bw.db.Exec(query, args...)
	}

	bw.total += int64(len(bw.rows))
	bw.rows = bw.rows[:0]

	if err != nil {
		return fmt.Errorf("flush %d rows: %w", len(placeholders), err)
	}
	return nil
}

func (bw *BatchWriter) Total() int64 { return bw.total }

// ---------------------------------------------------------------------------
// Concurrency plumbing
// ---------------------------------------------------------------------------

// Stats holds aggregate counters updated atomically by all workers.
type Stats struct {
	FilesDone    atomic.Int64
	FilesFailed  atomic.Int64
	RowsIngested atomic.Int64
}

// WorkerConfig holds everything a single worker goroutine needs.
// DB is a shared connection pool (all workers share it).
type WorkerConfig struct {
	DB        *sql.DB
	BatchSize int
	Stats     *Stats
	Wg        *sync.WaitGroup
}

// ProcessFile is the per-file entry point for a worker goroutine.
// Opens a file, scans JSONL lines, extracts follow/block events,
// and batch-inserts them into StarRocks.
func ProcessFile(cfg WorkerConfig, filepath string) {
	defer cfg.Wg.Done()

	db := cfg.DB

	// Any early return below is a failure — mark as such
	var failed bool
	defer func() {
		if failed {
			_ = MarkFileFailed(db, filepath)
		}
	}()

	fh, err := os.Open(filepath)
	if err != nil {
		failed = true
		cfg.Stats.FilesFailed.Add(1)
		fmt.Printf("  FAIL %s: open: %v\n", filepath, err)
		return
	}
	defer fh.Close()

	scanner := bufio.NewScanner(fh)
	scanner.Buffer(make([]byte, 0, 1<<20), 4<<20) // 4 MiB max line

	bw := NewBatchWriter(db, cfg.BatchSize)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		var rec FirehoseRecord
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			continue
		}

		ev, ok := extractEvent(&rec)
		if !ok {
			continue
		}

		if err := bw.Add(ev); err != nil {
			failed = true
			cfg.Stats.FilesFailed.Add(1)
			fmt.Printf("  FAIL %s: insert: %v\n", filepath, err)
			return
		}
	}

	if err := scanner.Err(); err != nil {
		failed = true
		cfg.Stats.FilesFailed.Add(1)
		fmt.Printf("  FAIL %s: scan: %v\n", filepath, err)
		return
	}

	if err := bw.Flush(); err != nil {
		failed = true
		cfg.Stats.FilesFailed.Add(1)
		fmt.Printf("  FAIL %s: final flush: %v\n", filepath, err)
		return
	}

	if err := MarkFileDone(db, filepath, bw.Total()); err != nil {
		fmt.Printf("  WARN %s: mark done: %v\n", filepath, err)
	}
	cfg.Stats.FilesDone.Add(1)
	cfg.Stats.RowsIngested.Add(bw.Total())
	fmt.Printf("  OK   %s  (%d rows)\n", filepath, bw.Total())
}

// extractEvent tries to produce an Event from a FirehoseRecord.
// Returns (Event, false) for non-relevant records.
func extractEvent(rec *FirehoseRecord) (Event, bool) {
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
