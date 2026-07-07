package main

import (
	"bufio"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/parquet-go/parquet-go"
)

// ─── Row types ───────────────────────────────────────────────────────────

type CascadeRow struct {
	PostURI            string  `parquet:"post_uri"`
	AuthorDID          string  `parquet:"author_did"`
	CreationTimeUS     int64   `parquet:"creation_time_us"`
	CascadeSize        int32   `parquet:"cascade_size"`
	CascadeDepth       int32   `parquet:"cascade_depth"`
	MaxOutDegree       int32   `parquet:"max_out_degree"`
	StructuralVirality float64 `parquet:"structural_virality"`
}

type BroadcastRow struct {
	PostURI          string  `parquet:"post_uri"`
	ParentDID        string  `parquet:"parent_did"`
	BroadcastSize    int32   `parquet:"broadcast_size"`
	MeanGapUS        float64 `parquet:"mean_gap_us"`
	MedianGapUS      float64 `parquet:"median_gap_us"`
	GapTrend         float64 `parquet:"gap_trend"`
	FirstChildTimeUS int64   `parquet:"first_child_time_us"`
	LastChildTimeUS  int64   `parquet:"last_child_time_us"`
}

type PathRow struct {
	PostURI         string  `parquet:"post_uri"`
	LeafDID         string  `parquet:"leaf_did"`
	PathDepth       int32   `parquet:"path_depth"`
	PathTotalTimeUS float64 `parquet:"path_total_time_us"`
	TraversalSpeed  float64 `parquet:"traversal_speed"`
	GapTrend        float64 `parquet:"gap_trend"`
}

type LifetimeRow struct {
	PostURI          string  `parquet:"post_uri"`
	AuthorDID        string  `parquet:"author_did"`
	CreationTimeUS   int64   `parquet:"creation_time_us"`
	LastRepostTimeUS int64   `parquet:"last_repost_time_us"`
	TotalReposts     int32   `parquet:"total_reposts"`
	T_50_US          float64 `parquet:"T_50_us"`
	T_95_US          float64 `parquet:"T_95_us"`
	T_99_US          float64 `parquet:"T_99_us"`
	TimeToPeakUS     float64 `parquet:"time_to_peak_us"`
}

type GapRow struct {
	PostURI       string  `parquet:"post_uri"`
	ReposterDID   string  `parquet:"reposter_did"`
	ParentDID     string  `parquet:"parent_did"`
	RepostTimeUS  int64   `parquet:"repost_time_us"`
	GlobalGapUS   float64 `parquet:"global_gap_us"`
	TopologyGapUS float64 `parquet:"topology_gap_us"`
}

// ─── Generic parquet writer ──────────────────────────────────────────────

type parquetWriter[T any] struct {
	w *parquet.GenericWriter[T]
	f *os.File
}

func newParquetWriter[T any](path string) (*parquetWriter[T], error) {
	f, err := os.Create(path)
	if err != nil {
		return nil, err
	}
	return &parquetWriter[T]{
		w: parquet.NewGenericWriter[T](f),
		f: f,
	}, nil
}

func (pw *parquetWriter[T]) write(rows []T) error {
	_, err := pw.w.Write(rows)
	return err
}

func (pw *parquetWriter[T]) close() error {
	if err := pw.w.Close(); err != nil {
		return err
	}
	return pw.f.Close()
}

// ─── Flags ───────────────────────────────────────────────────────────────

var outDir = flag.String("output", "results", "Output directory for parquet files")

// ─── Main ────────────────────────────────────────────────────────────────

