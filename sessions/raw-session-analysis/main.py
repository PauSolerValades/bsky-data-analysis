"""
Raw session analysis orchestrator.

Runs all 3 sections for each source.  Produces individual plot files
(no overlays, no multi-panel figures) in raw-session-analysis/results/.

Usage:
    uv run raw-session-analysis/main.py
    uv run raw-session-analysis/main.py --skip 3
    uv run raw-session-analysis/main.py --only 1,2
"""

import argparse
import sys
import time as time_mod

from _common import Source

import sessions_per_user as s1
import session_duration as s2
import session_gaps as s3


def main():
    parser = argparse.ArgumentParser(
        description="Raw session analysis (core vs all)"
    )
    parser.add_argument(
        "--skip", type=str, default="",
        help="Comma-separated section numbers to skip (e.g., '3')",
    )
    parser.add_argument(
        "--only", type=str, default="",
        help="Comma-separated section numbers to run exclusively (e.g., '1,2')",
    )
    parser.add_argument(
        "--source", type=str, default="",
        help="Comma-separated sources to run (e.g., 'core' or 'core,all'). Default: both.",
    )
    args = parser.parse_args()

    skip = {int(x) for x in args.skip.split(",") if x.strip()}
    only = {int(x) for x in args.only.split(",") if x.strip()}

    if args.source:
        sources = [Source(s) for s in args.source.split(",")]
    else:
        sources = list(Source)

    print("=" * 70, file=sys.stderr)
    print("  RAW SESSION ANALYSIS", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    total_t0 = time_mod.time()
    sections_run = 0

    for source in sources:
        print(f"\n  >>> Source: {source.label} <<<", file=sys.stderr)

        if _should_run(1, skip, only):
            _banner(1)
            s1.run(source)
            sections_run += 1

        if _should_run(2, skip, only):
            _banner(2)
            s2.run(source)
            sections_run += 1

        if _should_run(3, skip, only):
            _banner(3)
            s3.run(source)
            sections_run += 1

    elapsed = time_mod.time() - total_t0
    print(f"\n{'=' * 70}", file=sys.stderr)
    print(f"  DONE — {sections_run} sections in {elapsed:.0f}s", file=sys.stderr)
    print(f"{'=' * 70}", file=sys.stderr)


def _should_run(section: int, skip: set[int], only: set[int]) -> bool:
    if only:
        return section in only
    return section not in skip


def _banner(n: int):
    print(f"\n  [§{n}]", file=sys.stderr)


if __name__ == "__main__":
    main()
