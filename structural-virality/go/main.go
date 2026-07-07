package main

import (
	"database/sql"
	"flag"
	"fmt"
	"os"
	"sort"
	"strings"

	_ "github.com/go-sql-driver/mysql"
	"github.com/parquet-go/parquet-go"
)

// ─── StarRocks query ─────────────────────────────────────────────────────
//
// The query fetches original post creations AND reposts in a single sorted
// stream. Sorting by (subject_uri, time_us, is_repost) guarantees that:
//
//  1. All events for one cascade are contiguous.
//  2. Within a cascade, events are time-ordered.
//  3. For equal timestamps, creation (is_repost=0) comes before reposts.
//
// Reposts include repost_uri and via_uri for parent resolution.
// Creations have NULL repost_uri and via_uri.

const cascadeQuery = `
SELECT
    CONCAT('at://', did, '/app.bsky.feed.post/', rkey) AS subject_uri,
    NULL AS repost_uri,
    did   AS actor_did,
    NULL AS via_uri,
    time_us,
    0    AS is_repost
FROM bsky.records
WHERE collection = 'app.bsky.feed.post'
  AND operation  = 'create'
  AND time_us    > 0
  AND did        IS NOT NULL

UNION ALL

SELECT
    subject_uri,
    CONCAT('at://', did, '/app.bsky.feed.repost/', rkey) AS repost_uri,
    did                                                   AS actor_did,
    via_uri,
    time_us,
    1                                                     AS is_repost
FROM bsky.records
WHERE collection = 'app.bsky.feed.repost'
  AND operation  = 'create'
  AND time_us    > 0
  AND subject_uri IS NOT NULL

ORDER BY subject_uri, time_us, is_repost
`

// ─── Flags ───────────────────────────────────────────────────────────────

var (
	host     = flag.String("host", "10.18.74.14", "StarRocks host")
	port     = flag.Int("port", 9030, "StarRocks port")
	user     = flag.String("user", "pau", "StarRocks user")
	password = flag.String("password", "", "StarRocks password")
	database = flag.String("database", "bsky", "StarRocks database")
	outDir   = flag.String("output", "results", "Output directory for parquet files")
)

// ─── Main ────────────────────────────────────────────────────────────────

func main() {
	flag.Parse()

	if err := os.MkdirAll(*outDir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "cannot create output dir: %v\n", err)
		os.Exit(1)
	}

	// ── Connect to StarRocks ──────────────────────────────────────────

	dsn := fmt.Sprintf("%s:%s@tcp(%s:%d)/%s?interpolateParams=true&parseTime=true",
		*user, *password, *host, *port, *database,
	)

	db, err := sql.Open("mysql", dsn)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot open database: %v\n", err)
		os.Exit(1)
	}
	defer db.Close()

	// ── Open parquet writers ──────────────────────────────────────────

	cw, err := newCascadeWriter(*outDir + "/cascades.parquet")
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot open cascades writer: %v\n", err)
		os.Exit(1)
	}
	defer cw.close()

	bw, err := newBroadcastWriter(*outDir + "/broadcast_groups.parquet")
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot open broadcast writer: %v\n", err)
		os.Exit(1)
	}
	defer bw.close()

	pw, err := newPathWriter(*outDir + "/root_to_leaf.parquet")
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot open paths writer: %v\n", err)
		os.Exit(1)
	}
	defer pw.close()

	// ── Stream query results ──────────────────────────────────────────

	fmt.Fprintf(os.Stderr, "Executing cascade query...\n")
	rows, err := db.Query(cascadeQuery)
	if err != nil {
		fmt.Fprintf(os.Stderr, "query failed: %v\n", err)
		os.Exit(1)
	}
	defer rows.Close()

	var (
		currentPost   string
		currentEvents []RawEvent
		totalCascades int64
		first         = true
	)

	for rows.Next() {
		var (
			subjectURI string
			repostURI  sql.NullString
			actorDID   string
			viaURI     sql.NullString
			timeUS     int64
			isRepost   int
		)

		if err := rows.Scan(&subjectURI, &repostURI, &actorDID, &viaURI, &timeUS, &isRepost); err != nil {
			fmt.Fprintf(os.Stderr, "scan error: %v\n", err)
			continue
		}

		event := RawEvent{
			RepostURI: nullStr(repostURI),
			ActorDID:  actorDID,
			ViaURI:    nullStr(viaURI),
			TimeUS:    timeUS,
		}

		// Detect cascade boundary (sorted by subject_uri)
		if subjectURI != currentPost {
			// Process previous cascade
			if !first {
				processCascade(currentPost, currentEvents, cw, bw, pw)
				totalCascades++
				if totalCascades%100000 == 0 {
					fmt.Fprintf(os.Stderr, "  processed %d cascades...\n", totalCascades)
				}
			}
			first = false
			currentPost = subjectURI
			currentEvents = nil
		}
		currentEvents = append(currentEvents, event)
	}

	if err := rows.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "rows iteration error: %v\n", err)
		os.Exit(1)
	}

	// Process final cascade
	if len(currentEvents) > 0 {
		processCascade(currentPost, currentEvents, cw, bw, pw)
		totalCascades++
	}

	fmt.Fprintf(os.Stderr, "\nDone. %d cascades processed.\n", totalCascades)
	fmt.Fprintf(os.Stderr, "Output written to: %s/{cascades,broadcast_groups,root_to_leaf}.parquet\n", *outDir)

	// ── Quick summary (re-read cascades.parquet) ──────────────────────

	printSummary(*outDir + "/cascades.parquet")
}

