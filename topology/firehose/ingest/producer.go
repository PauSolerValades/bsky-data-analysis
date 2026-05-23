package main

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"sync/atomic"
	"time"
)

type ProducerConfig struct {
	MetaDB   *sql.DB
	Events   chan<- Event
	ClaimSem chan struct{}
	Stats    *ProducerStats
}

type ProducerStats struct {
	FilesClaimed atomic.Int64
	FilesSkipped atomic.Int64
	FilesFailed  atomic.Int64
}

func RunProducer(cfg ProducerConfig, files <-chan string) {
	for fp := range files {
		processOneFile(cfg, fp)
	}
}

func processOneFile(cfg ProducerConfig, filepath string) {
	// ---- 1. Claim ----
	cfg.ClaimSem <- struct{}{}
	var ok bool
	var err error
	for attempt := 0; attempt < 3; attempt++ {
		ok, err = ClaimFile(cfg.MetaDB, filepath)
		if err == nil {
			break
		}
		time.Sleep(time.Duration(attempt+1) * 500 * time.Millisecond)
	}
	<-cfg.ClaimSem

	if err != nil {
		fmt.Printf("  FAIL %s: claim: %v\n", filepath, err)
		cfg.Stats.FilesFailed.Add(1)
		return
	}
	if !ok {
		cfg.Stats.FilesSkipped.Add(1)
		return
	}
	cfg.Stats.FilesClaimed.Add(1)

	// ---- 2. Read + filter + send ----
	fh, err := os.Open(filepath)
	if err != nil {
		fmt.Printf("  FAIL %s: open: %v\n", filepath, err)
		_ = MarkFileFailed(cfg.MetaDB, filepath)
		cfg.Stats.FilesFailed.Add(1)
		return
	}
	defer fh.Close()

	scanner := bufio.NewScanner(fh)
	scanner.Buffer(make([]byte, 0, 1<<20), 4<<20)

	var count int64

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		var rec FirehoseRecord
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			continue
		}

		ev, ok := ExtractEvent(&rec)
		if !ok {
			continue
		}

		cfg.Events <- ev
		count++
	}

	if err := scanner.Err(); err != nil {
		fmt.Printf("  FAIL %s: scan: %v\n", filepath, err)
		_ = MarkFileFailed(cfg.MetaDB, filepath)
		cfg.Stats.FilesFailed.Add(1)
		return
	}

	// File read successfully — consumer will handle the INSERTs.
	// We leave it as PROCESSING. Main marks COMPLETED after pipeline drains.
}
