"""Fit session duration or inter-session gap distributions with distfit.

Usage:
    uv run sessions/analysis/fit_distribution.py --table-name sessions_tukey_k1_5 --column duration_s
    uv run sessions/analysis/fit_distribution.py --table-name sessions_tukey_k1_5 --column gap_s --plot-dir ../hyperparameter/plots/tukey/k1_5
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from distfit import distfit
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
    "legend.fontsize": 9,
})

DEFAULT_OUT = Path(__file__).resolve().parent / "results"

TIME_DISTS = ['expon', 'gamma', 'lognorm', 'weibull_min',
              'pareto', 'lomax', 'genpareto']


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fit distribution with distfit.")
    parser.add_argument("--table-name", type=str, required=True)
    parser.add_argument("--column", type=str, required=True,
                        choices=["duration_s", "gap_s"])
    parser.add_argument("--sample", type=int, default=100_000,
                        help="Max rows to fit (distfit is CPU-heavy)")
    parser.add_argument("--gof", type=str, default="RSS",
                        choices=["RSS", "wasserstein", "ks", "energy", "goodness_of_fit"],
                        help="Goodness-of-fit statistic")
    parser.add_argument("--plot-dir", type=str, default=str(DEFAULT_OUT),
                        help="Output directory for plot and tsv")
    args = parser.parse_args()
    plot_dir = Path(args.plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    conn = local_connect(Where.from_env(), repo_root=str(REPO))

    if args.column == "duration_s":
        rows = conn.query(f"""
            SELECT duration_s FROM {TBL_PREFIX}{args.table_name}
            WHERE duration_s > 0
            LIMIT {args.sample}
        """)
        data = np.array([r[0] for r in rows], dtype=np.float64)
        xlabel = "Session duration (s)"
        col_label = "duration"
    else:
        rows = conn.query(f"""
            SELECT gap_s FROM (
                SELECT
                    (LEAD(session_start) OVER (PARTITION BY did ORDER BY session_start)
                     - session_end) / 1000000.0 AS gap_s
                FROM {TBL_PREFIX}{args.table_name}
            ) t
            WHERE gap_s IS NOT NULL AND gap_s > 0
            LIMIT {args.sample}
        """)
        data = np.array([r[0] for r in rows], dtype=np.float64)
        xlabel = "Inter-session gap (s)"
        col_label = "gap"

    conn.close()

    data = data[data > 0]
    print(f"── distfit: {args.table_name} / {col_label} ──")
    print(f"  n = {len(data):,}  |  median = {np.median(data):.1f}  |  "
          f"P90 = {np.percentile(data, 90):.1f}")

    # ── distfit ────────────────────────────────────────────────────────

    dfit = distfit(distr=TIME_DISTS, stats=args.gof, n_boots=10, verbose=0)
    dfit.fit_transform(data)
    summary = dfit.summary[["name", "score", "bootstrap_score", "bootstrap_pass", "loc", "scale", "arg"]]

    print(f"\n  Best: {dfit.model['name']}  "
          f"(score={dfit.model['score']:.2e}, bootstrap={'pass' if dfit.model.get('bootstrap_pass') else 'fail'})")
    print(f"  Top 5:")
    for _, row in summary.head(5).iterrows():
        print(f"    {row['name']:<20s}  score={row['score']:.2e}")

    # ── TSV ────────────────────────────────────────────────────────────

    tsv_path = plot_dir / f"fit_{col_label}_{args.gof}__{args.table_name}.tsv"
    summary.to_csv(tsv_path, sep="\t", index=False)
    print(f"  → saved {tsv_path}")

    # ── Plot ───────────────────────────────────────────────────────────

    palette = sns.color_palette("colorblind")
    fig, ax = plt.subplots(figsize=(8, 5))

    lo, hi = np.log10(max(data.min(), 0.1)), np.log10(data.max())
    bins = np.logspace(lo, hi, 60)
    ax.hist(data, bins=bins, color=palette[0], alpha=0.6,
            edgecolor="white", linewidth=0.2, density=True, label="data")

    x_fit = np.logspace(lo, hi, 200)
    y_pdf = dfit.model["model"].pdf(x_fit)
    ax.plot(x_fit, y_pdf, color=palette[1], linewidth=2,
            label=f"{dfit.model['name']} (score={dfit.model['score']:.3f})")

    ax.set_xscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Density")
    ax.set_title(f"Distribution fit — {args.table_name} / {col_label}",
                 fontsize=12, fontweight="bold")
    ax.legend()

    fig.tight_layout()
    path = plot_dir / f"fit_{col_label}_{args.gof}__{args.table_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


if __name__ == "__main__":
    main()
