package main

import (
	"encoding/csv"
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
)

// Repost is a single repost event from the dump.
type Repost struct {
	SubjectURI string // original post URI
	RepostURI  string // URI of this repost record (key for via lookups)
	ViaURI     string // parent repost URI, empty if direct
	ActorDID   string // who reposted
	TimeUS     int64  // event timestamp (microseconds)
}

// Node in a cascade tree.
type Node struct {
	URI      string
	Actor    string
	Children []*Node
}

// extractDID pulls the DID from an AT URI like at://did:plc:xxx/app.bsky.feed.post/rkey
func extractDID(uri string) string {
	uri = strings.TrimPrefix(uri, "at://")
	idx := strings.IndexByte(uri, '/')
	if idx < 0 {
		return uri
	}
	return uri[:idx]
}

// subtreeMoments computes size, sum of sizes, and sum of squared sizes
// for the subtree rooted at node, in a single post-order pass.
func subtreeMoments(node *Node) (size, sumSizes, sumSizesSqr int64) {
	if len(node.Children) == 0 {
		return 1, 1, 1
	}
	size = 1
	for _, child := range node.Children {
		cs, css, cssq := subtreeMoments(child)
		size += cs
		sumSizes += css
		sumSizesSqr += cssq
	}
	sumSizes += size
	sumSizesSqr += size * size
	return
}

// virality computes the structural virality ν(T) from Wiener index / subtree moments.
// Formula from Goel et al. (2016):
//
//	ν(T) = (2n / (n-1)) · (ΣS_i / n  −  ΣS_i² / n²)
//
// where S_i is the size of the subtree rooted at node i,
// and n is the total number of nodes.
func virality(n, sumSizes, sumSizesSqr int64) float64 {
	if n <= 1 {
		return 0.0
	}
	fn := float64(n)
	return (2.0 * fn / (fn - 1.0)) *
		(float64(sumSizes)/fn - float64(sumSizesSqr)/(fn*fn))
}

// maxDepth computes the maximum depth of the tree (root = depth 0).
func maxDepth(node *Node) int {
	if len(node.Children) == 0 {
		return 0
	}
	md := 0
	for _, child := range node.Children {
		d := maxDepth(child)
		if d > md {
			md = d
		}
	}
	return md + 1
}

