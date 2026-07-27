#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Sweep orchestrator — calls run_tukey.py and run_hdbscan.py with parameter grids.

Usage:
    uv run session-creation/sweep.py
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DID_FILE = HERE / "sample_dids.txt"

# ── Tukey sweep ──
TUKEY_K = [1.2, 1.5, 1.7]

# ── HDBSCAN sweeps ──
# ε sweep (ms=1, mcs=2)
HDBSCAN_EPSILONS = [30, 60, 120, 300]
# min_samples sweep (ε=120, mcs=2)
HDBSCAN_MIN_SAMPLES = [1, 5, 10]


def run(cmd: list[str]):
    print(f"\n{'─' * 60}", file=sys.stderr)
    print(f"  {' '.join(cmd)}", file=sys.stderr)
    print(f"{'─' * 60}", file=sys.stderr)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  FAILED (code {result.returncode})", file=sys.stderr)


def main():
    base = ["uv", "run"]

    # Tukey k-sweep
    for k in TUKEY_K:
        run(base + [
            str(HERE / "run_tukey.py"),
            "--k", str(k),
            "--did-from-file", str(DID_FILE),
            "--summary",
        ])

    # HDBSCAN ε-sweep
    for eps in HDBSCAN_EPSILONS:
        run(base + [
            str(HERE / "run_hdbscan.py"),
            "--epsilon", str(eps),
            "--did-from-file", str(DID_FILE),
            "--summary",
        ])

    # HDBSCAN min_samples sweep
    for ms in HDBSCAN_MIN_SAMPLES:
        run(base + [
            str(HERE / "run_hdbscan.py"),
            "--epsilon", "120",
            "--min-samples", str(ms),
            "--did-from-file", str(DID_FILE),
            "--summary",
        ])

    print(f"\n{'=' * 60}", file=sys.stderr)
    print("  All sweeps complete.", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)


if __name__ == "__main__":
    main()
