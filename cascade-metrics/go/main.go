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
		fmt.Fprintf(os.Stderr, "Usage: %s <cascades.tsv>\n", os.Args[0])
		os.Exit(1)
	}
	tsvPath := args[0]

	if err := os.MkdirAll(*outDir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "cannot create output dir: %v\n", err)
		os.Exit(1)
	}

	bf, _ := os.Create(*outDir + "/broadcast_groups_rows.csv")
	defer bf.Close()
	bw := bufio.NewWriter(bf)
	defer bw.Flush()

	pf, _ := os.Create(*outDir + "/root_to_leaf_paths_rows.csv")
	defer pf.Close()
	pw := bufio.NewWriter(pf)
	defer pw.Flush()

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
				writeCascade(currentPost, currentEvents, bw, pw)
				totalCascades++
				if totalCascades%100000 == 0 {
					fmt.Fprintf(os.Stderr, "  %d cascades...\n", totalCascades)
				}
			}
			first = false
			currentPost = subjectURI
			currentEvents = nil
		}
		currentEvents = append(currentEvents, RawEvent{
			RepostURI: repostURI, ActorDID: actorDID,
			ViaURI: viaURI, TimeUS: timeUS,
		})
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "scan error: %v\n", err)
	}
	if len(currentEvents) > 0 {
		writeCascade(currentPost, currentEvents, bw, pw)
		totalCascades++
	}

	bw.Flush()
	pw.Flush()

	fmt.Fprintf(os.Stderr, "\nDone. %d cascades.\n", totalCascades)
	fmt.Fprintf(os.Stderr, "Output: %s/broadcast_groups_rows.csv, %s/root_to_leaf_paths_rows.csv\n", *outDir, *outDir)
}

func writeCascade(postURI string, events []RawEvent, bw, pw *bufio.Writer) {
	c := BuildCascade(postURI, events)
	if c == nil {
		return
	}

	for _, g := range c.BroadcastGroups() {
		fmt.Fprintf(bw, "\"%s\",\"%s\",%d,%.6f,%.6f,%.6f,%d,%d\n",
			g.PostURI, g.ParentDID, g.BroadcastSize,
			g.MeanGapUS, g.MedianGapUS, g.GapTrend,
			g.FirstChildTimeUS, g.LastChildTimeUS,
		)
	}

	for _, p := range c.RootToLeafPaths() {
		fmt.Fprintf(pw, "\"%s\",\"%s\",%d,%.6f,%.6f,%.6f\n",
			p.PostURI, p.LeafDID, p.PathDepth,
			p.PathTotalTimeUS, p.TraversalSpeed, p.GapTrend,
		)
	}
}
