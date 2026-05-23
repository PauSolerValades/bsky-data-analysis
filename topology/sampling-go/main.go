package main

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"math/rand/v2"
	"os"
	"runtime"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/parquet-go/parquet-go"
)

// ── Types ──────────────────────────────────────────────────────────────────

type CSR struct {
	OutAdj [][]int32
	InAdj  [][]int32
}

type ForestFire struct {
	csr         *CSR
	pF, pB      float64
	rng         *rand.Rand
	numNodes    int
	visited     []bool
	queue       []int32
	visitedList []int32
	burnedEdges []Edge
}

type Edge struct{ A, S int32 }

// ── Binary edge loading ────────────────────────────────────────────────────

func loadEdges(path string) ([]Edge, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	info, _ := f.Stat()
	numEdges := int(info.Size() / 16)
	edges := make([]Edge, numEdges)

	buf := make([]byte, 16*1024*1024)
	var idx int
	for {
		n, err := f.Read(buf)
		if n == 0 {
			break
		}
		if err != nil && err.Error() != "EOF" {
			return nil, err
		}
		for i := 0; i < n; i += 16 {
			edges[idx] = Edge{
				A: int32(binary.LittleEndian.Uint64(buf[i : i+8])),
				S: int32(binary.LittleEndian.Uint64(buf[i+8 : i+16])),
			}
			idx++
		}
	}
	return edges[:idx], nil
}

// ── CSR builder ────────────────────────────────────────────────────────────

func buildCSR(edges []Edge, numNodes int) *CSR {
	fmt.Printf("  Building CSR for %d nodes, %d edges ...\n", numNodes, len(edges))
	t0 := time.Now()

	outDeg := make([]int32, numNodes)
	inDeg := make([]int32, numNodes)
	for _, e := range edges {
		outDeg[e.A]++
		inDeg[e.S]++
	}

	csr := &CSR{
		OutAdj: make([][]int32, numNodes),
		InAdj:  make([][]int32, numNodes),
	}
	for i := 0; i < numNodes; i++ {
		csr.OutAdj[i] = make([]int32, 0, outDeg[i])
		csr.InAdj[i] = make([]int32, 0, inDeg[i])
	}
	for _, e := range edges {
		csr.OutAdj[e.A] = append(csr.OutAdj[e.A], e.S)
		csr.InAdj[e.S] = append(csr.InAdj[e.S], e.A)
	}

	fmt.Printf("  CSR ready in %.1fs\n", time.Since(t0).Seconds())
	return csr
}

// ── Forest Fire ────────────────────────────────────────────────────────────

var TargetSizes = []int{
	10_000, 50_000, 100_000, 250_000, 500_000, 750_000, 1_000_000,
}

func NewForestFire(csr *CSR, pF, pB float64, seed int64) *ForestFire {
	return &ForestFire{
		csr:         csr,
		pF:          pF,
		pB:          pB,
		rng:         rand.New(rand.NewPCG(uint64(seed), uint64(seed>>32+1))),
		numNodes:    len(csr.OutAdj),
		visited:     make([]bool, len(csr.OutAdj)),
		queue:       make([]int32, 0, 100_000),
		visitedList: make([]int32, 0, TargetSizes[len(TargetSizes)-1]),
		burnedEdges: make([]Edge, 0, TargetSizes[len(TargetSizes)-1]),
	}
}

func (ff *ForestFire) geometricSample(p float64, cap int) int {
	if p <= 0 || cap <= 0 {
		return 0
	}
	if p >= 1.0 {
		return cap
	}
	u := ff.rng.Float64()
	if u >= 1.0 {
		u = 0.999999
	}
	n := int(math.Ceil(math.Log(1-u) / math.Log(1-p)))
	if n > cap {
		return cap
	}
	return n
}

