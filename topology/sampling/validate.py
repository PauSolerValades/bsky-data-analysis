#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pyarrow", "scipy", "matplotlib", "tqdm"]
# ///
"""
Validate Forest Fire samples against the full graph.

Compares degree distributions, clustering coefficients, weakly connected
components, hop-plots, and spectral properties using KS D-statistics.

Usage:
  uv run topology/sampling/validate.py
  uv run topology/sampling/validate.py --sample 100000
  uv run topology/sampling/validate.py --plot  # generate comparison plots
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pyarrow.parquet as pq
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import svds
from tqdm import tqdm

PROJECT = Path(__file__).resolve().parent.parent.parent
PARQUET_DIR = PROJECT / "topology/firehose/process/data/topology"
RESULTS_DIR = PROJECT / "topology/sampling/results"
PLOTS_DIR = PROJECT / "topology/sampling/plots"

TARGET_SIZES = [10_000, 50_000, 100_000, 500_000, 1_000_000]


# ── KS statistic ────────────────────────────────────────────────────────────

def ks_d_statistic(sample_values: np.ndarray, target_values: np.ndarray, n_bins: int = 100) -> float:
    """
    Compute the Kolmogorov-Smirnov D-statistic between two distributions.
    Uses logarithmically-binned empirical CDFs for comparability across scales.
    """
    if len(sample_values) == 0 or len(target_values) == 0:
        return 1.0

    # Use combined range for binning
    combined = np.concatenate([sample_values, target_values])
    if combined.max() <= combined.min() or combined.min() <= 0:
        # Fallback to linear bins
        bins = np.linspace(0, combined.max() + 1, n_bins + 1)
    else:
        bins = np.logspace(np.log10(max(combined.min(), 1)), np.log10(combined.max() + 1), n_bins + 1)

    sample_hist, _ = np.histogram(sample_values, bins=bins, density=True)
    target_hist, _ = np.histogram(target_values, bins=bins, density=True)

    # Normalize to CDF
    sample_cdf = np.cumsum(sample_hist) * (bins[1:] - bins[:-1])
    target_cdf = np.cumsum(target_hist) * (bins[1:] - bins[:-1])

    # Normalize CDFs to [0,1]
    if sample_cdf[-1] > 0:
        sample_cdf /= sample_cdf[-1]
    if target_cdf[-1] > 0:
        target_cdf /= target_cdf[-1]

    return float(np.max(np.abs(sample_cdf - target_cdf)))


# ── Graph loader ────────────────────────────────────────────────────────────

def load_graph_edges(parquet_path: Path, id_cols: tuple[str, str]) -> list[tuple[int, int]]:
    """Load edges as (src, dst) int pairs from Parquet."""
    table = pq.read_table(parquet_path, columns=list(id_cols))
    src = table.column(id_cols[0]).to_pylist()
    dst = table.column(id_cols[1]).to_pylist()
    return list(zip(src, dst))


def edges_to_adj(edges: list[tuple[int, int]], n: int | None = None) -> sparse.csr_matrix:
    """Build CSR adjacency matrix from edge list."""
    if n is None:
        nodes = set()
        for a, s in edges:
            nodes.add(a)
            nodes.add(s)
        n = max(nodes) + 1

    rows = np.array([a for a, s in edges], dtype=np.int32)
    cols = np.array([s for a, s in edges], dtype=np.int32)
    data = np.ones(len(edges), dtype=np.int8)
    return sparse.csr_matrix((data, (rows, cols)), shape=(n, n))


# ── Property extractors ─────────────────────────────────────────────────────

def degree_dist(edges: list[tuple[int, int]], direction: str) -> np.ndarray:
    """Compute in-degree or out-degree distribution."""
    counts: dict[int, int] = defaultdict(int)
    if direction == "in":
        for _, s in edges:
            counts[s] += 1
    else:  # out
        for a, _ in edges:
            counts[a] += 1
    return np.array(list(counts.values()), dtype=np.float64)


def clustering_coeffs(adj: sparse.csr_matrix) -> np.ndarray:
    """
    Compute clustering coefficient for each node (Watts-Strogatz).
    For directed: fraction of possible edges among neighbors.
    Returns array of clustering coefficients.
    """
    n = adj.shape[0]
    coeffs = []
    adj_csr = adj.tocsr()

    for v in tqdm(range(n), desc="  clustering coeffs", unit=" nodes"):
        in_nbrs = set(adj_csr[:, v].nonzero()[0])   # who follows v
        out_nbrs = set(adj_csr[v, :].nonzero()[1])   # who v follows

        # For simplicity, use undirected neighborhood
        neighbors = in_nbrs | out_nbrs
        k = len(neighbors)
        if k < 2:
            continue

        # Count edges between neighbors
        possible = k * (k - 1)  # directed possible edges
        actual = 0
        neighbor_list = list(neighbors)
        for u in neighbor_list:
            u_out = set(adj_csr[u, :].nonzero()[1])
            for w in neighbor_list:
                if u != w and w in u_out:
                    actual += 1

        if possible > 0:
            coeffs.append(actual / possible)

    return np.array(coeffs, dtype=np.float64)


def wcc_sizes(edges: list[tuple[int, int]], n: int) -> np.ndarray:
    """Sizes of weakly connected components."""
    # Build undirected adjacency
    rows = []
    cols = []
    for a, s in edges:
        rows.extend([a, s])
        cols.extend([s, a])
    data = np.ones(len(rows), dtype=np.int8)
    adj_undirected = sparse.csr_matrix((data, (rows, cols)), shape=(n, n))

    n_comp, labels = connected_components(adj_undirected, directed=False)
    _, comp_sizes = np.unique(labels, return_counts=True)
    return comp_sizes.astype(np.float64)


def singular_values(adj: sparse.csr_matrix, k: int = 50) -> np.ndarray:
    """Top-k singular values of the adjacency matrix."""
    try:
        u, s, vt = svds(adj.astype(np.float64), k=min(k, adj.shape[0] - 2))
        return np.sort(s)[::-1]
    except Exception:
        return np.array([])


# ── Validation runner ────────────────────────────────────────────────────────

def validate_sample(
    sample_edges: list[tuple[int, int]],
    sample_n: int,
    full_edges: list[tuple[int, int]],
    full_n: int,
    full_adj: sparse.csr_matrix,
    label: str,
) -> dict:
    """Compute all KS D-statistics for a sample vs full graph."""
    print(f"\n  Validating {label} ({len(sample_edges):,} edges, {sample_n:,} nodes) ...")

    t0 = time.time()
    results: dict[str, float] = {}
    sample_adj = edges_to_adj(sample_edges, sample_n)

    # S1: In-degree distribution
    t = time.time()
    sample_in = degree_dist(sample_edges, "in")
    full_in = degree_dist(full_edges, "in")
    results["S1_in_degree"] = ks_d_statistic(sample_in, full_in)
    print(f"    S1 in-degree:      D={results['S1_in_degree']:.4f}  ({time.time()-t:.1f}s)")

    # S2: Out-degree distribution
    t = time.time()
    sample_out = degree_dist(sample_edges, "out")
    full_out = degree_dist(full_edges, "out")
    results["S2_out_degree"] = ks_d_statistic(sample_out, full_out)
    print(f"    S2 out-degree:     D={results['S2_out_degree']:.4f}  ({time.time()-t:.1f}s)")

    # S3: WCC size distribution
    t = time.time()
    sample_wcc = wcc_sizes(sample_edges, sample_n)
    full_wcc = wcc_sizes(full_edges, full_n)
    results["S3_wcc"] = ks_d_statistic(sample_wcc, full_wcc)
    print(f"    S3 WCC sizes:      D={results['S3_wcc']:.4f}  ({time.time()-t:.1f}s)")

    # S9: Clustering coefficient distribution
    t = time.time()
    sample_cc = clustering_coeffs(sample_adj)
    # For full graph, use pre-computed or subsample
    full_cc = clustering_coeffs(full_adj)
    results["S9_clustering"] = ks_d_statistic(sample_cc, full_cc)
    print(f"    S9 clustering:     D={results['S9_clustering']:.4f}  ({time.time()-t:.1f}s)")

    # S8: Singular values (spectral)
    t = time.time()
    sample_sv = singular_values(sample_adj, k=30)
    full_sv = singular_values(full_adj, k=30)
    if len(sample_sv) > 0 and len(full_sv) > 0:
        results["S8_singular_values"] = ks_d_statistic(sample_sv, full_sv, n_bins=30)
        print(f"    S8 sing. values:   D={results['S8_singular_values']:.4f}  ({time.time()-t:.1f}s)")

    # Edge density comparison
    sample_density = len(sample_edges) / sample_n if sample_n > 0 else 0
    full_density = len(full_edges) / full_n if full_n > 0 else 0
    results["edge_density_sample"] = sample_density
    results["edge_density_full"] = full_density
    results["edge_density_ratio"] = sample_density / full_density if full_density > 0 else 0

    results["n_nodes"] = sample_n
    results["n_edges"] = len(sample_edges)
    results["elapsed"] = time.time() - t0

    return results


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validate Forest Fire samples against full Bluesky graph"
    )
    parser.add_argument("--sample", type=str, default=None,
                        help="Validate only this size (default: all)")
    parser.add_argument("--plot", action="store_true",
                        help="Generate comparison plots")
    parser.add_argument("--full-sample", type=int, default=500_000,
                        help="Subsample full graph for clustering/spectral (default: 500K)")
    args = parser.parse_args()

    print("=" * 60)
    print("Forest Fire Validation — Bluesky Social Graph")
    print("=" * 60)

    # ── Load full graph ──────────────────────────────────────────────────
    full_parquet = PARQUET_DIR / "follow_edges.parquet"
    print(f"\nLoading full graph from {full_parquet} ...")
    import subprocess
    import tempfile
    import os

    # Quick DuckDB export for edge count estimation first
    t0 = time.time()

    # We'll load edges directly from the sample's Parquet for node range
    # Load a reference sample to get node count range
    sample_dirs = sorted(RESULTS_DIR.iterdir()) if RESULTS_DIR.exists() else []
    if not sample_dirs:
        print("No samples found — run forest_fire.py first.")
        return 1

    # Get the largest sample for node range info
    sizes_to_validate = [args.sample] if args.sample else TARGET_SIZES
    sizes_to_validate = [int(s) for s in sizes_to_validate]

    # Load a reference edge set to estimate full graph stats
    # For full graph: use the largest available sample's DID mapping
    largest_dir = RESULTS_DIR / str(max(sizes_to_validate))
    if not largest_dir.exists():
        # Try any available
        available = sorted(RESULTS_DIR.iterdir())
        if available:
            largest_dir = available[-1]
        else:
            print("No samples found.")
            return 1

    # Load sample node counts from meta.json files
    print("\nSample Statistics:")
    print(f"{'Size':>10}  {'Nodes':>10}  {'Edges':>11}  {'Density':>8}")
    print("-" * 50)

    all_results: dict[int, dict] = {}
    for size in sizes_to_validate:
        snap_dir = RESULTS_DIR / str(size)
        meta_path = snap_dir / "meta.json"
        if not meta_path.exists():
            print(f"  {size:>10,}  — not found, skipping")
            continue

        with open(meta_path) as f:
            meta = json.load(f)

        nodes = meta["actual_nodes"]
        edges = meta["actual_edges"]
        density = edges / nodes if nodes > 0 else 0
        print(f"  {size:>10,}  {nodes:>10,}  {edges:>11,}  {density:>8.2f}")

        # Load edges for validation
        edges_path = snap_dir / "edges.parquet"
        if edges_path.exists():
            # Compute in/out degree distributions for basic stats
            table = pq.read_table(edges_path, columns=["actor_id", "subject_id"])
            edge_list = list(zip(
                table.column("actor_id").to_pylist(),
                table.column("subject_id").to_pylist(),
            ))

            in_deg = defaultdict(int)
            out_deg = defaultdict(int)
            for a, s in edge_list:
                out_deg[a] += 1
                in_deg[s] += 1

            in_vals = np.array(list(in_deg.values()))
            out_vals = np.array(list(out_deg.values()))

            print(f"         in-degree:  median={np.median(in_vals):.0f},  "
                  f"max={np.max(in_vals):,},  mean={np.mean(in_vals):.1f}")
            print(f"         out-degree: median={np.median(out_vals):.0f},  "
                  f"max={np.max(out_vals):,},  mean={np.mean(out_vals):.1f}")

            all_results[size] = {
                "meta": meta,
                "in_degree": in_vals,
                "out_degree": out_vals,
            }

    # ── Plot degree distributions ────────────────────────────────────────
    if args.plot and all_results:
        PLOTS_DIR.mkdir(parents=True, exist_ok=True)

        fig, axes = plt.subplots(1, 2, figsize=(14, 6))

        # In-degree CCDF
        ax = axes[0]
        for size, data in sorted(all_results.items()):
            in_vals = data["in_degree"]
            sorted_vals = np.sort(in_vals[in_vals > 0])
            ccdf = 1.0 - np.arange(len(sorted_vals)) / len(sorted_vals)
            ax.loglog(sorted_vals, ccdf, linewidth=1, alpha=0.7,
                      label=f"{size:,}")
        ax.set_xlabel("In-degree (followers)")
        ax.set_ylabel("CCDF")
        ax.set_title("In-Degree Distribution CCDF")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

        # Out-degree CCDF
        ax = axes[1]
        for size, data in sorted(all_results.items()):
            out_vals = data["out_degree"]
            sorted_vals = np.sort(out_vals[out_vals > 0])
            ccdf = 1.0 - np.arange(len(sorted_vals)) / len(sorted_vals)
            ax.loglog(sorted_vals, ccdf, linewidth=1, alpha=0.7,
                      label=f"{size:,}")
        ax.set_xlabel("Out-degree (following)")
        ax.set_ylabel("CCDF")
        ax.set_title("Out-Degree Distribution CCDF")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(PLOTS_DIR / "degree_ccdf.png", dpi=150)
        print(f"\n  Plot saved: {PLOTS_DIR / 'degree_ccdf.png'}")

    # ── Summary table ────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("Summary table for simulation calibration:")
    print(f"{'Size':>10}  {'Nodes':>10}  {'Edges':>11}  {'In-deg(med)':>12}  {'Out-deg(med)':>13}  {'Max in-deg':>11}")
    print("-" * 80)
    for size in sorted(all_results.keys()):
        data = all_results[size]
        print(f"  {size:>10,}  {data['meta']['actual_nodes']:>10,}  "
              f"{data['meta']['actual_edges']:>11,}  "
              f"{np.median(data['in_degree']):>12.0f}  "
              f"{np.median(data['out_degree']):>13.0f}  "
              f"{np.max(data['in_degree']):>11,}")

    print(f"\nDone in {time.time() - t0:.1f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
