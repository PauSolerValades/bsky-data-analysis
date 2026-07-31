"""Histogram of session durations (log scale, no distribution fit).

Usage:
    uv run sessions/analysis/hist_session_length.py --table-name sessions_tukey_k1_5
    uv run sessions/analysis/hist_session_length.py --table-name sessions_tukey_k1_5 --plot-dir ../hyperparameter/plots/tukey/k1_5
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
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


def main():
    parser = argparse.ArgumentParser(description="Session duration histogram.")
    parser.add_argument("--table-name", type=str, required=True)
    parser.add_argument("--sample", type=int, default=500_000,
                        help="Max rows to load")
    parser.add_argument("--bins", type=int, default=80,
                        help="Number of log-spaced bins")
    parser.add_argument("--plot-dir", type=str, default=str(DEFAULT_OUT))
    args = parser.parse_args()
    plot_dir = Path(args.plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    conn = local_connect(Where.from_env(), repo_root=str(REPO))
    rows = conn.query(f"""
        SELECT duration_s FROM {TBL_PREFIX}{args.table_name}
        WHERE duration_s > 0
        LIMIT {args.sample}
    """)
    conn.close()
    data = np.array([r[0] for r in rows], dtype=np.float64)
    data = data[data > 0]

    # ── Stats ──────────────────────────────────────────────────────────

    print(f"── Session duration histogram: {args.table_name} ──")
    print(f"  n = {len(data):,}")
    print(f"  median = {np.median(data):.1f}s")

    # ── Plot ───────────────────────────────────────────────────────────

    palette = sns.color_palette("colorblind")
    fig, ax = plt.subplots(figsize=(8, 5))

    lo = max(data.min(), 0.1)
    hi = data.max()
    bins = np.logspace(np.log10(lo), np.log10(hi), args.bins)
    ax.hist(data, bins=bins, color=palette[0], alpha=0.7,
            edgecolor="white", linewidth=0.2)

    ax.set_xscale("log")
    ax.set_xlabel("Session duration (s)")
    ax.set_ylabel("Sessions")
    ax.set_title(f"Duration distribution — {args.table_name}",
                 fontsize=12, fontweight="bold")

    fig.tight_layout()
    path = plot_dir / f"hist_duration__{args.table_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


if __name__ == "__main__":
    main()