func (ff *ForestFire) burn(node int32, neighbors []int32, p float64) []int32 {
	var unvisited []int32
	for _, n := range neighbors {
		if !ff.visited[n] {
			unvisited = append(unvisited, n)
		}
	}
	if len(unvisited) == 0 {
		return nil
	}
	k := ff.geometricSample(p, len(unvisited))
	if k == 0 {
		return nil
	}
	if k >= len(unvisited) {
		return unvisited
	}
	result := make([]int32, k)
	for i := 0; i < k; i++ {
		j := ff.rng.IntN(len(unvisited) - i)
		result[i] = unvisited[j]
		unvisited[j] = unvisited[len(unvisited)-1-i]
	}
	return result
}

func (ff *ForestFire) randomUnvisited() int32 {
	for {
		c := int32(ff.rng.IntN(ff.numNodes))
		if !ff.visited[c] {
			return c
		}
	}
}

func (ff *ForestFire) Run(resultsDir string, dids []string, params map[string]any, startTime time.Time) {
	lastTargetIdx := len(TargetSizes) - 1
	nextTarget := 0

	// Seed
	seed := int32(ff.rng.IntN(ff.numNodes))
	ff.visited[seed] = true
	ff.queue = append(ff.queue, seed)
	ff.visitedList = append(ff.visitedList, seed)

	var visitedCount int64 = 1
	fmt.Printf("  Burning (target %d nodes) ...\n", TargetSizes[lastTargetIdx])

	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	go func() {
		for range ticker.C {
			vc := atomic.LoadInt64(&visitedCount)
			elapsed := time.Since(startTime).Seconds()
			fmt.Printf("    %d nodes visited (%.0f nodes/s)\n", vc, float64(vc)/elapsed)
		}
	}()

	for nextTarget < len(TargetSizes) {
		target := TargetSizes[nextTarget]

		for atomic.LoadInt64(&visitedCount) < int64(target) {
			if len(ff.queue) > 0 {
				v := ff.queue[0]
				ff.queue = ff.queue[1:]

				burnedOut := ff.burn(v, ff.csr.OutAdj[v], ff.pF)
				for _, w := range burnedOut {
					ff.visited[w] = true
					ff.burnedEdges = append(ff.burnedEdges, Edge{v, w})
					ff.queue = append(ff.queue, w)
					ff.visitedList = append(ff.visitedList, w)
					atomic.AddInt64(&visitedCount, 1)
				}

				if ff.pB > 0 {
					burnedIn := ff.burn(v, ff.csr.InAdj[v], ff.pB)
					for _, w := range burnedIn {
						ff.visited[w] = true
						ff.burnedEdges = append(ff.burnedEdges, Edge{w, v})
						ff.queue = append(ff.queue, w)
						ff.visitedList = append(ff.visitedList, w)
						atomic.AddInt64(&visitedCount, 1)
					}
				}
			} else {
				newSeed := ff.randomUnvisited()
				ff.visited[newSeed] = true
				ff.queue = append(ff.queue, newSeed)
				ff.visitedList = append(ff.visitedList, newSeed)
				atomic.AddInt64(&visitedCount, 1)
			}
		}

		vc := atomic.LoadInt64(&visitedCount)
		fmt.Printf("\n  === Snapshot %d nodes (actual: %d) ===\n", target, vc)

		// Write snapshot: nodes, burned edges, induced edges (streamed)
		ff.writeSnapshot(resultsDir, target, int(vc), dids, params)
		nextTarget++
	}
}

