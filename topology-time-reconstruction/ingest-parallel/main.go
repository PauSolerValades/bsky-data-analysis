package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/psoler/bluesky-ingest-parallel/ingest"
)

func main() {
	dataDir := flag.String("data", "",
		"Root directory containing YYYY-MM/DD/records_*.jsonl files")
	dsn := flag.String("dsn",
		"pau:regulate-evil-decode@tcp(10.18.74.14:9030)/bsky_topology",
		"StarRocks / MySQL DSN")
	workers := flag.Int("workers", 64,
		"Number of concurrent file-processing goroutines")
	batchSize := flag.Int("batch", 5_000,
		"Rows per INSERT batch")
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, `usage: ingest-parallel [flags]

Parallel ingestion of Bluesky firehose JSONL files into StarRocks.
All workers share one connection pool (capped for StarRocks backend limits).
Tracks progress in parsed_files — safe to interrupt and resume.

Flags:
`)
		flag.PrintDefaults()
	}
	flag.Parse()

	if *dataDir == "" {
		flag.Usage()
		os.Exit(2)
	}

	absData, err := filepath.Abs(*dataDir)
	if err != nil {
		log.Fatalf("Resolve data dir: %v", err)
	}
	if fi, err := os.Stat(absData); err != nil || !fi.IsDir() {
		log.Fatalf("%q is not a directory", absData)
	}

	// ---- Connect (single shared pool for all workers + claiming) ----
	// StarRocks limits backend concurrency to ~6 connections.
	// We use 4 to stay comfortably under, leaving room for metadata queries.
	db, err := ingest.Connect(*dsn, 4)
	if err != nil {
		log.Fatalf("Connect: %v", err)
	}
	defer db.Close()

	// ---- Discover files ----
	log.Println("Discovering files...")
	allFiles, err := ingest.DiscoverFiles(absData)
	if err != nil {
		log.Fatalf("Discover: %v", err)
	}
	log.Printf("Found %d .jsonl files", len(allFiles))
	if len(allFiles) == 0 {
		log.Println("Nothing to do.")
		return
	}

	// ---- Filter already COMPLETED ----
	done, err := ingest.LoadAlreadyParsed(db)
	if err != nil {
		log.Fatalf("Load parsed_files: %v", err)
	}

	todo := make([]string, 0, len(allFiles))
	for _, f := range allFiles {
		if !done[f] {
			todo = append(todo, f)
		}
	}

	skipped := len(allFiles) - len(todo)
	if skipped > 0 {
		log.Printf("Skipping %d already COMPLETED file(s)", skipped)
	}
	if len(todo) == 0 {
		log.Println("All files already processed. Nothing to do.")
		return
	}

	// ---- Claim + dispatch ----
	log.Printf("Claiming and processing %d file(s) with %d workers...", len(todo), *workers)

	sem := make(chan struct{}, *workers)
	stats := &ingest.Stats{}
	var wg sync.WaitGroup

	cfg := ingest.WorkerConfig{
		DB:        db,
		BatchSize: *batchSize,
		Stats:     stats,
		Wg:        &wg,
	}

	start := time.Now()
	var claimed int

	for _, fp := range todo {
		ok, err := ingest.ClaimFile(db, fp)
		if err != nil {
			log.Printf("  WARN: claim %s: %v", fp, err)
			continue
		}
		if !ok {
			continue // already claimed by another instance
		}
		claimed++

		sem <- struct{}{}
		wg.Add(1)
		go func(path string) {
			defer func() { <-sem }()
			ingest.ProcessFile(cfg, path)
		}(fp)
	}

	wg.Wait()
	elapsed := time.Since(start)

	// ---- Summary ----
	log.Printf("==============================")
	log.Printf("Elapsed:       %s", elapsed.Round(time.Second))
	log.Printf("Files claimed: %d", claimed)
	log.Printf("Files done:    %d", stats.FilesDone.Load())
	log.Printf("Files failed:  %d", stats.FilesFailed.Load())
	log.Printf("Rows ingested: %d", stats.RowsIngested.Load())
	if stats.RowsIngested.Load() > 0 && elapsed.Seconds() > 0 {
		rps := float64(stats.RowsIngested.Load()) / elapsed.Seconds()
		log.Printf("Rows/sec:      %.0f", rps)
	}
}
