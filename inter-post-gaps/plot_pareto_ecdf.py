#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "polars",
#     "matplotlib",
#     "numpy",
# ]
# ///
"""
ECDF plots + CSV of fitted Pareto parameters (α, xmin) for inter-post gaps.

For users whose best fit is powerlaw, across both gap types (global, within_session).

Output:
    results/pareto_inter_post_params.csv       — 4 columns: gap_type, alpha, xmin_s, xmin_h
    results/pareto_inter_post_ecdf.png          — univariate ECDFs (α, xmin)
    results/pareto_inter_post_bivariate_ecdf.png — bivariate ECDF (α vs xmin)

Usage:
    uv run inter-post-gaps/plot_pareto_ecdf.py
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
INPUT = HERE / "results" / "inter_post_gap_fits.csv"
OUTDIR = HERE / "results"


def ecdf(values: np.ndarray):
    x = np.sort(values)
    y = np.arange(1, len(x) + 1) / len(x)
    return x, y


COLORS = {
    "global": "#F8766D",
    "within_session": "#00BFC4",
}
LABELS = {
    "global": "Global (all posts)",
    "within_session": "Within-session",
}

PARAM_INFO = [
    ("gap_pl_alpha",   "α — Pareto exponent",   False),
    ("gap_pl_xmin_h",  "xₘᵢₙ — threshold (hours)", True),
    ("gap_pl_xmin",    "xₘᵢₙ — threshold (seconds)", True),
]


def main():
    print(f"Loading {INPUT} ...", file=sys.stderr)
    df = pl.read_csv(INPUT, null_values=["NA", ""], infer_schema_length=500000,
                     ignore_errors=True)

    # Force numeric columns
    for col in df.columns:
        if col not in ("did", "gap_best", "gap_type"):
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    # ── Export CSV: Pareto parameters for powerlaw best-fit users ─────────
    pareto = df.filter(pl.col("gap_best") == "powerlaw")

    csv_rows = []
    for row in pareto.iter_rows(named=True):
        alpha = row.get("gap_pl_alpha")
        xmin_s = row.get("gap_pl_xmin")
        xmin_h = row.get("gap_pl_xmin_h")
        if alpha is not None and xmin_s is not None and np.isfinite(alpha) and np.isfinite(xmin_s):
            csv_rows.append({
                "gap_type": row["gap_type"],
                "alpha": float(alpha),
                "xmin_s": float(xmin_s),
                "xmin_h": float(xmin_h) if xmin_h is not None and np.isfinite(xmin_h) else 0.0,
            })

    csv_df = pl.DataFrame(csv_rows)
    csv_path = OUTDIR / "pareto_inter_post_params.csv"
    csv_df.write_csv(csv_path, float_precision=6)
    print(f"  → {csv_path}  ({csv_df.shape[0]:,} rows, {csv_df.shape[1]} cols)", file=sys.stderr)

    # ── Plot 3-panel ECDF: alpha, xmin_h, xmin_s ─────────────────────────
    set_ggplot_style()

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    for ax, (param_col, label, log_x) in zip(axes, PARAM_INFO):
        for gt in ["global", "within_session"]:
            sub = pareto.filter(pl.col("gap_type") == gt)
            vals = sub[param_col].drop_nulls().to_numpy().astype(float)
            vals = vals[np.isfinite(vals)]
            if len(vals) == 0:
                continue

            x, y = ecdf(vals)
            ax.step(x, y, where="post", color=COLORS[gt], linewidth=1.5,
                    label=f"{LABELS[gt]}  (n={len(vals):,})")

            med = np.median(vals)
            ax.axvline(med, color=COLORS[gt], linestyle="--", linewidth=0.8, alpha=0.6)
            med_str = f"{med:.4g}" if med < 100 else f"{med:,.0f}"
            ax.text(med, 0.03, f"med={med_str}", color=COLORS[gt], fontsize=7,
                    rotation=90, va="bottom", ha="right")

        if log_x:
            ax.set_xscale("log")

        ax.set_xlabel(label, fontsize=10)
        ax.set_ylabel("ECDF", fontsize=10)
        ax.set_ylim(0, 1.02)
        ax.legend(fontsize=8, loc="lower right")

    fig.suptitle("Empirical CDF of fitted Pareto parameters — Inter-post gaps\n(users where powerlaw is best fit)",
                 fontsize=12, fontweight="bold", y=1.04)
    fig.tight_layout()

    out_path = OUTDIR / "pareto_inter_post_ecdf.png"
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  → {out_path}", file=sys.stderr)

    # ── Bivariate ECDF: α vs xmin (seconds) ─────────────────────────────
    fig2, axes2 = plt.subplots(1, 2, figsize=(14, 6))

    for ax, gt in zip(axes2, ["global", "within_session"]):
        sub = pareto.filter(pl.col("gap_type") == gt)
        alphas = sub["gap_pl_alpha"].drop_nulls().to_numpy().astype(float)
        xmins = sub["gap_pl_xmin"].drop_nulls().to_numpy().astype(float)
        mask = np.isfinite(alphas) & np.isfinite(xmins) & (xmins > 0)
        alphas = alphas[mask]
        xmins = xmins[mask]

        if len(alphas) == 0:
            continue

        if len(alphas) > 20_000:
            idx = np.random.default_rng(42).choice(len(alphas), 20_000, replace=False)
            a_plot, x_plot = alphas[idx], xmins[idx]
        else:
            a_plot, x_plot = alphas, xmins

        hb = ax.hexbin(np.log10(x_plot), a_plot, gridsize=50, cmap="YlOrRd",
                       bins="log", mincnt=1)
        plt.colorbar(hb, ax=ax, label="log₁₀(count)")

        ax.axhline(np.median(alphas), color="black", linestyle="--", linewidth=0.8, alpha=0.5)
        ax.axvline(np.log10(np.median(xmins)), color="black", linestyle="--", linewidth=0.8, alpha=0.5)

        ax.set_xlabel("log₁₀(xmin / seconds)", fontsize=10)
        ax.set_ylabel("α (Pareto exponent)", fontsize=10)
        ax.set_title(f"{LABELS[gt]}\n(n={len(alphas):,} pairs)", fontsize=11,
                     fontweight="semibold")

    fig2.suptitle("Bivariate distribution: Pareto α vs xmin — Inter-post gaps (powerlaw best-fit users)",
                  fontsize=13, fontweight="bold", y=1.02)
    fig2.tight_layout()

    bivar_path = OUTDIR / "pareto_inter_post_bivariate_ecdf.png"
    fig2.savefig(bivar_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig2)
    print(f"  → {bivar_path}", file=sys.stderr)


def set_ggplot_style():
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "#F5F5F5",
        "axes.edgecolor": "#333333",
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": "#D3D3D3",
        "grid.linewidth": 0.5,
        "grid.alpha": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.6,
        "font.family": "sans-serif",
    })


if __name__ == "__main__":
    main()
