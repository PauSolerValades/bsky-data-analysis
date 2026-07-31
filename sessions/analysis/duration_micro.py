"""Check for microsessions: singletons and very short sessions.

Usage:
    uv run sessions/analysis/duration_micro.py --table-name sessions_tukey_k1_5
    uv run sessions/analysis/duration_micro.py --table-name sessions_tukey_k1_5 --plot-dir ../hyperparameter/plots/tukey/k1_5
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import seaborn as sns
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env")
from running_locally.local_db import Where, get_connection as local_connect

WHERE = Where.from_env()
TBL_PREFIX = "pau_db." if WHERE == Where.SERVER else ""

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

DEFAULT_OUT = Path(__file__).resolve().parent / "results"


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Microsession check.")
    parser.add_argument("--table-name", type=str, required=True)
    parser.add_argument("--plot-dir", type=str, default=str(DEFAULT_OUT),
                        help="Output directory for the plot")
    args = parser.parse_args()
    plot_dir = Path(args.plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    conn = local_connect(Where.from_env(), repo_root=str(REPO))

    rows = conn.query(f"""
        SELECT duration_s, is_singleton
        FROM {TBL_PREFIX}{args.table_name}
    """)
    durations = np.array([r[0] for r in rows], dtype=np.float64)
    singletons = np.array([r[1] for r in rows], dtype=np.int64)

    user_rows = conn.query(f"""
        SELECT did, AVG(is_singleton) AS singleton_ratio, COUNT(*) AS n
        FROM {TBL_PREFIX}{args.table_name}
        GROUP BY did
    """)
    singleton_ratios = np.array([r[1] for r in user_rows])

    conn.close()

    n_total = len(durations)
    n_single = int(singletons.sum())
    pct_single = 100 * n_single / n_total
    pct_lt1 = 100 * np.mean(durations < 1)
    pct_lt5 = 100 * np.mean(durations < 5)
    pct_lt60 = 100 * np.mean(durations < 60)

    print(f"── Microsession check: {args.table_name} ──")
    print(f"  Total sessions:     {n_total:>12,}")
    print(f"  Singletons:         {n_single:>12,}  ({pct_single:.1f}%)")
    print(f"  Duration < 1s:      {np.sum(durations < 1):>12,}  ({pct_lt1:.1f}%)")
    print(f"  Duration < 5s:      {np.sum(durations < 5):>12,}  ({pct_lt5:.1f}%)")
    print(f"  Duration < 60s:     {np.sum(durations < 60):>12,}  ({pct_lt60:.1f}%)")
    print(f"  Median duration:    {np.median(durations):>12.1f}s")
    print(f"  Median singleton ratio per user: {np.median(singleton_ratios):.2f}")

    # ── Plot ──────────────────────────────────────────────────────────

    fig, ax = plt.subplots(figsize=(8, 5))
    palette = sns.color_palette("colorblind")

    micro = durations[(durations >= 0) & (durations <= 60)]
    bins = np.linspace(0, 60, 61)
    ax.hist(micro, bins=bins, color=palette[0], alpha=0.7,
            edgecolor="white", linewidth=0.2)
    ax.set_xlabel("Session duration (seconds)")
    ax.set_ylabel("Sessions")
    ax.set_title(f"Microsession check — {args.table_name}\n(duration ≤ 60s, {len(micro):,} sessions)",
                 fontsize=12, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

    text = (
        f"total: {n_total:,}\n"
        f"singletons: {n_single:,} ({pct_single:.1f}%)\n"
        f"< 1s: {pct_lt1:.1f}%\n"
        f"< 5s: {pct_lt5:.1f}%\n"
        f"< 60s: {pct_lt60:.1f}%"
    )
    ax.text(0.95, 0.95, text, transform=ax.transAxes, ha="right", va="top",
            fontsize=9, bbox=dict(boxstyle="round,pad=0.3",
                                  facecolor="white", alpha=0.85))

    fig.tight_layout()
    path = plot_dir / f"duration_micro__{args.table_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


if __name__ == "__main__":
    main()
