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

_SECTIONS = {
    "sessions_per_user": 1,
    "session_duration": 2,
    "session_gaps": 3,
    # numeric aliases
    "1": 1, "2": 2, "3": 3,
}


def _parse_sections(raw: str) -> set[int]:
    """Parse comma-separated section names (or numbers) into a set of ints."""
    result: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        n = _SECTIONS.get(token)
        if n is None:
            print(f"Unknown section: '{token}'. Options: {list(_SECTIONS)}", file=sys.stderr)
            sys.exit(1)
        result.add(n)
    return result


def main():
    parser = argparse.ArgumentParser(description="Raw session analysis (core vs all)")
    parser.add_argument(
        "--skip",
        type=str,
        default="",
        help="Comma-separated sections to skip (e.g., 'session_gaps' or '3')",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="Comma-separated sections to run exclusively (e.g., 'sessions_per_user,session_duration' or '1,2')",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="",
        help="Comma-separated sources to run (e.g., 'core' or 'core,all'). Default: both.",
    )
    args = parser.parse_args()

    skip = _parse_sections(args.skip)
    only = _parse_sections(args.only)

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
