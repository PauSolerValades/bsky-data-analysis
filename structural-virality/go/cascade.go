package main

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
	ChildStart []int // CSR offsets; ChildStart[i]..ChildStart[i+1] = children of i
	Children   []int // flattened child indices
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

// BuildCascade constructs a CSR cascade tree from the raw events.
// The first event MUST be a creation (root). Remaining events are reposts.
func BuildCascade(postURI string, events []RawEvent) *Cascade {
	n := len(events)
	if n == 0 {
		return nil
	}

	c := &Cascade{
		PostURI:    postURI,
		Nodes:      make([]Node, n),
		ChildStart: make([]int, n+1),
		Children:   make([]int, n-1), // all events except root are children
	}

	// Root = index 0
	c.Nodes[0] = Node{ActorDID: events[0].ActorDID, TimeUS: events[0].TimeUS}

	// Map repost_uri → index for via-URI parent lookups.
	// Also map actor_did → index (the *last* occurrence wins, which is fine
	// because a user reposting the same post multiple times is rare and any
	// via reference to them will point to the latest repost URI anyway).
	uriToIdx := make(map[string]int, n)

	for i := 1; i < n; i++ {
		c.Nodes[i] = Node{ActorDID: events[i].ActorDID, TimeUS: events[i].TimeUS}
		uriToIdx[events[i].RepostURI] = i
	}

	// Pass 1: count children per parent
	childCount := make([]int, n)
	for i := 1; i < n; i++ {
		parentIdx := 0 // default: attach to root
		if events[i].ViaURI != "" {
			if idx, ok := uriToIdx[events[i].ViaURI]; ok {
				parentIdx = idx
			}
		}
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
		parentIdx := 0
		if events[i].ViaURI != "" {
			if idx, ok := uriToIdx[events[i].ViaURI]; ok {
				parentIdx = idx
			}
		}
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

// StructuralVirality returns the Wiener-index-based structural virality ν(T)
// from Goel et al. (2016). Uses the subtree-crossing formula:
//
//	ν(T) = 2 · Σ (sub · (n - sub)) / (n · (n - 1))
//
// where the sum is over all edges (parent→child) and sub is the size of the
// child's subtree. Single-node cascades return 0.
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

// BroadcastGroup holds metrics for a single parent's children within a cascade.
type BroadcastGroup struct {
	PostURI         string
	ParentDID       string
	BroadcastSize   int
	MeanGapUS       float64
	MedianGapUS     float64
	GapTrend        float64
	FirstChildTimeUS int64
	LastChildTimeUS  int64
}

// BroadcastGroups computes broadcast group metrics for every node with ≥1 child.
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

	// Children are already time-sorted because we process reposts in time_us order.
	bg := BroadcastGroup{
		PostURI:          c.PostURI,
		ParentDID:        c.Nodes[i].ActorDID,
		BroadcastSize:    k,
		FirstChildTimeUS: c.Nodes[children[0]].TimeUS,
		LastChildTimeUS:  c.Nodes[children[k-1]].TimeUS,
	}

	if k >= 2 {
		// Build sorted time array for gap computation
		times := make([]float64, k)
		for j, child := range children {
			times[j] = float64(c.Nodes[child].TimeUS)
		}

		sum := 0.0
		for j := 1; j < k; j++ {
			sum += times[j] - times[j-1]
		}
		bg.MeanGapUS = sum / float64(k-1)

		// Median gap
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

// RootToLeafPath holds metrics for one path from root to a leaf node.
type RootToLeafPath struct {
	PostURI        string
	LeafDID        string
	PathDepth      int
	PathTotalTimeUS float64
	TraversalSpeed float64
	GapTrend       float64
}

// RootToLeafPaths computes all root-to-leaf path metrics.
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
