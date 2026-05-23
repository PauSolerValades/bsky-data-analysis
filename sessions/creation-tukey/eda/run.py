#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "polars",
#     "matplotlib",
#     "seaborn",
#     "numpy",
#     "scipy",
# ]
# ///
"""
Master EDA orchestrator for session-oriented Bluesky firehose analysis.

Runs all 8 sections in order, passing results downstream.
Each section also produces standalone plots and text summaries in eda/results/.

Sections:
  §1  powerlaw_binning    — Events-per-user + power-law fit & binning
  §2  user_classification — Event-type composition / archetypes
  §3  activity_span       — Activity span, active days, density
  §4  gap_analysis        — Per-user inter-arrival gap distributions
  §5  coverage            — Who contributes the gaps?
  §6  event_type_dist     — Events-per-user by event type separately
  §7  composite_score     — Composite engagement scoring
  §8  recommend           — Filtering strategy recommendations

Usage:
    uv run sessions/creation-tukey/eda/run.py                    # run everything
    uv run sessions/creation-tukey/eda/run.py --force            # re-fetch stats from DB
    uv run sessions/creation-tukey/eda/run.py --skip 4           # skip gap analysis (slow)
    uv run sessions/creation-tukey/eda/run.py --only 1,2,5       # run only specific sections
    uv run sessions/creation-tukey/eda/run.py --gap-sample 30000 # gap analysis sample size
"""

import argparse
import json
import sys
import time as time_mod
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "results"

# Import all section modules
import powerlaw_binning as s1
import user_classification as s2
import activity_span as s3
import gap_analysis as s4
import coverage as s5
import event_type_dist as s6
import composite_score as s7
import recommend as s8


SECTION_NAMES = {
    1: "§1 — Power-law binning",
    2: "§2 — User classification / archetypes",
    3: "§3 — Activity span & density",
    4: "§4 — Per-user gap distribution",
    5: "§5 — Coverage analysis",
    6: "§6 — Event-type distributions",
    7: "§7 — Composite scoring",
    8: "§8 — Recommendations",
}


def main():
    parser = argparse.ArgumentParser(
        description="Session-oriented EDA for Bluesky firehose"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch per-user stats from DB (ignore parquet cache)",
    )
    parser.add_argument(
        "--skip", type=str, default="",
        help="Comma-separated section numbers to skip (e.g., '4,6')",
    )
    parser.add_argument(
        "--only", type=str, default="",
        help="Comma-separated section numbers to run exclusively",
    )
    parser.add_argument(
        "--gap-sample", type=int, default=50_000,
        help="Sample size for §4 gap analysis (default: 50000)",
    )
    parser.add_argument(
        "--json-output", type=str, default=None,
        help="Path to save all section results as JSON",
    )
    args = parser.parse_args()

    skip = {int(x) for x in args.skip.split(",") if x.strip()}
    only = {int(x) for x in args.only.split(",") if x.strip()}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_results: list[dict] = []
    total_t0 = time_mod.time()
    sections_run = 0

    print("=" * 70, file=sys.stderr)
    print("  SESSION-ORIENTED EDA — BLUESKY FIREHOSE", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(file=sys.stderr)

    # -------------------------------------------------------------------
    # §1: Power-law binning
    # -------------------------------------------------------------------
    if _should_run(1, skip, only):
        _banner(1)
        r = s1.run(force_reload=args.force)
        all_results.append(r)
        sections_run += 1

    # -------------------------------------------------------------------
    # §2: User classification
    # -------------------------------------------------------------------
    if _should_run(2, skip, only):
        _banner(2)
        r = s2.run(force_reload=args.force)
        all_results.append(r)
        sections_run += 1

    # -------------------------------------------------------------------
    # §3: Activity span & density
    # -------------------------------------------------------------------
    if _should_run(3, skip, only):
        _banner(3)
        r = s3.run(force_reload=args.force)
        all_results.append(r)
        sections_run += 1

    # -------------------------------------------------------------------
    # §4: Gap analysis (heavy — samples users)
    # -------------------------------------------------------------------
    if _should_run(4, skip, only):
        _banner(4)
        r = s4.run(sample_size=args.gap_sample, force_reload=args.force)
        all_results.append(r)
        sections_run += 1

    # -------------------------------------------------------------------
    # §5: Coverage
    # -------------------------------------------------------------------
    if _should_run(5, skip, only):
        _banner(5)
        r = s5.run(force_reload=args.force)
        all_results.append(r)
        sections_run += 1

    # -------------------------------------------------------------------
    # §6: Event-type distributions
    # -------------------------------------------------------------------
    if _should_run(6, skip, only):
        _banner(6)
        r = s6.run(force_reload=args.force)
        all_results.append(r)
        sections_run += 1

    # -------------------------------------------------------------------
    # §7: Composite scoring
    # -------------------------------------------------------------------
    if _should_run(7, skip, only):
        _banner(7)
        r = s7.run(force_reload=args.force)
        all_results.append(r)
        sections_run += 1

    # -------------------------------------------------------------------
    # §8: Recommendations (always runs — uses accumulated results)
    # -------------------------------------------------------------------
    if _should_run(8, skip, only):
        _banner(8)
        r_text = s8.run(results=all_results)
        all_results.append({"section": SECTION_NAMES[8], "recommendation": r_text})
        sections_run += 1

    # -------------------------------------------------------------------
    # Done
    # -------------------------------------------------------------------
    total_elapsed = time_mod.time() - total_t0
    print(file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"  EDA COMPLETE — {sections_run} sections in {total_elapsed:.0f}s", file=sys.stderr)
    print(f"  Results:  {OUT_DIR}/", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    # Save JSON if requested
    if args.json_output:
        json_path = Path(args.json_output)
        with open(json_path, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        print(f"\n  Results saved to {json_path}", file=sys.stderr)


def _should_run(section: int, skip: set[int], only: set[int]) -> bool:
    if only:
        return section in only
    return section not in skip


def _banner(n: int):
    print(f"  [{SECTION_NAMES[n]}]", file=sys.stderr)
    print(file=sys.stderr)


if __name__ == "__main__":
    main()
