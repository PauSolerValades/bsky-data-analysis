"""
Shared utilities for raw-session analysis.
"""

import os
import sys
import time as time_mod
from enum import Enum
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pymysql
from dotenv import load_dotenv
from scipy.stats import gaussian_kde

# ---------------------------------------------------------------------------
# Source enum
# ---------------------------------------------------------------------------

class Source(Enum):
    CORE = "core"
    ALL = "all"

    @property
    def table(self) -> str:
        return _SOURCE_TABLES[self]

    @property
    def color(self) -> str:
        return _SOURCE_COLORS[self]

    @property
    def label(self) -> str:
        return _SOURCE_LABELS[self]


_SOURCE_TABLES = {
    Source.CORE: "pau_db.sessions_raw_core",
    Source.ALL: "pau_db.sessions_raw_all",
}

_SOURCE_COLORS = {
    Source.CORE: "#4A90D9",
    Source.ALL: "#E6842A",
}

_SOURCE_LABELS = {
    Source.CORE: "Core (bsky.records + posts)",
    Source.ALL: "All (pau_db.all_events)",
}

N_BINS = 80


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent.parent
OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)

ENV_PATH = REPO / ".env"
load_dotenv(ENV_PATH)

DB_CONFIG = {
    "host": os.getenv("DATABASE_HOST", "10.18.74.14"),
    "port": int(os.getenv("DATABASE_PORT", "9030")),
    "user": os.getenv("DATABASE_USER", "pau"),
    "password": os.getenv("PAU_PASSWORD", ""),
    "database": "bsky",
    "charset": "utf8mb4",
}

plt.style.use("ggplot")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_connection() -> pymysql.Connection:
    return pymysql.connect(**DB_CONFIG)


def fetch_column(conn: pymysql.Connection, table: str, column: str) -> np.ndarray:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {column} FROM {table}")
        return np.array([r[0] for r in cur], dtype=np.float64)


def fetch_per_user_stats(conn: pymysql.Connection, table: str) -> dict[str, np.ndarray]:
    """Per-user: session count, median duration, median gap."""
    sql = f"""
        SELECT did, session_start, session_end, duration_s
        FROM {table}
        ORDER BY did, session_start
    """
    print(f"  Fetching per-user stats from {table} ...", file=sys.stderr)
    t0 = time_mod.time()

    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    print(f"    → {len(rows):,} rows in {time_mod.time() - t0:.0f}s", file=sys.stderr)

    from collections import defaultdict
    user_data: dict[str, list] = defaultdict(list)
    for did, start, end, dur in rows:
        user_data[did].append((int(start), int(end), float(dur)))

    n_sessions = []
    median_dur = []
    median_gap = []

    for sessions in user_data.values():
        durs = [s[2] for s in sessions]
        n_sessions.append(len(sessions))
        median_dur.append(np.median(durs))

        gaps = []
        for i in range(1, len(sessions)):
            gap = (sessions[i][0] - sessions[i - 1][1]) / 1_000_000
            if gap > 0:
                gaps.append(gap)
        median_gap.append(np.median(gaps) if gaps else np.nan)

    n_sessions = np.array(n_sessions, dtype=np.int64)
    median_dur = np.array(median_dur, dtype=np.float64)
    median_gap = np.array(median_gap, dtype=np.float64)

    print(f"    → {len(user_data):,} users", file=sys.stderr)

    return {
        "n_sessions": n_sessions,
        "median_dur": median_dur,
        "median_gap": median_gap,
    }


# ---------------------------------------------------------------------------
# Plot helpers — each saves ONE file
# ---------------------------------------------------------------------------

def _filename(section: str, source: Source, kind: str) -> str:
    tag = kind.lower().replace(" ", "_").replace("(", "").replace(")", "")
    return f"{section}_{source.value}_{tag}.png"


def savefig(fig, name: str, dpi: int = 150):
    path = OUT / name
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {path}", file=sys.stderr)


