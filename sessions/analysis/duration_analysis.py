"""Combined macro + micro session duration analysis with TSV output.

Generates two plots + one TSV with key metrics.

Usage:
    uv run sessions/analysis/duration_analysis.py --table-name sessions_tukey_k1_5
    uv run sessions/analysis/duration_analysis.py --table-name sessions_tukey_k1_5 --plot-dir ../hyperparameter/plots/tukey/k1_5
"""

import argparse
import csv
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
    parser = argparse.ArgumentParser(description="Session duration analysis.")
    parser.add_argument("--table-name", type=str, required=True)
    parser.add_argument("--plot-dir", type=str, default=str(DEFAULT_OUT))
    args = parser.parse_args()
    plot_dir = Path(args.plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    conn = local_connect(Where.from_env(), repo_root=str(REPO))
    rows = conn.query(f"""
        SELECT duration_s, is_singleton
        FROM {TBL_PREFIX}{args.table_name}
    """)
    conn.close()

    durations = np.array([r[0] for r in rows], dtype=np.float64)
    singletons = np.array([r[1] for r in rows], dtype=np.int64)
    pos = durations[durations > 0]

    # ── Metrics ────────────────────────────────────────────────────────

    n_total = len(durations)
    n_single = int(singletons.sum())
    pct_lt1 = 100 * np.mean(durations < 1)
    pct_lt5 = 100 * np.mean(durations < 5)
    pct_lt60 = 100 * np.mean(durations < 60)
    pct_gt1h = 100 * np.mean(durations > 3600)
    pct_gt4h = 100 * np.mean(durations > 14400)
    pct_gt8h = 100 * np.mean(durations > 28800)

    metrics = {
        "table": args.table_name,
        "n_sessions": n_total,
        "n_singletons": n_single,
        "pct_singletons": round(100 * n_single / n_total, 2),
        "pct_lt_1s": round(pct_lt1, 2),
        "pct_lt_5s": round(pct_lt5, 2),
        "pct_lt_60s": round(pct_lt60, 2),
        "pct_gt_1h": round(pct_gt1h, 2),
        "pct_gt_4h": round(pct_gt4h, 2),
        "pct_gt_8h": round(pct_gt8h, 2),
        "median_s": round(np.median(pos), 1),
        "mean_s": round(pos.mean(), 1),
        "p90_s": round(np.percentile(pos, 90), 0),
        "p95_s": round(np.percentile(pos, 95), 0),
        "p99_s": round(np.percentile(pos, 99), 0),
        "max_s": round(pos.max(), 0),
    }

    print(f"── Duration analysis: {args.table_name} ──")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    # ── TSV ────────────────────────────────────────────────────────────

    tsv_path = plot_dir / f"duration_metrics__{args.table_name}.tsv"
    with open(tsv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(metrics.keys()), delimiter="\t")
        w.writeheader()
        w.writerow(metrics)
    print(f"  → saved {tsv_path}")

    # ── Plot 1: CCDF (macro) ───────────────────────────────────────────

    palette = sns.color_palette("colorblind")
    fig, ax = plt.subplots(figsize=(8, 5))
    sorted_d = np.sort(pos)
    ccdf = 1 - np.arange(1, len(sorted_d) + 1) / len(sorted_d)
    ax.step(sorted_d, ccdf, where="post", color=palette[0], linewidth=1.5)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Session duration (s)")
    ax.set_ylabel("P(Duration ≥ x)")
    ax.set_title(f"Duration CCDF — {args.table_name}", fontsize=12, fontweight="bold")
    fig.tight_layout()
    path = plot_dir / f"duration_macro__{args.table_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")

    # ── Plot 2: micro (0–60s histogram) ────────────────────────────────

    fig, ax = plt.subplots(figsize=(8, 5))
    micro = durations[(durations >= 0) & (durations <= 60)]
    bins = np.linspace(0, 60, 61)
    ax.hist(micro, bins=bins, color=palette[0], alpha=0.7,
            edgecolor="white", linewidth=0.2)
    ax.set_xlabel("Session duration (s)")
    ax.set_ylabel("Sessions")
    ax.set_title(f"Duration ≤ 60s — {args.table_name}",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()
    path = plot_dir / f"duration_micro__{args.table_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


if __name__ == "__main__":
    main()