// writeSnapshot writes nodes, burned_edges, and induced_edges to Parquet.
// Induced edges are computed and streamed in chunks — never fully in memory.
func (ff *ForestFire) writeSnapshot(resultsDir string, target, vc int,
	dids []string, params map[string]any) {

	dir := fmt.Sprintf("%s/%d", resultsDir, target)
	os.MkdirAll(dir, 0755)

	t0 := time.Now()

	// ── Nodes ─────────────────────────────────────────────────────────
	sorted := make([]int32, vc)
	copy(sorted, ff.visitedList[:vc])
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })

	nodeRows := make([]NodeRow, vc)
	for i, v := range sorted {
		nodeRows[i] = NodeRow{DID: dids[v], IntID: v}
	}
	writeParquetRows(dir+"/nodes.parquet", nodeRows)
	fmt.Printf("    nodes: %d (%.1fs)\n", vc, time.Since(t0).Seconds())

	// ── Burned edges ──────────────────────────────────────────────────
	t1 := time.Now()
	burnedRows := make([]EdgeRow, len(ff.burnedEdges))
	for i, e := range ff.burnedEdges {
		burnedRows[i] = EdgeRow{
			ActorDID: dids[e.A], SubjectDID: dids[e.S],
			ActorID: e.A, SubjectID: e.S,
		}
	}
	writeParquetRows(dir+"/burned_edges.parquet", burnedRows)
	fmt.Printf("    burned edges: %d (%.1fs)\n", len(burnedRows), time.Since(t1).Seconds())

	// ── Induced edges — STREAMED ──────────────────────────────────────
	t2 := time.Now()
	inducedCount := ff.writeInducedParquet(dir+"/induced_edges.parquet", dids)
	fmt.Printf("    induced edges: %d (%.1fs)\n", inducedCount, time.Since(t2).Seconds())

	// ── Meta ──────────────────────────────────────────────────────────
	meta := map[string]any{
		"algorithm":     "ForestFire",
		"target_size":   target,
		"actual_nodes":  vc,
		"burned_edges":  len(ff.burnedEdges),
		"induced_edges": inducedCount,
		"p_f":           params["p_f"],
		"p_b":           params["p_b"],
		"seed":          params["seed"],
		"timestamp":     time.Now().Format(time.RFC3339),
	}
	b, _ := json.MarshalIndent(meta, "", "  ")
	os.WriteFile(dir+"/meta.json", b, 0644)
}

// ── Streaming induced edges ────────────────────────────────────────────────

const rowGroupSize = 5_000_000 // edges per row group — keeps memory ~100MB

func (ff *ForestFire) writeInducedParquet(path string, dids []string) int {
	f, err := os.Create(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR creating %s: %v\n", path, err)
		return 0
	}
	defer f.Close()

	// Build a sorted copy of visited for deterministic chunking
	visitedSorted := make([]int32, len(ff.visitedList))
	copy(visitedSorted, ff.visitedList)
	sort.Slice(visitedSorted, func(i, j int) bool { return visitedSorted[i] < visitedSorted[j] })

	type localEdge struct{ a, s int32 }
	ch := make(chan []localEdge, 32) // buffered channel of batches

	// Producer: parallel workers find induced edges, send batches
	numWorkers := 32
	chunkSize := (len(visitedSorted) + numWorkers - 1) / numWorkers
	var wg sync.WaitGroup

	for w := 0; w < numWorkers; w++ {
		start := w * chunkSize
		end := start + chunkSize
		if end > len(visitedSorted) {
			end = len(visitedSorted)
		}
		if start >= end {
			continue
		}
		wg.Add(1)
		go func(start, end int) {
			defer wg.Done()
			batch := make([]localEdge, 0, rowGroupSize/numWorkers+1000)
			for i := start; i < end; i++ {
				v := visitedSorted[i]
				for _, w := range ff.csr.OutAdj[v] {
					if ff.visited[w] {
						batch = append(batch, localEdge{v, w})
						if len(batch) >= rowGroupSize/numWorkers {
							ch <- batch
							batch = make([]localEdge, 0, rowGroupSize/numWorkers+1000)
						}
					}
				}
			}
			if len(batch) > 0 {
				ch <- batch
			}
		}(start, end)
	}

	// Closer
	go func() {
		wg.Wait()
		close(ch)
	}()

	// Consumer: write batches as Parquet row groups
	schema := parquet.SchemaOf(EdgeRow{})
	writer := parquet.NewGenericWriter[EdgeRow](f, schema,
		parquet.MaxRowsPerRowGroup(int64(rowGroupSize)),
		parquet.Compression(&parquet.Zstd),
	)

	total := 0
	rowBuf := make([]EdgeRow, 0, rowGroupSize)

	for batch := range ch {
		for _, e := range batch {
			rowBuf = append(rowBuf, EdgeRow{
				ActorDID: dids[e.a], SubjectDID: dids[e.s],
				ActorID: e.a, SubjectID: e.s,
			})
			total++
		}
		// Flush if buffer is large enough
		if len(rowBuf) >= rowGroupSize {
			if _, err := writer.Write(rowBuf); err != nil {
				fmt.Fprintf(os.Stderr, "ERROR writing induced: %v\n", err)
				return total
			}
			rowBuf = rowBuf[:0]
		}
	}
	// Final flush
	if len(rowBuf) > 0 {
		if _, err := writer.Write(rowBuf); err != nil {
			fmt.Fprintf(os.Stderr, "ERROR writing induced final: %v\n", err)
			return total
		}
	}
	writer.Close()
	return total
}

