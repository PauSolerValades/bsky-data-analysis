package main

import "math"

// ─── Raw event from StarRocks query ──────────────────────────────────────

// RawEvent is a single row from the StarRocks query. The query guarantees:
//   1. Sorted by (subject_uri, time_us, is_repost)
//   2. The first row for a cascade is always a creation (is_repost=0)
type RawEvent struct {
	RepostURI string // empty for creation events; key for via lookups
	ActorDID  string // did of the actor (author or reposter)
	ViaURI    string // repost URI the user saw; empty for creation events
	TimeUS    int64  // microsecond timestamp
}

// ─── Cascade tree node ───────────────────────────────────────────────────

// Node is one node in the cascade tree.
type Node struct {
	ActorDID string
	TimeUS   int64
}

// ─── Cascade tree (CSR representation) ───────────────────────────────────

// Cascade is a repost tree for one post, stored in CSR format.
// Index 0 is always the root (original post creator).
type Cascade struct {
	PostURI    string
	Nodes      []Node
	ChildStart []int   // CSR offsets
	Children   []int   // flattened child indices
	parentIdx  []int   // parentIdx[i] = parent node index; parentIdx[0] = -1
}

// NumNodes returns the total number of nodes including root.
func (c *Cascade) NumNodes() int { return len(c.Nodes) }

// Root returns the root node.
func (c *Cascade) Root() Node { return c.Nodes[0] }

// ChildrenOf returns the child indices of node i.
func (c *Cascade) ChildrenOf(i int) []int {
	if i < 0 || i >= len(c.ChildStart)-1 {
		return nil
	}
	return c.Children[c.ChildStart[i]:c.ChildStart[i+1]]
}

// ParentDIDOf returns the DID of the parent of node i, or empty for root.
func (c *Cascade) ParentDIDOf(i int) string {
	if i <= 0 || i >= len(c.parentIdx) {
		return ""
	}
	p := c.parentIdx[i]
	if p < 0 || p >= len(c.Nodes) {
		return ""
	}
	return c.Nodes[p].ActorDID
}

// BuildCascade constructs a CSR cascade tree from the raw events.
func BuildCascade(postURI string, events []RawEvent) *Cascade {
	n := len(events)
	if n == 0 {
		return nil
	}

	c := &Cascade{
		PostURI:    postURI,
		Nodes:      make([]Node, n),
		ChildStart: make([]int, n+1),
		Children:   make([]int, n-1),
		parentIdx:  make([]int, n),
	}

	// Root = index 0
	c.Nodes[0] = Node{ActorDID: events[0].ActorDID, TimeUS: events[0].TimeUS}
	c.parentIdx[0] = -1

	// Map repost_uri → index for via-URI parent lookups
	uriToIdx := make(map[string]int, n)
	for i := 1; i < n; i++ {
		c.Nodes[i] = Node{ActorDID: events[i].ActorDID, TimeUS: events[i].TimeUS}
		uriToIdx[events[i].RepostURI] = i
	}

	// Pass 1: resolve parents and count children
	childCount := make([]int, n)
	for i := 1; i < n; i++ {
		parentIdx := 0 // default: attach to root
		if events[i].ViaURI != "" {
			if idx, ok := uriToIdx[events[i].ViaURI]; ok {
				parentIdx = idx
			}
		}
		c.parentIdx[i] = parentIdx
		childCount[parentIdx]++
	}

	// Build CSR prefix sum
	acc := 0
	for i := 0; i < n; i++ {
		c.ChildStart[i] = acc
		acc += childCount[i]
	}
	c.ChildStart[n] = acc

	// Pass 2: fill Children
	writePos := make([]int, n)
	for i := 1; i < n; i++ {
		parentIdx := c.parentIdx[i]
		pos := c.ChildStart[parentIdx] + writePos[parentIdx]
		c.Children[pos] = i
		writePos[parentIdx]++
	}

	return c
}

// ─── Cascade-level metrics ───────────────────────────────────────────────

// Depth returns the maximum depth from root (root = depth 0).
func (c *Cascade) Depth() int { return c.depth(0, 0) }

// Size returns the total number of nodes including root.
func (c *Cascade) Size() int { return c.NumNodes() }

// MaxOutDegree returns the maximum children count of any node.
func (c *Cascade) MaxOutDegree() int { return c.maxOutDegree(0) }

// StructuralVirality returns the Wiener-index-based structural virality ν(T).
func (c *Cascade) StructuralVirality() float64 {
	n := c.NumNodes()
	if n <= 1 {
		return 0
	}
	var crossings float64
	subtreeSizes(c, 0, n, &crossings)
	return (2.0 * crossings) / float64(n*(n-1))
}