def save_hist(data: np.ndarray, source: Source, section: str, suffix: str,
              xlabel: str, clip_pct: int = 99):
    """Regular histogram, one file."""
    data = np.asarray(data, dtype=np.float64)
    data = data[~np.isnan(data)]
    if clip_pct:
        data = data[data <= np.percentile(data, clip_pct)]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(data, bins=N_BINS, color=source.color, alpha=0.8, edgecolor="none")
    ax.axvline(np.median(data), color="black", linestyle="--", linewidth=1.5)
    ax.text(np.median(data) * 1.05, ax.get_ylim()[1] * 0.9,
            f"median={np.median(data):.1f}", fontsize=10)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Count")
    ax.set_title(f"{source.label}\n{suffix} (P{clip_pct} clipped)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    savefig(fig, _filename(section, source, suffix.lower().replace(" ", "_")))


def save_loglog(data: np.ndarray, source: Source, section: str, suffix: str,
                xlabel: str, n_bins: int = 60):
    """Log-log histogram, one file."""
    data = np.asarray(data, dtype=np.float64)
    data = data[~np.isnan(data) & (data > 0)]
    if len(data) == 0:
        return

    lo = max(np.log10(data.min()), -1)
    hi = np.log10(data.max()) + 0.1
    bins = np.logspace(lo, hi, n_bins)
    hist, edges = np.histogram(data, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    nonzero = hist > 0

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.loglog(centers[nonzero], hist[nonzero], "-", color=source.color,
              linewidth=1.5, alpha=0.8)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Count")
    ax.set_title(f"{source.label}\n{suffix} (log-log)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    savefig(fig, _filename(section, source, suffix.lower().replace(" ", "_")))


def save_pdf(data: np.ndarray, source: Source, section: str, suffix: str,
             xlabel: str, clip_pct: int = 99):
    """PDF (histogram normalized + KDE), one file."""
    data = np.asarray(data, dtype=np.float64)
    data = data[~np.isnan(data) & (data > 0)]
    if len(data) < 3:
        return

    if clip_pct:
        clip_val = np.percentile(data, clip_pct)
        clipped = data[data <= clip_val]
    else:
        clipped = data

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(clipped, bins=N_BINS, density=True, color=source.color,
            alpha=0.4, edgecolor="none", label="Histogram")
    kde = gaussian_kde(clipped)
    xs = np.linspace(clipped.min(), clipped.max(), 200)
    ax.plot(xs, kde(xs), "-", color=source.color, linewidth=2, label="KDE")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Density")
    ax.set_title(f"{source.label}\n{suffix} (PDF, P{clip_pct} clipped)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    savefig(fig, _filename(section, source, suffix.lower().replace(" ", "_")))


def save_ccdf(data: np.ndarray, source: Source, section: str, suffix: str,
              xlabel: str):
    """Complementary CDF, one file."""
    data = np.asarray(data, dtype=np.float64)
    data = data[~np.isnan(data) & (data > 0)]
    if len(data) < 2:
        return

    sorted_data = np.sort(data)
    ccdf = 1 - np.arange(1, len(sorted_data) + 1) / len(sorted_data)

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.loglog(sorted_data, ccdf, "-", color=source.color, linewidth=1.5)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("P(X > x)")
    ax.set_title(f"{source.label}\n{suffix} (CCDF)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    savefig(fig, _filename(section, source, suffix.lower().replace(" ", "_")))


def print_percentiles(data: np.ndarray, label: str,
                      ps: list[int] | None = None):
    if ps is None:
        ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    d = np.asarray(data, dtype=np.float64)
    d = d[~np.isnan(d)]
    print(f"\n  {label} (n={len(d):,}):", file=sys.stderr)
    print(f"    Mean: {np.mean(d):.1f}", file=sys.stderr)
    for pv in ps:
        print(f"    P{pv:>2d}: {np.percentile(d, pv):>12.1f}", file=sys.stderr)
