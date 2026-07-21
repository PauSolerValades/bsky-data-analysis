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
    CORE_TUKEY    = "core_tukey"
    ALL_TUKEY     = "all_tukey"
    ALL_HDBSCAN   = "all_hdbscan"
    ALL_HDBSCAN_E30  = "all_hdbscan_e30"
    ALL_HDBSCAN_E120 = "all_hdbscan_e120"
    ALL_HDBSCAN_E300 = "all_hdbscan_e300"
    ALL_HDBSCAN_E120_MS5  = "all_hdbscan_e120_ms5"
    ALL_HDBSCAN_E120_MS10 = "all_hdbscan_e120_ms10"

    @property
    def table(self) -> str:
        return f"pau_db.sessions_raw_{self.value}"

    @property
    def color(self) -> str:
        return _SOURCE_COLORS[self]

    @property
    def label(self) -> str:
        return _SOURCE_LABELS[self]


_SOURCE_COLORS = {
    Source.CORE_TUKEY:    "#4A90D9",
    Source.ALL_TUKEY:     "#E6842A",
    Source.ALL_HDBSCAN:   "#2ECC71",
    Source.ALL_HDBSCAN_E30:  "#27AE60",
    Source.ALL_HDBSCAN_E120: "#1ABC9C",
    Source.ALL_HDBSCAN_E300: "#16A085",
    Source.ALL_HDBSCAN_E120_MS5:  "#8E44AD",
    Source.ALL_HDBSCAN_E120_MS10: "#C0392B",
}

_SOURCE_LABELS = {
    Source.CORE_TUKEY:    "Core Tukey",
    Source.ALL_TUKEY:     "All Tukey",
    Source.ALL_HDBSCAN:   "All HDBSCAN (ε=60)",
    Source.ALL_HDBSCAN_E30:  "All HDBSCAN (ε=30)",
    Source.ALL_HDBSCAN_E120: "All HDBSCAN (ε=120)",
    Source.ALL_HDBSCAN_E300: "All HDBSCAN (ε=300)",
    Source.ALL_HDBSCAN_E120_MS5:  "All HDBSCAN (ε=120, ms=5)",
    Source.ALL_HDBSCAN_E120_MS10: "All HDBSCAN (ε=120, ms=10)",
}

N_BINS = 80


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent.parent
OUT = Path(__file__).resolve().parent / "plots"
OUT.mkdir(parents=True, exist_ok=True)
OUT_SUBDIR: str = ""

CLIP_PCT: int | None = None  # set by main.py via --clip-at


def set_output_dir(path: str | Path) -> None:
    """Override the output directory for plots."""
    global OUT
    OUT = Path(path)
    OUT.mkdir(parents=True, exist_ok=True)


def set_subdir(name: str) -> None:
    """Set subdirectory for subsequent saves. Pass "" to reset."""
    global OUT_SUBDIR
    OUT_SUBDIR = name

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


def fetch_column(conn: pymysql.Connection, table: str, column: str,
                 where: str | None = None) -> np.ndarray:
    sql = f"SELECT {column} FROM {table}"
    if where:
        sql += f" WHERE {where}"
    with conn.cursor() as cur:
        cur.execute(sql)
        return np.array([r[0] for r in cur], dtype=np.float64)


def fetch_per_user_stats(conn: pymysql.Connection, table: str,
                         where: str | None = None) -> dict[str, np.ndarray]:
    """Per-user: session count, median duration, median gap."""
    sql = f"""
        SELECT did, session_start, session_end, duration_s
        FROM {table}
        ORDER BY did, session_start
    """
    if where:
        sql = sql.replace("ORDER BY", f"WHERE {where} ORDER BY")
    print(f"  Fetching per-user stats from {table} ...", file=sys.stderr)
    t0 = time_mod.time()

    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    print(f"    → {len(rows):,} rows in {time_mod.time() - t0:.0f}s", file=sys.stderr)
    # pi: imports middle of the code is terrorism
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
    path = OUT / OUT_SUBDIR / name if OUT_SUBDIR else OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {path}", file=sys.stderr)


