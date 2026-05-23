package main

import (
	"database/sql"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
)

// RunConsumer reads Events from the channel, accumulates batches, and
// INSERTs into StarRocks. No per-file tracking — just pure insertion.
func RunConsumer(cfg ConsumerConfig) {
	defer cfg.Wg.Done()

	batch := make([]Event, 0, cfg.BatchSize)

	flush := func() error {
		if len(batch) == 0 {
			return nil
		}

		const row = "(?,?,?,?,?)"
		placeholders := make([]string, 0, len(batch))
		args := make([]interface{}, 0, len(batch)*5)

		for _, ev := range batch {
			placeholders = append(placeholders, row)
			args = append(args, ev.Timestamp, ev.URI, ev.ActorDID, ev.SubjectDID, ev.ActionType)
		}

		query := fmt.Sprintf(
			"INSERT INTO graph_events (event_timestamp, uri, actor_did, subject_did, action_type) VALUES %s",
			strings.Join(placeholders, ","),
		)

		_, err := cfg.WorkerDB.Exec(query, args...)
		if err != nil {
			return err
		}

		cfg.RowsIngested.Add(int64(len(batch)))
		batch = batch[:0]
		return nil
	}

	for ev := range cfg.Events {
		batch = append(batch, ev)

		if len(batch) >= cfg.BatchSize {
			if err := flush(); err != nil {
				fmt.Printf("  WARN: batch flush: %v\n", err)
			}
		}
	}

	// Final flush
	if err := flush(); err != nil {
		fmt.Printf("  WARN: final flush: %v\n", err)
	}
}

type ConsumerConfig struct {
	WorkerDB     *sql.DB
	Events       <-chan Event
	BatchSize    int
	RowsIngested *atomic.Int64
	Wg           *sync.WaitGroup
}