func (c *Cascade) depth(i int, d int) int {
	maxD := d
	for _, child := range c.ChildrenOf(i) {
		cd := c.depth(child, d+1)
		if cd > maxD {
			maxD = cd
		}
	}
	return maxD
}

func (c *Cascade) maxOutDegree(i int) int {
	m := len(c.ChildrenOf(i))
	for _, child := range c.ChildrenOf(i) {
		cm := c.maxOutDegree(child)
		if cm > m {
			m = cm
		}
	}
	return m
}

// ─── Broadcast group analysis ────────────────────────────────────────────

type BroadcastGroup struct {
	PostURI          string
	ParentDID        string
	BroadcastSize    int
	MeanGapUS        float64
	MedianGapUS      float64
	GapTrend         float64
	FirstChildTimeUS int64
	LastChildTimeUS  int64
}

func (c *Cascade) BroadcastGroups() []BroadcastGroup {
	var groups []BroadcastGroup
	c.collectBroadcasts(0, &groups)
	return groups
}

func (c *Cascade) collectBroadcasts(i int, groups *[]BroadcastGroup) {
	children := c.ChildrenOf(i)
	if len(children) > 0 {
		*groups = append(*groups, c.broadcastGroup(i))
	}
	for _, child := range children {
		c.collectBroadcasts(child, groups)
	}
}

func (c *Cascade) broadcastGroup(i int) BroadcastGroup {
	children := c.ChildrenOf(i)
	k := len(children)

	bg := BroadcastGroup{
		PostURI:          c.PostURI,
		ParentDID:        c.Nodes[i].ActorDID,
		BroadcastSize:    k,
		FirstChildTimeUS: c.Nodes[children[0]].TimeUS,
		LastChildTimeUS:  c.Nodes[children[k-1]].TimeUS,
	}

	if k >= 2 {
		times := make([]float64, k)
		for j, child := range children {
			times[j] = float64(c.Nodes[child].TimeUS)
		}

		sum := 0.0
		for j := 1; j < k; j++ {
			sum += times[j] - times[j-1]
		}
		bg.MeanGapUS = sum / float64(k-1)

		gaps := make([]float64, k-1)
		for j := 1; j < k; j++ {
			gaps[j-1] = times[j] - times[j-1]
		}
		m := len(gaps)
		if m%2 == 0 {
			bg.MedianGapUS = (gaps[m/2-1] + gaps[m/2]) / 2
		} else {
			bg.MedianGapUS = gaps[m/2]
		}
		bg.GapTrend = computeGapTrend(times)
	}

	return bg
}

// ─── Root-to-leaf path analysis ──────────────────────────────────────────

type RootToLeafPath struct {
	PostURI         string
	LeafDID         string
	PathDepth       int
	PathTotalTimeUS float64
	TraversalSpeed  float64
	GapTrend        float64
}

func (c *Cascade) RootToLeafPaths() []RootToLeafPath {
	var paths []RootToLeafPath
	c.collectPaths(0, nil, &paths)
	return paths
}

func (c *Cascade) collectPaths(i int, times []float64, paths *[]RootToLeafPath) {
	children := c.ChildrenOf(i)
	if len(children) == 0 {
		p := RootToLeafPath{
			PostURI:   c.PostURI,
			LeafDID:   c.Nodes[i].ActorDID,
			PathDepth: len(times),
		}
		if len(times) > 0 {
			p.PathTotalTimeUS = float64(c.Nodes[i].TimeUS) - times[0]
		}
		if p.PathDepth > 0 {
			p.TraversalSpeed = p.PathTotalTimeUS / float64(p.PathDepth)
		}
		if len(times) >= 3 {
			p.GapTrend = computeGapTrend(times)
		}
		*paths = append(*paths, p)
		return
	}
	for _, child := range children {
		c.collectPaths(child, append(times, float64(c.Nodes[child].TimeUS)), paths)
	}
}

// ─── Post lifetime percentiles ───────────────────────────────────────────

// PostLifetime holds T_50, T_95, T_99 and time_to_peak for one cascade.
type PostLifetime struct {
	PostURI          string
	AuthorDID        string
	CreationTimeUS   int64
	LastRepostTimeUS int64
	TotalReposts     int
	T_50_US          float64
	T_95_US          float64
	T_99_US          float64
	TimeToPeakUS     float64
}

