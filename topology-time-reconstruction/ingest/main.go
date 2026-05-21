package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"

	src "github.com/psoler/bluesky-ingest/src"
)

func main() {
	deleteDB := flag.Bool("delete-db", false,
		"If the database file already exists, delete it first and start fresh")
	dbPath := flag.String("db", "bsky-topology.db",
		"Path to the SQLite database file")
	batchSize := flag.Int("batch-size", 10_000,
		"Number of records per transaction commit")
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, `usage: ingest [flags] <base_dir>

Walk <base_dir> recursively, find all .jsonl / .jsonl.zst files, and ingest
Bluesky follow/block events into a SQLite database.

The directory tree is expected to follow the pattern: YYYY-MM/DD/<files>.

Flags:
`)
		flag.PrintDefaults()
	}

	flag.Parse()
	if flag.NArg() < 1 {
		flag.Usage()
		os.Exit(2)
	}

	baseDir := flag.Arg(0)
	if abs, err := filepath.Abs(baseDir); err == nil {
		baseDir = abs
	}
	if fi, err := os.Stat(baseDir); err != nil || !fi.IsDir() {
		log.Fatalf("%q is not a directory", baseDir)
	}

	// ---- Step 1: Database ----
	db, err := src.InitDB(*dbPath, *deleteDB)
	if err != nil {
		log.Fatalf("Database init: %v", err)
	}
	defer db.Close()
	log.Printf("Database ready: %s", *dbPath)

	// ---- Step 2: Discover files ----
	log.Printf("Scanning: %s", baseDir)
	allFiles, err := src.DiscoverFiles(baseDir)
	if err != nil {
		log.Fatalf("File discovery: %v", err)
	}
	log.Printf("Found %d .jsonl / .jsonl.zst files", len(allFiles))
	if len(allFiles) == 0 {
		log.Println("No files to process. Done.")
		return
	}

	// ---- Step 3: Filter already-parsed ----
	todo, err := src.FilterAlreadyParsed(db, allFiles)
	if err != nil {
		log.Fatalf("Filter already-parsed: %v", err)
	}
	if len(todo) == 0 {
		log.Println("All files already processed. Nothing to do.")
		return
	}
	if skipped := len(allFiles) - len(todo); skipped > 0 {
		log.Printf("Skipping %d already-completed file(s)", skipped)
	}

	// ---- Step 4: Process ----
	log.Printf("Processing %d file(s)…", len(todo))
	for i, fp := range todo {
		fname := filepath.Base(fp)
		fmt.Printf("[%d/%d] %s … ", i+1, len(todo), fname)

		count, err := src.ProcessFile(db, fp, *batchSize)
		if err != nil {
			log.Printf("FAILED: %v", err)
			_ = src.MarkFileParsed(db, fp, 0, "FAILED")
			continue
		}

		if err := src.MarkFileParsed(db, fp, count, "COMPLETED"); err != nil {
			log.Printf("  WARN: %v", err)
		}
		fmt.Printf("%d records ✓\n", count)
	}

	log.Println("Done.")
}
