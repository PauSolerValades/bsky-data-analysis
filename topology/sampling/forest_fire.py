#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pyarrow", "tqdm", "duckdb"]
# ///
"""
Forest Fire graph sampling (Leskovec & Faloutsos, KDD 2006).

For dense social graphs like Bluesky, the full induced subgraph is too dense.
We output TWO edge sets per snapshot:
  1. burned_edges.parquet  — only edges traversed by the fire (the burn path)
  2. induced_edges.parquet — all edges between visited nodes (full induced subgraph)

Nodes are shared: nodes.parquet

Usage:
  uv run topology/sampling/forest_fire.py
  uv run topology/sampling/forest_fire.py --p-f 0.5 --p-b 0.2
  uv run topology/sampling/forest_fire.py --seed 12345
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

PROJECT = Path(__file__).resolve().parent.parent.parent
PARQUET_DIR = PROJECT / "topology/firehose/process/data/topology"
RESULTS_DIR = PROJECT / "topology/sampling/results"
TARGET_SIZES = [10_000, 50_000, 100_000, 500_000, 1_000_000]


# ── CSR builder ─────────────────────────────────────────────────────────────

class DirectedCSR:
    """In-memory adjacency for the Bluesky follow graph."""

    def __init__(self, parquet_path: str):
        t0 = time.time()
        self.db = duckdb.connect()
        self.db.execute("SET threads = 32")
        self.db.execute("SET memory_limit = '800GB'")

        print(f"Loading active edges ...")
        self.db.execute(f"""
            CREATE TEMP TABLE active_edges AS
            SELECT CAST(actor_did AS VARCHAR) AS a, CAST(subject_did AS VARCHAR) AS s
            FROM read_parquet('{parquet_path}') WHERE valid_to IS NULL
        """)
        n_edges = self.db.execute("SELECT COUNT(*) FROM active_edges").fetchone()[0]
        print(f"  {n_edges:,} active edges ({time.time()-t0:.1f}s)")

        print("Building DID mapping ...")
        t1 = time.time()
        self.db.execute("""
            CREATE TEMP TABLE dids AS
            SELECT DISTINCT did FROM (
                SELECT a AS did FROM active_edges UNION
                SELECT s AS did FROM active_edges
            ) ORDER BY did
        """)
        self.dids = self.db.execute("SELECT did FROM dids ORDER BY did").fetchnumpy()["did"]
        self.dids = list(self.dids)
        self.num_nodes = len(self.dids)
        print(f"  {self.num_nodes:,} unique DIDs ({time.time()-t1:.1f}s)")

        self.did_to_id = {did: i for i, did in enumerate(self.dids)}

        # Out adjacency
        print("Building out-adjacency ...")
        t_adj = time.time()
        out_rows = self.db.execute("""
            SELECT a.did AS actor, LIST(s.did) AS following
            FROM active_edges e JOIN dids a ON e.a=a.did JOIN dids s ON e.s=s.did
            GROUP BY a.did
        """).fetchall()
        self.out_adj = [np.array([], dtype=np.int32) for _ in range(self.num_nodes)]
        for actor_did, following in tqdm(out_rows, desc="  out_adj", unit=" nodes"):
            aid = self.did_to_id[actor_did]
            self.out_adj[aid] = np.array([self.did_to_id[d] for d in following], dtype=np.int32)
        total_out = sum(len(a) for a in self.out_adj)
        print(f"  {total_out:,} out-edges ({time.time()-t_adj:.1f}s)")

        # In adjacency
        print("Building in-adjacency ...")
        t_in = time.time()
        in_rows = self.db.execute("""
            SELECT s.did AS subject, LIST(a.did) AS followers
            FROM active_edges e JOIN dids a ON e.a=a.did JOIN dids s ON e.s=s.did
            GROUP BY s.did
        """).fetchall()
        self.in_adj = [np.array([], dtype=np.int32) for _ in range(self.num_nodes)]
        for subject_did, followers in tqdm(in_rows, desc="  in_adj ", unit=" nodes"):
            sid = self.did_to_id[subject_did]
            self.in_adj[sid] = np.array([self.did_to_id[d] for d in followers], dtype=np.int32)
        total_in = sum(len(a) for a in self.in_adj)
        print(f"  {total_in:,} in-edges ({time.time()-t_in:.1f}s)")

        self.db.execute("DROP TABLE IF EXISTS active_edges")
        self.db.execute("DROP TABLE IF EXISTS dids")
        self.db.close()
        print(f"  CSR ready ({time.time()-t0:.1f}s total)\n")


# ── Forest Fire ─────────────────────────────────────────────────────────────

class ForestFire:
    def __init__(self, csr: DirectedCSR, p_f: float = 0.5, p_b: float = 0.2,
                 seed: int | None = None):
        self.csr = csr
        self.p_f = p_f
        self.p_b = p_b
        self.rng = np.random.default_rng(seed)
        self.total_nodes = csr.num_nodes
        self.visited: set[int] = set()
        self.queue: deque[int] = deque()

    def _geometric(self, p: float, cap: int) -> int:
        if p <= 0 or cap <= 0:
            return 0
        if p >= 1.0:
            return cap
        u = self.rng.uniform()
        return min(int(np.ceil(np.log(1 - u) / np.log(1 - p))), cap)

    def _burn(self, node: int, neighbors: np.ndarray, p: float) -> list[int]:
        unvisited = np.array([n for n in neighbors if n not in self.visited], dtype=np.int32)
        if len(unvisited) == 0:
            return []
        k = self._geometric(p, len(unvisited))
        if k == 0:
            return []
        if k >= len(unvisited):
            return unvisited.tolist()
        return unvisited[self.rng.choice(len(unvisited), size=k, replace=False)].tolist()

    def _random_unvisited(self) -> int:
        while True:
            c = int(self.rng.integers(0, self.total_nodes))
            if c not in self.visited:
                return c

    def run(self, target_sizes: list[int]) -> dict[int, dict]:
        snapshots: dict[int, dict] = {}
        next_i = 0

        seed = int(self.rng.integers(0, self.total_nodes))
        self.visited.add(seed)
        self.queue.append(seed)

        # Track only edges actually traversed by the fire
        burned_edges: list[tuple[int, int]] = []

        pbar = tqdm(total=max(target_sizes), desc="  fire spread", unit=" nodes")
        pbar.update(1)

        while next_i < len(target_sizes):
            target = target_sizes[next_i]

            while len(self.visited) < target:
                if self.queue:
                    v = self.queue.popleft()

                    # Forward burn
                    out_n = self.csr.out_adj[v]
                    for w in self._burn(v, out_n, self.p_f):
                        self.visited.add(w)
                        burned_edges.append((v, w))
                        self.queue.append(w)
                        pbar.update(1)

                    # Backward burn
                    if self.p_b > 0:
                        in_n = self.csr.in_adj[v]
                        for w in self._burn(v, in_n, self.p_b):
                            self.visited.add(w)
                            burned_edges.append((w, v))
                            self.queue.append(w)
                            pbar.update(1)
                else:
                    new_seed = self._random_unvisited()
                    self.visited.add(new_seed)
                    self.queue.append(new_seed)
                    pbar.update(1)

                if len(self.visited) >= target:
                    break

            actual = len(self.visited)
            print(f"\n  Snapshot {target:,} ({actual:,} nodes, {len(burned_edges):,} burned edges)")

            # Compute induced subgraph (all edges between visited nodes)
            t_ind = time.time()
            visited_set = self.visited
            induced: list[tuple[int, int]] = []
            for v in tqdm(sorted(visited_set), desc="    induced edges", unit=" nodes"):
                for w in self.csr.out_adj[v]:
                    if w in visited_set:
                        induced.append((v, int(w)))
            print(f"    {len(induced):,} induced edges ({time.time()-t_ind:.1f}s)")

            snapshots[target] = {
                "visited": set(self.visited),
                "burned_edges": list(burned_edges),
                "induced_edges": induced,
            }
            next_i += 1

        pbar.close()
        return snapshots


# ── Output ──────────────────────────────────────────────────────────────────

def write_snapshot(csr, target, snap, out_dir, params, elapsed):
    out_dir.mkdir(parents=True, exist_ok=True)
    visited = snap["visited"]
    sorted_v = sorted(visited)

    # Nodes
    dids = [csr.dids[i] for i in sorted_v]
    pq.write_table(
        pa.table({"did": pa.array(dids, type=pa.string()),
                  "int_id": pa.array(sorted_v, type=pa.int32())}),
        out_dir / "nodes.parquet",
    )

    # Burned edges
    be = snap["burned_edges"]
    pq.write_table(
        pa.table({
            "actor_did": pa.array([csr.dids[a] for a, s in be], type=pa.string()),
            "subject_did": pa.array([csr.dids[s] for a, s in be], type=pa.string()),
            "actor_id": pa.array([a for a, s in be], type=pa.int32()),
            "subject_id": pa.array([s for a, s in be], type=pa.int32()),
        }),
        out_dir / "burned_edges.parquet",
    )

    # Induced edges
    ie = snap["induced_edges"]
    pq.write_table(
        pa.table({
            "actor_did": pa.array([csr.dids[a] for a, s in ie], type=pa.string()),
            "subject_did": pa.array([csr.dids[s] for a, s in ie], type=pa.string()),
            "actor_id": pa.array([a for a, s in ie], type=pa.int32()),
            "subject_id": pa.array([s for a, s in ie], type=pa.int32()),
        }),
        out_dir / "induced_edges.parquet",
    )

    meta = {
        "algorithm": "ForestFire",
        "target_size": target,
        "actual_nodes": len(visited),
        "burned_edges": len(be),
        "induced_edges": len(ie),
        "p_f": params["p_f"],
        "p_b": params["p_b"],
        "seed": params["seed"],
        "elapsed_seconds": round(elapsed, 1),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(out_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    print(f"  -> wrote {len(visited):,} nodes, {len(be):,} burned, {len(ie):,} induced")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Forest Fire sampling for Bluesky")
    parser.add_argument("--p-f", type=float, default=0.5)
    parser.add_argument("--p-b", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--parquet", type=str, default=str(PARQUET_DIR / "follow_edges.parquet"))
    parser.add_argument("--sizes", type=int, nargs="+", default=TARGET_SIZES)
    parser.add_argument("--out", type=str, default=str(RESULTS_DIR))
    args = parser.parse_args()

    print("=" * 60)
    print(f"Forest Fire: p_f={args.p_f}, p_b={args.p_b}, seed={args.seed}")
    print(f"Targets: {args.sizes}")
    print("=" * 60)

    t0 = time.time()
    if not Path(args.parquet).exists():
        print(f"ERROR: {args.parquet} not found"); return 1

    csr = DirectedCSR(args.parquet)
    t_csr = time.time()

    ff = ForestFire(csr, p_f=args.p_f, p_b=args.p_b, seed=args.seed)
    snapshots = ff.run(args.sizes)
    ff_time = time.time() - t_csr

    out_dir = Path(args.out)
    params = {"p_f": args.p_f, "p_b": args.p_b, "seed": args.seed}

    for target in args.sizes:
        if target in snapshots:
            write_snapshot(csr, target, snapshots[target],
                           out_dir / str(target), params, ff_time)

    print(f"\n{'=' * 60}")
    print(f"Done in {(time.time()-t0)/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
