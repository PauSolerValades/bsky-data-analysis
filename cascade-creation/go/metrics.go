package main

// ─── Shared helpers ──────────────────────────────────────────────────────

// subtreeSizes does a post-order traversal: for each edge, it accumulates
// sub * (total - sub) into crossings, where sub is the size of the subtree
// below that edge.
func subtreeSizes(c *Cascade, node int, total int, crossings *float64) int {
	size := 1
	for _, child := range c.ChildrenOf(node) {
		sub := subtreeSizes(c, child, total, crossings)
		*crossings += float64(sub * (total - sub))
		size += sub
	}
	return size
}

// computeGapTrend computes the slope of gap values over position via linear
// regression. Returns 0 for fewer than 3 points (slope needs ≥2 points; but
// we require ≥3 for a meaningful trend so there are at least 2 gaps).
func computeGapTrend(times []float64) float64 {
	k := len(times)
	if k < 3 {
		return 0
	}
	n := float64(k - 1) // number of gaps
	var sumX, sumY, sumXY, sumX2 float64
	for i := 1; i < k; i++ {
		x := float64(i)
		y := times[i] - times[i-1]
		sumX += x
		sumY += y
		sumXY += x * y
		sumX2 += x * x
	}
	denom := n*sumX2 - sumX*sumX
	if denom < 1e-10 && denom > -1e-10 {
		return 0
	}
	return (n*sumXY - sumX*sumY) / denom
}
