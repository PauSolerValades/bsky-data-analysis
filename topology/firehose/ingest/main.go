package main

import (
	"database/sql"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"

)

func main() {
	dataDir := flag.String("data", "",
		"Root directory containing YYYY-MM/DD/records_*.jsonl files")
	dsn := flag.String("dsn",
		"pau:regulate-evil-decode@tcp(10.18.74.14:9030)/bsky_topology",
		"StarRocks DSN")
	producers := flag.Int("producers", 64, "File-reader goroutines")
	consumers := flag.Int("consumers", 6, "DB-writer goroutines")
	batchSize := flag.Int("batch", 5_000, "Rows per INSERT")
	chanSize := flag.Int("chan", 10_000_000, "Channel buffer (events)")
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, `usage: ingest-parallel [flags]

Producer-consumer pipeline: Bluesky firehose → StarRocks.
Producers read NFS + filter. Consumers batch INSERT.
Files marked PROCESSING on claim, COMPLETED after pipeline drains.
Safe to interrupt — restart skips COMPLETED files.

Flags:
`)
		flag.PrintDefaults()
	}
	flag.Parse()
	if *dataDir == "" {
		flag.Usage()
		os.Exit(2)
	}

	absDir, err := filepath.Abs(*dataDir)
	if err != nil {
		log.Fatalf("Resolve: %v", err)
	}

	// ---- Connections ----
	metaDB, err := Connect(*dsn, 1)
	if err != nil {
		log.Fatalf("Meta: %v", err)
	}
	defer metaDB.Close()

	workerDB, err := Connect(*dsn, *consumers)
	if err != nil {
		log.Fatalf("Worker: %v", err)
	}
	defer workerDB.Close()

	// ---- Discover + filter done ----
	log.Println("Discovering...")
	all, err := DiscoverFiles(absDir)
	if err != nil {
		log.Fatalf("Discover: %v", err)
	}
	log.Printf("Found %d .jsonl files", len(all))

	done, err := LoadAlreadyParsed(metaDB)
	if err != nil {
		log.Fatalf("Load: %v", err)
	}

	todo := make([]string, 0, len(all))
	for _, f := range all {
		if !done[f] {
			todo = append(todo, f)
		}
	}
	if skipped := len(all) - len(todo); skipped > 0 {
		log.Printf("Skipping %d COMPLETED", skipped)
	}
	if len(todo) == 0 {
		log.Println("All done.")
		return
	}

	// ---- Channels ----
	events := make(chan Event, *chanSize)
	files := make(chan string, len(todo))
	for _, f := range todo {
		files <- f
	}
	close(files)

	// ---- Launch consumers ----
	var consumerWg sync.WaitGroup
	rowsIngested := new(atomic.Int64)

	consumerCfg := ConsumerConfig{
		WorkerDB:     workerDB,
		Events:       events,
		BatchSize:    *batchSize,
		RowsIngested: rowsIngested,
		Wg:           &consumerWg,
	}

	for i := 0; i < *consumers; i++ {
		consumerWg.Add(1)
		go RunConsumer(consumerCfg)
	}

	// ---- Launch producers ----
	producerStats := &ProducerStats{}
	claimSem := make(chan struct{}, 2)

	producerCfg := ProducerConfig{
		MetaDB:   metaDB,
		Events:   events,
		ClaimSem: claimSem,
		Stats:    producerStats,
	}

	var producerWg sync.WaitGroup
	start := time.Now()

	for i := 0; i < *producers; i++ {
		producerWg.Add(1)
		go func() {
			defer producerWg.Done()
			RunProducer(producerCfg, files)
		}()
	}

	// ---- Wait for producers, close channel, wait for consumers ----
	producerWg.Wait()
	close(events)
	consumerWg.Wait()
	elapsed := time.Since(start)

	// ---- Mark all PROCESSING files as COMPLETED ----
	// Pipeline drained = all events are in StarRocks. Safe to commit.
	log.Println("Marking files COMPLETED...")
	markAllComplete(metaDB)

	// ---- Summary ----
	log.Printf("==============================")
	log.Printf("Elapsed:       %s", elapsed.Round(time.Second))
	log.Printf("Files claimed: %d", producerStats.FilesClaimed.Load())
	log.Printf("Files failed:  %d", producerStats.FilesFailed.Load())
	log.Printf("Rows ingested: %d", rowsIngested.Load())
	if rowsIngested.Load() > 0 && elapsed.Seconds() > 0 {
		rps := float64(rowsIngested.Load()) / elapsed.Seconds()
		log.Printf("Rows/sec:      %.0f", rps)
	}
}

func markAllComplete(db *sql.DB) {
	_, _ = db.Exec(`
		INSERT INTO parsed_files (filename, status, record_count, parsed_at)
		SELECT filename, 'COMPLETED', 0, NOW()
		FROM parsed_files WHERE status = 'PROCESSING'
	`)
}
