"""Fixed-threshold hyperparameter sweep.

Calls table_creation/fixed_threshold.py for each threshold.

Usage:
    uv run sessions/hyperparameter/fixed_threshold_sweep.py
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "table_creation" / "fixed_threshold.py"

THRESHOLDS = [
    (300, "sessions_fixed_300s"),     # 5 min
    (600, "sessions_fixed_600s"),     # 10 min
    (1200, "sessions_fixed_1200s"),   # 20 min
    (1800, "sessions_fixed_1800s"),   # 30 min
]


def run(cmd: list[str]):
    print(f"\n{'─' * 60}", file=sys.stderr)
    print(f"  {' '.join(cmd)}", file=sys.stderr)
    print(f"{'─' * 60}", file=sys.stderr)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  FAILED (code {result.returncode})", file=sys.stderr)


def main():
    workers = os.cpu_count() or 1
    print(f"  Using {workers} workers", file=sys.stderr)

    for threshold, table_name in THRESHOLDS:
        run([
            "uv", "run", str(SCRIPT),
            "--table-name", table_name,
            "--threshold", str(threshold),
            "--workers", str(workers),
            "--summary",
        ])

    print(f"\n{'=' * 60}", file=sys.stderr)
    print("  Fixed-threshold sweep complete.", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)


if __name__ == "__main__":
    main()
