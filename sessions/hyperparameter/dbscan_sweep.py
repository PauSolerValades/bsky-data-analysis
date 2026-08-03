"""DBSCAN hyperparameter sweep.

min_samples fixed at 2 (ms=1 is provably equivalent to fixed-threshold).
Calls table_creation/dbscan_cluster.py for each epsilon.

Usage:
    uv run sessions/hyperparameter/dbscan_sweep.py
    uv run sessions/hyperparameter/dbscan_sweep.py --parallel 4
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "table_creation" / "dbscan_cluster.py"

MIN_SAMPLES = 2
# Burst scale (5-60s) + session scale (300-600s, comparable to fixed/HDBSCAN)
EPSILONS = [5, 15, 30, 60, 300, 600]


def run_one(eps: float) -> tuple[str, bool]:
    table_name = f"sessions_dbscan_e{eps}_ms{MIN_SAMPLES}"
    cmd = [
        "uv", "run", str(SCRIPT),
        "--table-name", table_name,
        "--epsilon", str(eps),
        "--min-samples", str(MIN_SAMPLES),
        "--summary",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n  FAILED {table_name}: {result.stderr[-200:]}", file=sys.stderr)
    return table_name, result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="DBSCAN sweep.")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Max concurrent subprocesses")
    args = parser.parse_args()

    total = len(EPSILONS)
    print(f"  {total} epsilons, parallel={args.parallel}", file=sys.stderr)

    failed = []
    if args.parallel == 1:
        for i, eps in enumerate(EPSILONS):
            print(f"\n  [{i + 1}/{total}] eps={eps}", file=sys.stderr)
            name, ok = run_one(eps)
            if not ok:
                failed.append(name)
    else:
        done = 0
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {pool.submit(run_one, eps): eps for eps in EPSILONS}
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
    print(f"  DBSCAN sweep complete — {total} epsilons.", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)


if __name__ == "__main__":
    main()
