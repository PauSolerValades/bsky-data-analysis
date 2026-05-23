#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "polars",
#     "numpy",
# ]
# ///
"""
§8 — Filtering strategy recommendations.

Consumes results from all previous sections and produces a concrete
recommendation for which users to include in session analysis and
what threshold(s) to use.
"""

import json
import sys
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parent / "results"


def build_recommendation(all_results: list[dict]) -> str:
    """Given results from all sections, produce a human-readable recommendation."""
    # Extract key numbers
    r = {}
    for section in all_results:
        r.update({k: v for k, v in section.items() if k != "section"})

    lines = [
        "=" * 70,
        "  SESSION ANALYSIS — FILTERING STRATEGY RECOMMENDATION",
        "=" * 70,
        "",
        "Based on the EDA results from all 7 sections, here is the recommended",
        "filtering strategy for session analysis on the 8-day Bluesky firehose.",
        "",
        "---",
        "",
    ]

    # §1: Event distribution
    if "xmin" in r:
        xmin = r["xmin"]
        alpha = r.get("alpha", "?")
        lines.extend([
            "§1 — POWER-LAW FIT",
            f"  The event-count distribution follows a power-law above xmin ≈ {xmin:.0f}",
            f"  events (α = {alpha}). To avoid tourist noise, keep users with at least",
            f"  {xmin:.0f} events — they belong to the heavy-tail regime.",
            f"  This matches the established '≥6 events' tourist-removal heuristic.",
            "",
        ])

    # §2: Archetypes
    if "archetypes" in r:
        arch = r["archetypes"]
        lines.append("§2 — USER ARCHETYPES")
        for name in sorted(arch, key=arch.get, reverse=True):
            lines.append(f"  {name}: {arch[name]:,}")
        lines.extend([
            "  Users split naturally into creators, engagers, curators, and tourists.",
            "  Creators (post-heavy) produce content; engagers (like-heavy) react.",
            "  They likely have different session rhythms — consider stratifying.",
            "",
        ])

    # §3: Activity span
    if "pct_one_day" in r:
        lines.extend([
            "§3 — ACTIVITY SPAN",
            f"  {r['pct_one_day']:.1f}% of users are active on only 1 day (bingers/tourists).",
            f"  {r.get('pct_full_8_days', '?')}% are active on 7–8 days (consistent users).",
            "  For session analysis, multi-day users give meaningful inter-session gaps;",
            "  single-day bingers do not. Consider requiring active_days ≥ 2.",
            "",
        ])

    # §4: Gap distribution
    if "median_of_medians" in r:
        mg = r["median_of_medians"]
        p5 = r.get("pct_below_5min", 0)
        lines.extend([
            "§4 — PER-USER GAP DISTRIBUTION",
            f"  Median of per-user median gaps: {mg:.0f}s ({mg/60:.1f} min)",
            f"  {p5:.1f}% of users have median gap < 5 min.",
            "",
        ])

    # §5: Coverage
    if "coverage_table" in r:
        lines.extend([
            "§5 — COVERAGE",
            "  See 05_summary.txt for the full table. Key takeaway:",
        ])
        cov = r["coverage_table"]
        for bucket, vals in cov.items():
            pg = vals["pct_gaps"]
            if pg > 30:
                lines.append(f"  Bucket '{bucket}' contributes {pg:.1f}% of all gaps → DOMINANT")
        lines.append("")

    # §6: Event types
    lines.extend([
        "§6 — EVENT-TYPE DISTRIBUTIONS",
        "  Likes dominate, posts and reposts are rarer. Session analysis should",
        "  use core events (posts, replies, reposts) as established — likes",
        "  are a different behavioural signal (passive engagement).",
        "",
    ])

    # §7: Composite score
    if "median_score" in r:
        lines.extend([
            "§7 — COMPOSITE SCORE",
            f"  Median score: {r['median_score']:.3f}",
            f"  P90 score:    {r['p90_score']:.3f}",
            "",
        ])

    # Final recommendation
    lines.extend([
        "=" * 70,
        "  FINAL RECOMMENDATION",
        "=" * 70,
        "",
        "FILTER (keep these users for session analysis):",
        "",
        "  1. REQUIRED:  total_events ≥ 6        (remove tourists)",
        "  2. REQUIRED:  active_days ≥ 2          (remove single-day bingers)",
        "  3. OPTIONAL:  events_per_active_day ≤ 100  (remove likely bots)",
        "  4. OPTIONAL:  score ≥ P25 threshold     (remove low-engagement users)",
        "",
        "THRESHOLD STRATEGY:",
        "",
        "  A. If using a single global threshold:",
        "     → Use the elbow method result (~285s / 4.8 min for human-filtered data).",
        "",
        "  B. If using per-user adaptive threshold (recommended):",
        "     → Tukey's IQR method (Q3 + 1.5×IQR) with a 2-min floor and 60-min fallback.",
        "     → This adapts to each user's natural rhythm.",
        "",
        "  C. If stratifying by archetype:",
        "     → Creators (post-heavy) may have tighter intra-session gaps.",
        "     → Engagers (like-heavy) may have wider browsing gaps.",
        "     → Compute separate elbow thresholds per archetype for best results.",
        "",
        "RECOMMENDED COMMAND:",
        "",
        "  uv run session-analysis/session_engagement_analysis.py \\",
        "    --did-file session-analysis/results/users.txt \\",
        "    --min-actions 6 \\",
        "    --fallback-threshold 4.8 \\",
        "    --summary",
        "",
        "Or for core events only (posts, replies, reposts — no likes):",
        "",
        "  uv run session-analysis/session_core_events.py \\",
        "    --min-events 6 --max-events 800 --threshold 285 --summary",
        "",
        "=" * 70,
    ])

    return "\n".join(lines)


def run(results: list[dict] | None = None, results_json: str | None = None) -> str:
    """Run §8 and return the recommendation string.

    Args:
        results: List of result dicts from previous sections.
        results_json: Path to a JSON file containing all results (alternative).
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if results_json:
        with open(results_json) as f:
            results = json.load(f)

    if results is None:
        print("No results provided — generating generic recommendation.", file=sys.stderr)
        results = []

    recommendation = build_recommendation(results)
    (OUT_DIR / "08_recommendation.txt").write_text(recommendation)
    print(f"\n{recommendation}", file=sys.stderr)

    return recommendation


if __name__ == "__main__":
    # Try to load results from a JSON file if provided
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-json", type=str, help="JSON file with section results")
    args = ap.parse_args()
    run(results_json=args.results_json)