func main() {
	flag.Parse()

	args := flag.Args()
	if len(args) < 1 {
		fmt.Fprintf(os.Stderr, "Usage: %s [-output dir] <reposts.tsv>\n", os.Args[0])
		os.Exit(1)
	}
	tsvPath := args[0]

	if err := os.MkdirAll(*outDir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "cannot create output dir: %v\n", err)
		os.Exit(1)
	}

	// ── Open parquet writers ──────────────────────────────────────────

	cw, err := newParquetWriter[CascadeRow](*outDir + "/cascades.parquet")
	if err != nil {
		fmt.Fprintf(os.Stderr, "cascades writer: %v\n", err)
		os.Exit(1)
	}
	defer cw.close()

	bw, err := newParquetWriter[BroadcastRow](*outDir + "/broadcast_groups.parquet")
	if err != nil {
		fmt.Fprintf(os.Stderr, "broadcast writer: %v\n", err)
		os.Exit(1)
	}
	defer bw.close()

	pw, err := newParquetWriter[PathRow](*outDir + "/root_to_leaf_paths.parquet")
	if err != nil {
		fmt.Fprintf(os.Stderr, "paths writer: %v\n", err)
		os.Exit(1)
	}
	defer pw.close()

	lw, err := newParquetWriter[LifetimeRow](*outDir + "/post_lifetime.parquet")
	if err != nil {
		fmt.Fprintf(os.Stderr, "lifetime writer: %v\n", err)
		os.Exit(1)
	}
	defer lw.close()

	gw, err := newParquetWriter[GapRow](*outDir + "/repost_gaps.parquet")
	if err != nil {
		fmt.Fprintf(os.Stderr, "gaps writer: %v\n", err)
		os.Exit(1)
	}
	defer gw.close()

	// ── Read TSV ──────────────────────────────────────────────────────

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

		// Columns: subject_uri, repost_uri, via_uri, actor_did, time_us, is_repost
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

		// Normalize NULL markers
		if repostURI == `\N` || repostURI == "NULL" {
			repostURI = ""
		}
		if viaURI == `\N` || viaURI == "NULL" {
			viaURI = ""
		}

		if subjectURI != currentPost {
			if !first {
				processCascade(currentPost, currentEvents, cw, bw, pw, lw, gw)
				totalCascades++
				if totalCascades%100000 == 0 {
					fmt.Fprintf(os.Stderr, "  processed %d cascades (line %d)...\n", totalCascades, lineNo)
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
		os.Exit(1)
	}

	if len(currentEvents) > 0 {
		processCascade(currentPost, currentEvents, cw, bw, pw, lw, gw)
		totalCascades++
	}

	fmt.Fprintf(os.Stderr, "\nDone. %d cascades processed from %d lines.\n", totalCascades, lineNo)
	fmt.Fprintf(os.Stderr, "Output written to %s/{cascades,broadcast_groups,root_to_leaf_paths,post_lifetime,repost_gaps}.parquet\n", *outDir)
}

// ─── Cascade processing ──────────────────────────────────────────────────

func processCascade(
	postURI string,
	events []RawEvent,
	cw *parquetWriter[CascadeRow],
	bw *parquetWriter[BroadcastRow],
	pw *parquetWriter[PathRow],
	lw *parquetWriter[LifetimeRow],
	gw *parquetWriter[GapRow],
) {
	c := BuildCascade(postURI, events)
	if c == nil {
		return
	}

	// 1. Cascade-level
	cw.write([]CascadeRow{{
		PostURI:            postURI,
		AuthorDID:          c.Root().ActorDID,
		CreationTimeUS:     c.Root().TimeUS,
		CascadeSize:        int32(c.Size()),
		CascadeDepth:       int32(c.Depth()),
		MaxOutDegree:       int32(c.MaxOutDegree()),
		StructuralVirality: c.StructuralVirality(),
	}})

	// 2. Broadcast groups
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

	// 3. Root-to-leaf paths
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

	// 4. Post lifetime
	if lt := c.Lifetime(); lt != nil {
		lw.write([]LifetimeRow{{
			PostURI:          lt.PostURI,
			AuthorDID:        lt.AuthorDID,
			CreationTimeUS:   lt.CreationTimeUS,
			LastRepostTimeUS: lt.LastRepostTimeUS,
			TotalReposts:     int32(lt.TotalReposts),
			T_50_US:          lt.T_50_US,
			T_95_US:          lt.T_95_US,
			T_99_US:          lt.T_99_US,
			TimeToPeakUS:     lt.TimeToPeakUS,
		}})
	}

	// 5. Repost gaps
	for _, g := range c.RawGaps() {
		gw.write([]GapRow{{
			PostURI:       g.PostURI,
			ReposterDID:   g.ReposterDID,
			ParentDID:     g.ParentDID,
			RepostTimeUS:  g.RepostTimeUS,
			GlobalGapUS:   g.GlobalGapUS,
			TopologyGapUS: g.TopologyGapUS,
		}})
	}
}


