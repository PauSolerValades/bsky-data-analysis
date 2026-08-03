"""HDBSCAN hyperparameter sweep.

Fixed to the viable corner: mcs=2, ms=1 (other combos give 28-52%
singletons — degenerate for sessionization). Sweeps epsilon only.
Calls table_creation/hdbscan_cluster.py for each epsilon.

Usage:
    uv run sessions/hyperparameter/hdbscan_sweep.py
    uv run sessions/hyperparameter/hdbscan_sweep.py --parallel 4
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "table_creation" / "hdbscan_cluster.py"

MIN_CLUSTER_SIZE = [2]
MIN_SAMPLES = [1]
EPSILONS = [0, 60, 180, 300, 600]


def run_one(mcs: int, ms: int, eps: float) -> tuple[str, bool]:
    table_name = f"sessions_hdbscan_mcs{mcs}_ms{ms}_e{eps}"
    cmd = [
        "uv", "run", str(SCRIPT),
        "--table-name", table_name,
        "--min-cluster-size", str(mcs),
        "--min-samples", str(ms),
        "--epsilon", str(eps),
        "--summary",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n  FAILED {table_name}: {result.stderr[-200:]}", file=sys.stderr)
    return table_name, result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="HDBSCAN sweep.")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Max concurrent subprocesses")
    args = parser.parse_args()

    # Build all combinations
    jobs = [(mcs, ms, eps)
            for mcs in MIN_CLUSTER_SIZE
            for ms in MIN_SAMPLES
            for eps in EPSILONS]
    total = len(jobs)
    print(f"  {total} combinations, parallel={args.parallel}", file=sys.stderr)

    if args.parallel == 1:
        # Sequential — preserves ordered output
        failed = []
        for i, (mcs, ms, eps) in enumerate(jobs):
            table_name = f"sessions_hdbscan_mcs{mcs}_ms{ms}_e{eps}"
            print(f"\n  [{i + 1}/{total}] {table_name}", file=sys.stderr)
            name, ok = run_one(mcs, ms, eps)
            if not ok:
                failed.append(name)
    else:
        # Parallel via thread pool
        done = 0
        failed = []
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {pool.submit(run_one, mcs, ms, eps): (mcs, ms, eps)
                       for mcs, ms, eps in jobs}
            for fut in as_completed(futures):
                name, ok = fut.result()
                done += 1
                status = "" if ok else " [FAILED]"
                print(f"  [{done}/{total}] {name}{status}", file=sys.stderr)
                if not ok:
                    failed.append(name)

    if failed:
        print(f"\n  {len(failed)} FAILED: {failed}", file=sys.stderr)

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  HDBSCAN sweep complete — {total} combinations.", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)


if __name__ == "__main__":
    main()
