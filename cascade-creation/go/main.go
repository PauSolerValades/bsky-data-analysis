package main

import (
	"bufio"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
)

var outDir = flag.String("output", ".", "Output directory for CSV files")

func main() {
	flag.Parse()
	args := flag.Args()
	if len(args) < 1 {
		fmt.Fprintf(os.Stderr, "Usage: %s [-output dir] <cascades.tsv>\n", os.Args[0])
		fmt.Fprintf(os.Stderr, "\nAfter building CSVs, load into StarRocks:\n")
		fmt.Fprintf(os.Stderr, "  mysql -h 10.18.74.14 -P 9030 -u pau -p -e \"LOAD DATA LOCAL INFILE 'cascades_rows.csv' INTO TABLE pau_db.cascades FIELDS TERMINATED BY ',' ENCLOSED BY '\\\"'\"\n")
		os.Exit(1)
	}
	tsvPath := args[0]

	if err := os.MkdirAll(*outDir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "cannot create output dir: %v\n", err)
		os.Exit(1)
	}

	// ── Open output files ─────────────────────────────────────────────

	cf, err := os.Create(*outDir + "/cascades_rows.csv")
	if err != nil {
		fmt.Fprintf(os.Stderr, "cascades output: %v\n", err)
		os.Exit(1)
	}
	defer cf.Close()
	cw := bufio.NewWriter(cf)
	defer cw.Flush()

	ef, err := os.Create(*outDir + "/cascade_edges_rows.csv")
	if err != nil {
		fmt.Fprintf(os.Stderr, "edges output: %v\n", err)
		os.Exit(1)
	}
	defer ef.Close()
	ew := bufio.NewWriter(ef)
	defer ew.Flush()

	// ── Read TSV, build trees, write CSVs ─────────────────────────────

	f, err := os.Open(tsvPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot open %s: %v\n", tsvPath, err)
		os.Exit(1)
	}
	defer f.Close()

	fmt.Fprintf(os.Stderr, "Reading %s...\n", tsvPath)

	var (
		currentPost   string
		currentEvents []RawEvent
		totalCascades int64
		first         = true
		lineNo        int
	)

	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 0, 1024*1024), 10*1024*1024)

	for scanner.Scan() {
		line := scanner.Text()
		lineNo++
		if line == "" {
			continue
		}
		fields := strings.Split(line, "\t")
		if len(fields) < 6 {
			continue
		}
		subjectURI := fields[0]
		repostURI := fields[1]
		viaURI := fields[2]
		actorDID := fields[3]
		timeUS, err := strconv.ParseInt(fields[4], 10, 64)
		if err != nil {
			continue
		}
		if repostURI == `\N` || repostURI == "NULL" {
			repostURI = ""
		}
		if viaURI == `\N` || viaURI == "NULL" {
			viaURI = ""
		}

		if subjectURI != currentPost {
			if !first {
				writeCascade(currentPost, currentEvents, cw, ew)
				totalCascades++
				if totalCascades%100000 == 0 {
					fmt.Fprintf(os.Stderr, "  %d cascades (line %d)...\n", totalCascades, lineNo)
				}
			}
			first = false
			currentPost = subjectURI
			currentEvents = nil
		}
		currentEvents = append(currentEvents, RawEvent{
			RepostURI: repostURI,
			ActorDID:  actorDID,
			ViaURI:    viaURI,
			TimeUS:    timeUS,
		})
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "scan error: %v\n", err)
	}
	if len(currentEvents) > 0 {
		writeCascade(currentPost, currentEvents, cw, ew)
		totalCascades++
	}

	cw.Flush()
	ew.Flush()

	fmt.Fprintf(os.Stderr, "\nDone. %d cascades from %d lines.\n", totalCascades, lineNo)
	fmt.Fprintf(os.Stderr, "Output: %s/cascades_rows.csv, %s/cascade_edges_rows.csv\n", *outDir, *outDir)
}

func writeCascade(postURI string, events []RawEvent, cw, ew *bufio.Writer) {
	c := BuildCascade(postURI, events)
	if c == nil {
		return
	}

	// Cascade row: CSV with quoted strings
	fmt.Fprintf(cw, "\"%s\",\"%s\",%d,%d,%d,%d,%.6f\n",
		postURI, c.Root().ActorDID, c.Root().TimeUS,
		c.Size(), c.Depth(), c.MaxOutDegree(), c.StructuralVirality(),
	)

	// Edge rows
	for i := 1; i < c.NumNodes(); i++ {
		fmt.Fprintf(ew, "\"%s\",\"%s\",%d,\"%s\"\n",
			postURI, c.Nodes[i].ActorDID, c.Nodes[i].TimeUS, c.ParentDIDOf(i),
		)
	}
}
