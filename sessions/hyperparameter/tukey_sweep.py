"""Tukey hyperparameter sweep.

Calls table_creation/tukey.py for each k value.

Usage:
    uv run sessions/hyperparameter/tukey_sweep.py
    uv run sessions/hyperparameter/tukey_sweep.py --parallel 4
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "table_creation" / "tukey.py"

K_VALUES = [1.2, 1.5, 1.7]


def run_one(k: float) -> tuple[str, bool]:
    k_str = str(k).replace(".", "_")
    table_name = f"sessions_tukey_k{k_str}"
    cmd = [
        "uv", "run", str(SCRIPT),
        "--table-name", table_name,
        "--k", str(k),
        "--summary",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n  FAILED {table_name}: {result.stderr[-200:]}", file=sys.stderr)
    return table_name, result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Tukey sweep.")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Max concurrent subprocesses")
    args = parser.parse_args()

    print(f"  {len(K_VALUES)} k-values, parallel={args.parallel}", file=sys.stderr)

    if args.parallel == 1:
        failed = []
        for i, k in enumerate(K_VALUES):
            name, ok = run_one(k)
            if not ok:
                failed.append(name)
    else:
        done = 0
        failed = []
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {pool.submit(run_one, k): k for k in K_VALUES}
            for fut in as_completed(futures):
                name, ok = fut.result()
                done += 1
                status = "" if ok else " [FAILED]"
                print(f"  [{done}/{len(K_VALUES)}] {name}{status}", file=sys.stderr)
                if not ok:
                    failed.append(name)

    if failed:
        print(f"\n  {len(failed)} FAILED: {failed}", file=sys.stderr)

    print(f"\n{'=' * 60}", file=sys.stderr)
    print("  Tukey sweep complete.", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)


if __name__ == "__main__":
    main()
