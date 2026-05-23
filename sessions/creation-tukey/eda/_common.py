"""
Shared utilities for all EDA modules: env loading, DB connection, per-user stats cache.

Reuses credentials from the project-root .env file.
Cached per-user aggregates live in eda/results/per_user_stats.parquet.
"""

import os
import sys
import time as time_mod
from pathlib import Path

import numpy as np
import polars as pl
import pymysql

# ---------------------------------------------------------------------------
# Env / config
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
OUT_DIR = Path(__file__).resolve().parent / "results"
CACHE_PATH = OUT_DIR / "per_user_stats.parquet"


def _load_env_file() -> None:
    """Load .env from repo root if present and keys are not already set."""
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        print(f"WARNING: .env not found at {env_path}", file=sys.stderr)
        return
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

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def get_connection() -> pymysql.Connection:
    """Return a new pymysql connection using the project .env credentials."""
    return pymysql.connect(**DB_CONFIG)


# ---------------------------------------------------------------------------
# Per-user aggregate stats (cached to parquet)
# ---------------------------------------------------------------------------

PER_USER_STATS_SQL = """
SELECT
    e.did,
    e.total_events,
    e.n_posts,
    e.n_replies,
    e.n_reposts,
    e.first_us,
    e.last_us,
    e.active_days,
    COALESCE(u.num_likes,     0) AS n_likes,
    COALESCE(u.num_follows,   0) AS n_follows,
    COALESCE(u.num_posts,     0) AS n_posts_all,
    COALESCE(u.num_reposts,   0) AS n_reposts_all
FROM (
    SELECT
        did,
        COUNT(*)                                                   AS total_events,
        SUM(CASE WHEN event_type = 'post'   THEN 1 ELSE 0 END)     AS n_posts,
        SUM(CASE WHEN event_type = 'reply'  THEN 1 ELSE 0 END)     AS n_replies,
        SUM(CASE WHEN event_type = 'repost' THEN 1 ELSE 0 END)     AS n_reposts,
        MIN(time_us)                                               AS first_us,
        MAX(time_us)                                               AS last_us,
        COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000)))     AS active_days
    FROM pau_db.user_core_events
    GROUP BY did
) e
LEFT JOIN pau_db.users u ON e.did = u.did
"""


def fetch_per_user_stats(conn: pymysql.Connection) -> pl.DataFrame:
    """Run the big aggregate query and return a polars DataFrame."""
    print("Querying per-user aggregates from pau_db.user_core_events ...", file=sys.stderr)
    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute(PER_USER_STATS_SQL)
        rows = cur.fetchall()
    elapsed = time_mod.time() - t0
    print(f"  → {len(rows):,} rows in {elapsed:.0f}s", file=sys.stderr)

    df = pl.DataFrame(
        rows,
        schema=[
            "did", "total_events", "n_posts", "n_replies", "n_reposts",
            "first_us", "last_us", "active_days",
            "n_likes", "n_follows", "n_posts_all", "n_reposts_all",
        ],
        orient="row",
    )
    # Ensure numeric columns are the right type
    df = df.with_columns([
        pl.col("total_events").cast(pl.Int64),
        pl.col("n_posts").cast(pl.Int64),
        pl.col("n_replies").cast(pl.Int64),
        pl.col("n_reposts").cast(pl.Int64),
        pl.col("first_us").cast(pl.Int64),
        pl.col("last_us").cast(pl.Int64),
        pl.col("active_days").cast(pl.Int64),
        pl.col("n_likes").cast(pl.Int64),
        pl.col("n_follows").cast(pl.Int64),
        pl.col("n_posts_all").cast(pl.Int64),
        pl.col("n_reposts_all").cast(pl.Int64),
    ])

    # Derived columns
    df = df.with_columns([
        ((pl.col("last_us") - pl.col("first_us")) / 3_600_000_000.0).alias("span_hours"),
        (pl.col("total_events") / pl.col("active_days").cast(pl.Float64)).alias("events_per_active_day"),
    ])

    return df