def save_hist(
    data: np.ndarray,
    source: Source,
    section: str,
    suffix: str,
    xlabel: str,
):
    """Regular histogram, one file."""
    data = np.asarray(data, dtype=np.float64)
    data = data[~np.isnan(data)]

    title_extra = ""
    if CLIP_PCT is not None:
        data = data[data <= np.percentile(data, CLIP_PCT)]
        title_extra = f" (P{CLIP_PCT} clipped)"

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(data, bins="auto", color=source.color, alpha=0.8, edgecolor="none")
    ax.axvline(np.median(data), color="black", linestyle="--", linewidth=1.5)
    ax.text(
        np.median(data) * 1.05,
        ax.get_ylim()[1] * 0.9,
        f"median={np.median(data):.1f}",
        fontsize=10,
    )
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Count")
    ax.set_title(f"{source.label}\n{suffix}{title_extra}")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    savefig(fig, _filename(section, source, suffix.lower().replace(" ", "_")))


def save_loglog(
    data: np.ndarray,
    source: Source,
    section: str,
    suffix: str,
    xlabel: str,
    n_bins: int = 60,
):
    """Log-log histogram, one file."""
    data = np.asarray(data, dtype=np.float64)
    data = data[~np.isnan(data) & (data > 0)]
    if len(data) == 0:
        return

    lo = max(np.log10(data.min()), -1)
    hi = np.log10(data.max()) + 0.1
    bins = np.logspace(lo, hi, n_bins)
    # ponytail: round bins for integer data so edges align with values
    if np.all(data == np.floor(data)):
        bins = np.unique(np.round(bins).astype(int))
    hist, edges = np.histogram(data, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    nonzero = hist > 0

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.loglog(
        centers[nonzero],
        hist[nonzero],
        "-",
        color=source.color,
        linewidth=1.5,
        alpha=0.8,
    )
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Count")
    ax.set_title(f"{source.label}\n{suffix} (log-log)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    savefig(fig, _filename(section, source, suffix.lower().replace(" ", "_")))


def save_pdf(
    data: np.ndarray,
    source: Source,
    section: str,
    suffix: str,
    xlabel: str,
):
    """PDF (histogram normalized + KDE), one file."""
    data = np.asarray(data, dtype=np.float64)
    data = data[~np.isnan(data) & (data > 0)]
    if len(data) < 3:
        return

    title_extra = ""
    if CLIP_PCT is not None:
        data = data[data <= np.percentile(data, CLIP_PCT)]
        if len(data) < 3:
            return
        title_extra = f" (P{CLIP_PCT} clipped)"

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(
        data,
        bins="auto",
        density=True,
        color=source.color,
        alpha=0.4,
        edgecolor="none",
        label="Histogram",
    )
    kde = gaussian_kde(data)
    xs = np.linspace(data.min(), data.max(), 200)
    ax.plot(xs, kde(xs), "-", color=source.color, linewidth=2, label="KDE")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Density")
    ax.set_title(f"{source.label}\n{suffix} (PDF){title_extra}")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    savefig(fig, _filename(section, source, suffix.lower().replace(" ", "_")))


def save_ccdf(data: np.ndarray, source: Source, section: str, suffix: str, xlabel: str):
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


def print_percentiles(data: np.ndarray, label: str, ps: list[int] | None = None):
    if ps is None:
        ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    d = np.asarray(data, dtype=np.float64)
    d = d[~np.isnan(d)]
    print(f"\n  {label} (n={len(d):,}):", file=sys.stderr)
    print(f"    Mean: {np.mean(d):.1f}", file=sys.stderr)
    for pv in ps:
        print(f"    P{pv:>2d}: {np.percentile(d, pv):>12.1f}", file=sys.stderr)
