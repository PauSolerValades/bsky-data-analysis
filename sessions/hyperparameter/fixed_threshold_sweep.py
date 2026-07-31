"""Fixed-threshold hyperparameter sweep.

Calls table_creation/fixed_threshold.py for each threshold.

Usage:
    uv run sessions/hyperparameter/fixed_threshold_sweep.py
    uv run sessions/hyperparameter/fixed_threshold_sweep.py --parallel 4
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "table_creation" / "fixed_threshold.py"

THRESHOLDS = [
    (300, "sessions_fixed_300s"),     # 5 min
    (600, "sessions_fixed_600s"),     # 10 min
    (1200, "sessions_fixed_1200s"),   # 20 min
    (1800, "sessions_fixed_1800s"),   # 30 min
]


def run_one(threshold: int, table_name: str) -> tuple[str, bool]:
    cmd = [
        "uv", "run", str(SCRIPT),
        "--table-name", table_name,
        "--threshold", str(threshold),
        "--summary",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n  FAILED {table_name}: {result.stderr[-200:]}", file=sys.stderr)
    return table_name, result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Fixed-threshold sweep.")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Max concurrent subprocesses")
    args = parser.parse_args()

    print(f"  {len(THRESHOLDS)} thresholds, parallel={args.parallel}", file=sys.stderr)

    if args.parallel == 1:
        failed = []
        for i, (threshold, table_name) in enumerate(THRESHOLDS):
            print(f"\n  [{i + 1}/{len(THRESHOLDS)}] {table_name}", file=sys.stderr)
            name, ok = run_one(threshold, table_name)
            if not ok:
                failed.append(name)
    else:
        done = 0
        failed = []
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {pool.submit(run_one, t, n): n for t, n in THRESHOLDS}
            for fut in as_completed(futures):
                name, ok = fut.result()
                done += 1
                status = "" if ok else " [FAILED]"
                print(f"  [{done}/{len(THRESHOLDS)}] {name}{status}", file=sys.stderr)
                if not ok:
                    failed.append(name)

    if failed:
        print(f"\n  {len(failed)} FAILED: {failed}", file=sys.stderr)

    print(f"\n{'=' * 60}", file=sys.stderr)
    print("  Fixed-threshold sweep complete.", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)


if __name__ == "__main__":
    main()