def load_or_fetch_stats(force: bool = False) -> pl.DataFrame:
    """Return per-user stats, loading from parquet cache or fetching from DB.

    Args:
        force: If True, re-fetch from DB even if cache exists.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not force and CACHE_PATH.exists():
        print(f"Loading cached stats from {CACHE_PATH} ...", file=sys.stderr)
        return pl.read_parquet(CACHE_PATH)

    conn = get_connection()
    try:
        df = fetch_per_user_stats(conn)
    finally:
        conn.close()

    df.write_parquet(CACHE_PATH)
    print(f"  → Cached to {CACHE_PATH}", file=sys.stderr)
    return df


# ---------------------------------------------------------------------------
# Plot utilities
# ---------------------------------------------------------------------------

def set_mpl_style():
    """Configure matplotlib/seaborn defaults for consistent plots."""
    import matplotlib.pyplot as plt
    import seaborn as sns
    sns.set_theme(style="whitegrid", context="notebook", font_scale=1.1)


def savefig(fig, name: str, dpi: int = 150):
    """Save figure to eda/results/<name>, creating the directory if needed."""
    import matplotlib.pyplot as plt
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  → Saved {path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Power-law / heavy-tail helpers
# ---------------------------------------------------------------------------

def powerlaw_fit_tail(
    data: np.ndarray,
    n_xmins: int = 50,
    verbose: bool = False,
) -> dict:
    """Fit a power-law distribution to the tail of *data* using MLE.

    Follows Clauset-Shalizi-Newman: scan candidate xmin values, pick the one
    that minimizes the KS statistic between the empirical CDF and the fitted
    power-law.  Returns {xmin, alpha, ks_stat, n_tail}.

    Requires scipy.  No external 'powerlaw' package needed.
    """
    from scipy import stats as scistats

    data = np.asarray(data, dtype=np.float64)
    data = data[data > 0]
    if len(data) < 20:
        if verbose:
            print("  powerlaw_fit_tail: too few data points, skipping", file=sys.stderr)
        return {"xmin": 1, "alpha": 2.0, "ks_stat": 1.0, "n_tail": len(data)}

    data_sorted = np.sort(data)
    # Candidate xmins: unique values in the lower half of the data
    candidates = np.unique(data_sorted[data_sorted <= np.median(data_sorted)])
    if len(candidates) > n_xmins:
        idx = np.linspace(0, len(candidates) - 1, n_xmins, dtype=int)
        candidates = candidates[idx]

    best_ks = np.inf
    best_xmin = 1
    best_alpha = 2.0

    for xmin in candidates:
        tail = data[data >= xmin]
        if len(tail) < 10:
            continue
        # MLE for power-law exponent (continuous, x >= xmin)
        alpha = 1 + len(tail) / np.sum(np.log(tail / xmin))
        # KS statistic
        tail_sorted = np.sort(tail)
        empirical_cdf = np.arange(1, len(tail_sorted) + 1) / len(tail_sorted)
        theoretical_cdf = 1 - (tail_sorted / xmin) ** (1 - alpha)
        ks = np.max(np.abs(empirical_cdf - theoretical_cdf))
        if ks < best_ks:
            best_ks = ks
            best_xmin = xmin
            best_alpha = alpha

    if verbose:
        print(f"  Power-law fit: xmin={best_xmin:.0f}, α={best_alpha:.3f}, "
              f"KS={best_ks:.4f}, n_tail={np.sum(data >= best_xmin)}", file=sys.stderr)

    return {
        "xmin": best_xmin,
        "alpha": best_alpha,
        "ks_stat": best_ks,
        "n_tail": int(np.sum(data >= best_xmin)),
    }


def log_spaced_bins(data: np.ndarray, n_bins: int = 30) -> np.ndarray:
    """Return log-spaced bin edges covering the range of *data*.

    The first bin starts at 0.5 to catch the 1-event users nicely.
    """
    data = np.asarray(data, dtype=np.float64)
    xmax = data.max()
    bin_min = max(0.5, data[data > 0].min() * 0.8) if (data > 0).any() else 0.5
    return np.logspace(np.log10(bin_min), np.log10(xmax * 1.1), n_bins)