// ── Parquet helpers ────────────────────────────────────────────────────────

type NodeRow struct {
	DID   string `parquet:"did"`
	IntID int32  `parquet:"int_id"`
}

type EdgeRow struct {
	ActorDID   string `parquet:"actor_did"`
	SubjectDID string `parquet:"subject_did"`
	ActorID    int32  `parquet:"actor_id"`
	SubjectID  int32  `parquet:"subject_id"`
}

func writeParquetRows[T any](path string, rows []T) {
	f, err := os.Create(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR creating %s: %v\n", path, err)
		return
	}
	defer f.Close()
	parquet.Write[T](f, rows)
}

// ── Main ───────────────────────────────────────────────────────────────────

func main() {
	pF := 0.5
	pB := 0.2
	seed := int64(42)
	dataDir := "data"
	resultsDir := "results"

	for i := 1; i < len(os.Args); i++ {
		switch os.Args[i] {
		case "--p-f":
			i++; fmt.Sscanf(os.Args[i], "%f", &pF)
		case "--p-b":
			i++; fmt.Sscanf(os.Args[i], "%f", &pB)
		case "--seed":
			i++; fmt.Sscanf(os.Args[i], "%d", &seed)
		case "--data":
			i++; dataDir = os.Args[i]
		case "--out":
			i++; resultsDir = os.Args[i]
		}
	}

	fmt.Println("============================================================")
	fmt.Println("Forest Fire Graph Sampling — Bluesky Social Graph (Go)")
	fmt.Println("============================================================")
	fmt.Printf("  p_f=%v  p_b=%v  seed=%d\n", pF, pB, seed)
	fmt.Printf("  targets: %v\n", TargetSizes)
	fmt.Println()

	t0 := time.Now()

	// ── Load DIDs ───────────────────────────────────────────────────────
	fmt.Println("[1] Loading DIDs ...")
	dids, err := loadDIDs(dataDir + "/dids.txt")
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("  %d DIDs (%.1fs)\n", len(dids), time.Since(t0).Seconds())

	// ── Load edges ──────────────────────────────────────────────────────
	tLoad := time.Now()
	fmt.Println("[2] Loading edges ...")
	edges, err := loadEdges(dataDir + "/edges.bin")
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("  %d edges (%.1fs)\n", len(edges), time.Since(tLoad).Seconds())

	// ── Build CSR ───────────────────────────────────────────────────────
	csr := buildCSR(edges, len(dids))
	edges = nil
	runtime.GC()

	// ── Forest Fire ─────────────────────────────────────────────────────
	fmt.Println("[3] Forest Fire ...")
	ff := NewForestFire(csr, pF, pB, seed)
	params := map[string]any{"p_f": pF, "p_b": pB, "seed": seed}
	ff.Run(resultsDir, dids, params, t0)

	fmt.Printf("\n============================================================\n")
	fmt.Printf("Done in %.1f min\n", time.Since(t0).Minutes())
}

func loadDIDs(path string) ([]string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	lines := make([]string, 0, 28_000_000)
	start := 0
	for i := 0; i < len(data); i++ {
		if data[i] == '\n' {
			lines = append(lines, string(data[start:i]))
			start = i + 1
		}
	}
	if start < len(data) {
		lines = append(lines, string(data[start:]))
	}
	return lines, nil
}
