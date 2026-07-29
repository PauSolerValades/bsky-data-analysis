"""HDBSCAN hyperparameter sweep.

Full grid: min_cluster_size × min_samples × epsilon.
Calls table_creation/hdbscan.py for each combination.

Usage:
    uv run sessions/hyperparameter/hdbscan_sweep.py
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "table_creation" / "hdbscan.py"

MIN_CLUSTER_SIZE = [2, 3, 5]
MIN_SAMPLES = [1, 2, 3]
EPSILONS = [0, 60, 180, 300]


def run(cmd: list[str]):
    print(f"\n{'─' * 60}", file=sys.stderr)
    print(f"  {' '.join(cmd)}", file=sys.stderr)
    print(f"{'─' * 60}", file=sys.stderr)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  FAILED (code {result.returncode})", file=sys.stderr)


def main():
    workers = os.cpu_count() or 1
    total = len(MIN_CLUSTER_SIZE) * len(MIN_SAMPLES) * len(EPSILONS)
    i = 0
    print(f"  {total} combinations, {workers} workers", file=sys.stderr)

    for mcs in MIN_CLUSTER_SIZE:
        for ms in MIN_SAMPLES:
            for eps in EPSILONS:
                i += 1
                table_name = f"sessions_hdbscan_mcs{mcs}_ms{ms}_e{eps}"
                print(f"\n  [{i}/{total}] {table_name}", file=sys.stderr)

                run([
                    "uv", "run", str(SCRIPT),
                    "--table-name", table_name,
                    "--min-cluster-size", str(mcs),
                    "--min-samples", str(ms),
                    "--epsilon", str(eps),
                    "--workers", str(workers),
                    "--summary",
                ])

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  HDBSCAN sweep complete — {total} combinations.", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)


if __name__ == "__main__":
    main()