// ─── Cascade processing ──────────────────────────────────────────────────

// processCascade builds the CSR tree for one cascade, computes all three
// datasets, and writes the rows to their respective parquet writers.
func processCascade(
	postURI string,
	events []RawEvent,
	cw *cascadeWriter,
	bw *broadcastWriter,
	pw *pathWriter,
) {
	c := BuildCascade(postURI, events)
	if c == nil {
		return
	}

	// Cascade-level row
	cw.write([]CascadeRow{{
		PostURI:            postURI,
		AuthorDID:          c.Root().ActorDID,
		CreationTimeUS:     c.Root().TimeUS,
		CascadeSize:        int32(c.Size()),
		CascadeDepth:       int32(c.Depth()),
		MaxOutDegree:       int32(c.MaxOutDegree()),
		StructuralVirality: c.StructuralVirality(),
	}})

	// Broadcast groups
	for _, g := range c.BroadcastGroups() {
		bw.write([]BroadcastRow{{
			PostURI:          g.PostURI,
			ParentDID:        g.ParentDID,
			BroadcastSize:    int32(g.BroadcastSize),
			MeanGapUS:        g.MeanGapUS,
			MedianGapUS:      g.MedianGapUS,
			GapTrend:         g.GapTrend,
			FirstChildTimeUS: g.FirstChildTimeUS,
			LastChildTimeUS:  g.LastChildTimeUS,
		}})
	}

	// Root-to-leaf paths
	for _, p := range c.RootToLeafPaths() {
		pw.write([]PathRow{{
			PostURI:         p.PostURI,
			LeafDID:         p.LeafDID,
			PathDepth:       int32(p.PathDepth),
			PathTotalTimeUS: p.PathTotalTimeUS,
			TraversalSpeed:  p.TraversalSpeed,
			GapTrend:        p.GapTrend,
		}})
	}
}

// ─── Quick summary ───────────────────────────────────────────────────────

func printSummary(path string) {
	rows, err := parquet.ReadFile[CascadeRow](path)
	if err != nil {
		return
	}

	n := len(rows)
	if n == 0 {
		return
	}

	var (
		vVals    []float64
		maxV     = -1.0
		minV     = 1e308
		maxSize  int32
		maxDepth int32
		sum      float64
	)

	for _, r := range rows {
		v := r.StructuralVirality
		vVals = append(vVals, v)
		sum += v
		if v > maxV {
			maxV = v
		}
		if v < minV {
			minV = v
		}
		if r.CascadeSize > maxSize {
			maxSize = r.CascadeSize
		}
		if r.CascadeDepth > maxDepth {
			maxDepth = r.CascadeDepth
		}
	}

	sort.Float64s(vVals)
	mean := sum / float64(n)

	broadcast := 0
	for _, v := range vVals {
		if v == 1.0 {
			broadcast++
		}
	}

	fmt.Println()
	fmt.Println(strings.Repeat("─", 55))
	fmt.Println("  Structural Virality Summary")
	fmt.Println(strings.Repeat("─", 55))
	fmt.Printf("  Cascades:              %d\n", n)
	fmt.Printf("  Max cascade size:      %d\n", maxSize)
	fmt.Printf("  Max cascade depth:     %d\n", maxDepth)
	fmt.Printf("  ν range:               [%.4f, %.4f]\n", minV, maxV)
	fmt.Printf("  ν mean:                %.4f\n", mean)
	fmt.Printf("  ν p50:                 %.4f\n", vVals[n/2])
	fmt.Printf("  ν p90:                 %.4f\n", vVals[n*90/100])
	if n >= 100 {
		fmt.Printf("  ν p99:                 %.4f\n", vVals[n*99/100])
	}
	fmt.Printf("  ν = 1.0 (broadcast):   %d (%.1f%%)\n", broadcast, 100*float64(broadcast)/float64(n))
	fmt.Println(strings.Repeat("─", 55))
}

// ─── Helpers ─────────────────────────────────────────────────────────────

func nullStr(ns sql.NullString) string {
	if ns.Valid {
		return ns.String
	}
	return ""
}