// Lifetime computes percentile timings relative to post creation.
// Returns nil for single-node cascades.
func (c *Cascade) Lifetime() *PostLifetime {
	n := c.NumNodes()
	if n <= 1 {
		return nil
	}

	N := n - 1 // repost count
	creationTime := c.Nodes[0].TimeUS
	lastTime := c.Nodes[n-1].TimeUS

	return &PostLifetime{
		PostURI:          c.PostURI,
		AuthorDID:        c.Nodes[0].ActorDID,
		CreationTimeUS:   creationTime,
		LastRepostTimeUS: lastTime,
		TotalReposts:     N,
		T_50_US:          c.repostTimeAtPct(0.50) - float64(creationTime),
		T_95_US:          c.repostTimeAtPct(0.95) - float64(creationTime),
		T_99_US:          c.repostTimeAtPct(0.99) - float64(creationTime),
		TimeToPeakUS:     c.timeToPeak() - float64(creationTime),
	}
}

// repostTimeAtPct returns the timestamp of the repost at percentile p of all
// reposts. p=0.50 → time of the median repost.
func (c *Cascade) repostTimeAtPct(p float64) float64 {
	N := c.NumNodes() - 1
	if N <= 0 {
		return float64(c.Nodes[0].TimeUS)
	}
	idx := int(math.Ceil(p*float64(N))) - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= N {
		idx = N - 1
	}
	return float64(c.Nodes[idx+1].TimeUS) // +1 because index 0 is root
}

// timeToPeak finds the densest 1% bin of repost activity and returns the
// timestamp of the first repost in that bin.
func (c *Cascade) timeToPeak() float64 {
	n := c.NumNodes()
	if n <= 2 {
		return float64(c.Nodes[0].TimeUS)
	}

	creationTime := float64(c.Nodes[0].TimeUS)
	lastTime := float64(c.Nodes[n-1].TimeUS)
	span := lastTime - creationTime
	if span <= 0 {
		return creationTime
	}

	const numBins = 100
	var bins [100]int
	var binFirstTime [100]float64
	for i := range binFirstTime {
		binFirstTime[i] = -1
	}

	for i := 1; i < n; i++ {
		t := float64(c.Nodes[i].TimeUS)
		bin := int((t - creationTime) / span * float64(numBins))
		if bin >= numBins {
			bin = numBins - 1
		}
		if bin < 0 {
			bin = 0
		}
		bins[bin]++
		if binFirstTime[bin] < 0 {
			binFirstTime[bin] = t
		}
	}

	maxBin := 0
	for b := 1; b < numBins; b++ {
		if bins[b] > bins[maxBin] {
			maxBin = b
		}
	}

	if binFirstTime[maxBin] >= 0 {
		return binFirstTime[maxBin]
	}
	return creationTime
}

// ─── Per-repost gaps ─────────────────────────────────────────────────────

// RepostGap holds per-repost inter-event timing.
type RepostGap struct {
	PostURI       string
	ReposterDID   string
	ParentDID     string
	RepostTimeUS  int64
	GlobalGapUS   float64 // time since previous repost in this cascade; -1 for first
	TopologyGapUS float64 // time since previous repost from same parent; -1 for first
}

// RawGaps returns one row per repost with global and topology gaps.
func (c *Cascade) RawGaps() []RepostGap {
	n := c.NumNodes()
	if n <= 1 {
		return nil
	}

	gaps := make([]RepostGap, n-1)
	lastTimePerParent := make(map[int]float64) // parent node index → last child time

	for i := 1; i < n; i++ {
		parentIdx := c.parentIdx[i]
		parentDID := ""
		if parentIdx >= 0 && parentIdx < n {
			parentDID = c.Nodes[parentIdx].ActorDID
		}

		thisTime := float64(c.Nodes[i].TimeUS)

		g := RepostGap{
			PostURI:      c.PostURI,
			ReposterDID:  c.Nodes[i].ActorDID,
			ParentDID:    parentDID,
			RepostTimeUS: c.Nodes[i].TimeUS,
			GlobalGapUS:  -1,
			TopologyGapUS: -1,
		}

		// Global gap: time since the immediately previous repost in the cascade
		if i > 1 {
			g.GlobalGapUS = thisTime - float64(c.Nodes[i-1].TimeUS)
		}

		// Topology gap: time since the previous repost from the same parent
		if prevTime, ok := lastTimePerParent[parentIdx]; ok {
			g.TopologyGapUS = thisTime - prevTime
		}
		lastTimePerParent[parentIdx] = thisTime

		gaps[i-1] = g
	}

	return gaps
}
