"""Tukey hyperparameter sweep.

Calls table_creation/tukey.py for each k value.

Usage:
    uv run sessions/hyperparameter/tukey_sweep.py
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "table_creation" / "tukey.py"

K_VALUES = [1.2, 1.5, 1.7]


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

    for k in K_VALUES:
        k_str = str(k).replace(".", "_")
        table_name = f"sessions_tukey_k{k_str}"
        run([
            "uv", "run", str(SCRIPT),
            "--table-name", table_name,
            "--k", str(k),
            "--workers", str(workers),
            "--summary",
        ])

    print(f"\n{'=' * 60}", file=sys.stderr)
    print("  Tukey sweep complete.", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)


if __name__ == "__main__":
    main()