// buildCascade builds the cascade tree for a single post and computes ν.
func buildCascade(postURI string, reposts []Repost) (size int64, vir float64, depth int) {
	// Root = post creator
	creator := extractDID(postURI)
	root := &Node{URI: postURI, Actor: creator}

	// Map repost_uri -> node for via lookups
	nodeByURI := make(map[string]*Node, len(reposts))

	for _, r := range reposts {
		node := &Node{URI: r.RepostURI, Actor: r.ActorDID}

		// Find parent
		parent := root
		if r.ViaURI != "" {
			if p, ok := nodeByURI[r.ViaURI]; ok {
				parent = p
			}
			// else: via points to unknown repost → attach to root
		}
		parent.Children = append(parent.Children, node)
		nodeByURI[r.RepostURI] = node
	}

	n, ss, ssq := subtreeMoments(root)
	return n, virality(n, ss, ssq), maxDepth(root)
}

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintf(os.Stderr, "Usage: %s <reposts.tsv> <output.csv>\n", os.Args[0])
		os.Exit(1)
	}
	inputPath := os.Args[1]
	outputPath := os.Args[2]

	f, err := os.Open(inputPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error opening input: %v\n", err)
		os.Exit(1)
	}
	defer f.Close()

	// Read tab-separated input streamed by subject_uri, time_us
	reader := csv.NewReader(f)
	reader.Comma = '\t'
	reader.LazyQuotes = true
	reader.FieldsPerRecord = 5

	out, err := os.Create(outputPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error creating output: %v\n", err)
		os.Exit(1)
	}
	defer out.Close()

	writer := csv.NewWriter(out)
	defer writer.Flush()
	writer.Write([]string{"post_uri", "cascade_size", "structural_virality", "max_depth"})

	var currentPost string
	var currentReposts []Repost
	lineNo := 0
	totalPosts := 0

	for {
		record, err := reader.Read()
		if err != nil {
			if err.Error() == "EOF" {
				break
			}
			// Skip malformed lines
			lineNo++
			continue
		}
		lineNo++

		// Columns: subject_uri, repost_uri, via_uri, actor_did, time_us
		if len(record) < 5 {
			continue
		}
		subjectURI := record[0]
		repostURI := record[1]
		viaURI := record[2]
		actorDID := record[3]
		timeStr := record[4]

		// Handle MySQL \N for NULL
		if viaURI == `\N` || viaURI == "NULL" {
			viaURI = ""
		}
		if timeStr == `\N` || timeStr == "" {
			continue
		}

		timeUS, err := strconv.ParseInt(timeStr, 10, 64)
		if err != nil {
			continue
		}

		r := Repost{
			SubjectURI: subjectURI,
			RepostURI:  repostURI,
			ViaURI:     viaURI,
			ActorDID:   actorDID,
			TimeUS:     timeUS,
		}

		// Detect post boundary (data is sorted by subject_uri, time_us)
		if subjectURI != currentPost {
			// Process previous post
			if len(currentReposts) > 0 {
				size, vir, depth := buildCascade(currentPost, currentReposts)
				writer.Write([]string{
					currentPost,
					strconv.FormatInt(size, 10),
					strconv.FormatFloat(vir, 'f', 6, 64),
					strconv.Itoa(depth),
				})
				totalPosts++
				if totalPosts%100000 == 0 {
					fmt.Fprintf(os.Stderr, "  processed %d posts...\n", totalPosts)
				}
			}
			currentPost = subjectURI
			currentReposts = nil
		}
		currentReposts = append(currentReposts, r)
	}

	// Process last post
	if len(currentReposts) > 0 {
		size, vir, depth := buildCascade(currentPost, currentReposts)
		writer.Write([]string{
			currentPost,
			strconv.FormatInt(size, 10),
			strconv.FormatFloat(vir, 'f', 6, 64),
			strconv.Itoa(depth),
		})
		totalPosts++
	}

	// Summary stats
	fmt.Fprintf(os.Stderr, "Lines read: %d\n", lineNo)
	fmt.Fprintf(os.Stderr, "Posts with ≥1 repost: %d\n", totalPosts)
	fmt.Fprintf(os.Stderr, "Output written to: %s\n", outputPath)

	// Compute quick summary of ν distribution
	if totalPosts > 0 {
		fmt.Println("\n--- Quick summary (re-run with plots for full analysis) ---")
		// Re-read output to compute stats
		out.Close()
		summaryFile, _ := os.Open(outputPath)
		defer summaryFile.Close()
		summaryReader := csv.NewReader(summaryFile)
		summaryReader.Read() // skip header
		var vVals []float64
		maxV, minV := -1.0, math.MaxFloat64
		maxSize := int64(0)
		for {
			rec, err := summaryReader.Read()
			if err != nil {
				break
			}
			if len(rec) < 3 {
				continue
			}
			v, _ := strconv.ParseFloat(rec[2], 64)
			sz, _ := strconv.ParseInt(rec[1], 10, 64)
			vVals = append(vVals, v)
			if v > maxV {
				maxV = v
			}
			if v < minV {
				minV = v
			}
			if sz > maxSize {
				maxSize = sz
			}
		}

		sort.Float64s(vVals)
		n := len(vVals)
		mean := 0.0
		for _, v := range vVals {
			mean += v
		}
		mean /= float64(n)

		fmt.Printf("Cascades: %d\n", n)
		fmt.Printf("Max cascade size: %d\n", maxSize)
		fmt.Printf("ν range: [%.4f, %.4f]\n", minV, maxV)
		fmt.Printf("ν mean:  %.4f\n", mean)
		fmt.Printf("ν p50:   %.4f\n", vVals[n/2])
		fmt.Printf("ν p90:   %.4f\n", vVals[n*90/100])
		fmt.Printf("ν p99:   %.4f\n", vVals[n*99/100])
	}
}
