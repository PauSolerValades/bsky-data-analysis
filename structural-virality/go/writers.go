package main

import (
	"os"

	"github.com/parquet-go/parquet-go"
)

// ─── Row types ───────────────────────────────────────────────────────────

// CascadeRow is one row in cascades.parquet.
type CascadeRow struct {
	PostURI            string  `parquet:"post_uri"`
	AuthorDID          string  `parquet:"author_did"`
	CreationTimeUS     int64   `parquet:"creation_time_us"`
	CascadeSize        int32   `parquet:"cascade_size"`
	CascadeDepth       int32   `parquet:"cascade_depth"`
	MaxOutDegree       int32   `parquet:"max_out_degree"`
	StructuralVirality float64 `parquet:"structural_virality"`
}

// BroadcastRow is one row in broadcast_groups.parquet.
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

// PathRow is one row in root_to_leaf.parquet.
type PathRow struct {
	PostURI         string  `parquet:"post_uri"`
	LeafDID         string  `parquet:"leaf_did"`
	PathDepth       int32   `parquet:"path_depth"`
	PathTotalTimeUS float64 `parquet:"path_total_time_us"`
	TraversalSpeed  float64 `parquet:"traversal_speed"`
	GapTrend        float64 `parquet:"gap_trend"`
}

// ─── Writers ─────────────────────────────────────────────────────────────

// cascadeWriter writes CascadeRow batches to a parquet file.
type cascadeWriter struct {
	w *parquet.GenericWriter[CascadeRow]
	f *os.File
}

func newCascadeWriter(path string) (*cascadeWriter, error) {
	f, err := os.Create(path)
	if err != nil {
		return nil, err
	}
	return &cascadeWriter{
		w: parquet.NewGenericWriter[CascadeRow](f),
		f: f,
	}, nil
}

func (w *cascadeWriter) write(rows []CascadeRow) error {
	_, err := w.w.Write(rows)
	return err
}

func (w *cascadeWriter) close() error {
	if err := w.w.Close(); err != nil {
		return err
	}
	return w.f.Close()
}

// broadcastWriter writes BroadcastRow batches.
type broadcastWriter struct {
	w *parquet.GenericWriter[BroadcastRow]
	f *os.File
}

func newBroadcastWriter(path string) (*broadcastWriter, error) {
	f, err := os.Create(path)
	if err != nil {
		return nil, err
	}
	return &broadcastWriter{
		w: parquet.NewGenericWriter[BroadcastRow](f),
		f: f,
	}, nil
}

func (w *broadcastWriter) write(rows []BroadcastRow) error {
	_, err := w.w.Write(rows)
	return err
}

func (w *broadcastWriter) close() error {
	if err := w.w.Close(); err != nil {
		return err
	}
	return w.f.Close()
}

// pathWriter writes PathRow batches.
type pathWriter struct {
	w *parquet.GenericWriter[PathRow]
	f *os.File
}

func newPathWriter(path string) (*pathWriter, error) {
	f, err := os.Create(path)
	if err != nil {
		return nil, err
	}
	return &pathWriter{
		w: parquet.NewGenericWriter[PathRow](f),
		f: f,
	}, nil
}

func (w *pathWriter) write(rows []PathRow) error {
	_, err := w.w.Write(rows)
	return err
}

func (w *pathWriter) close() error {
	if err := w.w.Close(); err != nil {
		return err
	}
	return w.f.Close()
}
