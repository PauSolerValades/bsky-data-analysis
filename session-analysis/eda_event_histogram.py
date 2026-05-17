#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "matplotlib",
#     "seaborn",
#     "numpy",
# ]
# ///
"""
Quick EDA: event-count histogram from pau_db.user_core_events.

Generates a bar chart of events-per-user buckets, used in the documentation
to justify the activity thresholds for session analysis.

Usage:
    uv run session-analysis/eda_event_histogram.py
"""

import os
import sys
import time as time_mod
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pymysql
import seaborn as sns


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_env_file():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        print(f"ERROR: .env not found at {env_path}", file=sys.stderr)
        sys.exit(1)
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val and key not in os.environ:
                os.environ[key] = val


_load_env_file()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


DB_CONFIG = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": _env("DATABASE_NAME", "bsky"),
    "charset": "utf8mb4",
}

OUT_DIR = Path(__file__).resolve().parent / "results"

QUERY = """
SELECT
    CASE
        WHEN n = 1 THEN 1
        WHEN n = 2 THEN 2
        WHEN n = 3 THEN 3
        WHEN n = 4 THEN 4
        WHEN n = 5 THEN 5
        WHEN n BETWEEN 6 AND 10 THEN 6
        WHEN n BETWEEN 11 AND 25 THEN 7
        WHEN n BETWEEN 26 AND 50 THEN 8
        WHEN n BETWEEN 51 AND 100 THEN 9
        WHEN n BETWEEN 101 AND 500 THEN 10
        WHEN n BETWEEN 501 AND 1000 THEN 11
        WHEN n BETWEEN 1001 AND 5000 THEN 12
        ELSE 13
    END AS bucket,
    COUNT(*) AS users
FROM (
    SELECT did, COUNT(*) AS n
    FROM pau_db.user_core_events
    GROUP BY did
) t
GROUP BY bucket
ORDER BY bucket
"""

BUCKET_LABELS = [
    "1", "2", "3", "4", "5",
    "6–10", "11–25", "26–50", "51–100",
    "101–500", "501–1K", "1K–5K", "5K+",
]

# Colour buckets: tourists (grey), active (blue), bots (red)
BUCKET_COLORS = (
    ["#AAAAAA"] * 5           # 1–5: tourists
    + ["#4A90D9"] * 3          # 6–50: active
    + ["#F5A623"] * 2          # 51–500: heavy / borderline
    + ["#D94A4A"] * 3          # 501+: bots
)


def main():
    print(f"Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']} ...", file=sys.stderr)
    conn = pymysql.connect(**DB_CONFIG)

    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute(QUERY)
        rows = cur.fetchall()
    conn.close()

    buckets = [r[0] for r in rows]
    counts = np.array([int(r[1]) for r in rows])
    total = int(counts.sum())
    print(f"  → {total:,} users in {len(rows)} buckets ({time_mod.time() - t0:.1f}s)",
          file=sys.stderr)

    # Cumulative percentages
    labels = [BUCKET_LABELS[b - 1] for b in buckets]
    colors = [BUCKET_COLORS[b - 1] for b in buckets]
    cum_pct = np.cumsum(counts) / total * 100

    # Plot
    sns.set_theme(style="whitegrid", context="talk")
    fig, ax = plt.subplots(figsize=(16, 8))

    bars = ax.bar(range(len(labels)), counts, color=colors, alpha=0.9, edgecolor="white", linewidth=0.5)

    # Annotate bars with count and percentage
    for i, (c, pct) in enumerate(zip(counts, counts / total * 100)):
        if pct >= 1.0:
            ax.text(i, c + max(counts) * 0.015, f"{c:,}\n({pct:.1f}%)",
                    ha="center", va="bottom", fontsize=9, fontweight="bold",
                    color=colors[i])
        else:
            ax.text(i, c + max(counts) * 0.03, f"{c:,}\n({pct:.1f}%)",
                    ha="center", va="bottom", fontsize=8, color=colors[i])

    # Cumulative line
    ax2 = ax.twinx()
    ax2.plot(range(len(labels)), cum_pct, "o-", color="#333333", linewidth=2, markersize=6,
             label="Cumulative %")
    ax2.set_ylabel("Cumulative % of users", fontsize=13, color="#333333")
    ax2.tick_params(axis="y", labelcolor="#333333")
    ax2.set_ylim(0, 105)
    ax2.legend(loc="lower right", fontsize=11)

    # Threshold annotations
    ax.axvline(x=4.5, color="#666666", linewidth=1.5, linestyle="--", alpha=0.7)
    ax.text(4.7, max(counts) * 0.92, f"52.7% tourists\n(≤5 events)",
            fontsize=10, color="#666666", fontweight="bold")

    ax.axvline(x=9.5, color="#D94A4A", linewidth=1.5, linestyle="--", alpha=0.7)
    ax.text(9.7, max(counts) * 0.82, "Bots\n(100+/day)",
            fontsize=10, color="#D94A4A", fontweight="bold")

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_xlabel("Events per user (8-day window)", fontsize=14)
    ax.set_ylabel("Number of users", fontsize=14)
    ax.set_title(
        f"User activity distribution — pau_db.user_core_events\n"
        f"N = {total:,} users, 8 days of Bluesky firehose data",
        fontsize=16, fontweight="bold",
    )
    ax.tick_params(labelsize=11)

    # Legend for colours
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor="#AAAAAA", label="Tourists (≤5 events)"),
        Patch(facecolor="#4A90D9", label="Active users (6–50)"),
        Patch(facecolor="#F5A623", label="Heavy / borderline (51–500)"),
        Patch(facecolor="#D94A4A", label="Likely automated (500+)"),
    ]
    ax.legend(handles=legend_elements, loc="upper right", fontsize=10)

    fig.tight_layout()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUT_DIR / "eda_event_histogram.png"
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot saved to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
