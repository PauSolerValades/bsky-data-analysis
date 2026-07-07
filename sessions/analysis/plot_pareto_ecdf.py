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
ECDF plots + CSV of fitted Pareto (power-law) parameters α and xmin.

For users whose best fit is powerlaw, plots the empirical CDF of α and xmin
separately for durations and gaps, across both session tables.

Output:
    results_new/params/pareto_params.csv        — 4 columns: quantity, table, alpha, xmin
    results_new/params/pareto_param_ecdf.png      — univariate ECDFs (α, xmin)
    results_new/params/pareto_bivariate_ecdf.png  — bivariate ECDF (α vs xmin)

Usage:
    uv run sessions/analysis/plot_pareto_ecdf.py
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
INPUT = HERE / "results_new" / "distribution_fit_results.csv"
OUTDIR = HERE / "results_new" / "params"


def ecdf(values: np.ndarray):
    x = np.sort(values)
    y = np.arange(1, len(x) + 1) / len(x)
    return x, y


COLORS = {
    "sessions_all": "#F8766D",
    "sessions_engagement": "#00BFC4",
}
LABELS = {
    "sessions_all": "sessions_all (all events incl. likes)",
    "sessions_engagement": "sessions_engagement (engaged, no likes)",
}

PARAM_INFO = {
    ("dur", "pl_alpha"): ("α — Pareto exponent (duration)", True),
    ("dur", "pl_xmin"):  ("xₘᵢₙ — Pareto threshold, seconds (duration)", True),
    ("gap", "pl_alpha"): ("α — Pareto exponent (gap)", True),
    ("gap", "pl_xmin"):  ("xₘᵢₙ — Pareto threshold, seconds (gap)", True),
}


def main():
    print(f"Loading {INPUT} ...", file=sys.stderr)
    df = pl.read_csv(INPUT, null_values=["NA", ""], infer_schema_length=500000,
                     ignore_errors=True)

    # ── Export CSV: 4 columns with all Pareto parameter observations ──────
    rows = []
    for tbl in ["sessions_all", "sessions_engagement"]:
        sub = df.filter(pl.col("source_table") == tbl)

        dur_pl = sub.filter(pl.col("dur_best") == "powerlaw")
        for row in dur_pl.iter_rows(named=True):
            a = row.get("dur_pl_alpha")
            x = row.get("dur_pl_xmin")
            if a is not None and x is not None and np.isfinite(a) and np.isfinite(x):
                rows.append({"quantity": "duration", "table": tbl, "alpha": float(a), "xmin": float(x)})

        gap_pl = sub.filter(pl.col("gap_best") == "powerlaw")
        for row in gap_pl.iter_rows(named=True):
            a = row.get("gap_pl_alpha")
            x = row.get("gap_pl_xmin")
            if a is not None and x is not None and np.isfinite(a) and np.isfinite(x):
                rows.append({"quantity": "gap", "table": tbl, "alpha": float(a), "xmin": float(x)})

    csv_df = pl.DataFrame(rows)
    csv_path = OUTDIR / "pareto_params.csv"
    csv_df.write_csv(csv_path, float_precision=6)
    print(f"  → {csv_path}  ({csv_df.shape[0]:,} rows, {csv_df.shape[1]} cols)", file=sys.stderr)

    # ── Plot ──────────────────────────────────────────────────────────────
    set_ggplot_style()

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))

    for ax_idx, ((qty, param), (label, log_x)) in enumerate(PARAM_INFO.items()):
        r, c = divmod(ax_idx, 2)
        ax = axes[r][c]

        best_col = f"{qty}_best"
        param_col = f"{qty}_{param}"

        for tbl in ["sessions_all", "sessions_engagement"]:
            sub = df.filter(
                (pl.col("source_table") == tbl) &
                (pl.col(best_col) == "powerlaw")
            )
            vals = sub[param_col].drop_nulls().to_numpy().astype(float)
            vals = vals[np.isfinite(vals)]

            if len(vals) == 0:
                continue

            x, y = ecdf(vals)
            ax.step(x, y, where="post", color=COLORS[tbl], linewidth=1.5,
                    label=f"{LABELS[tbl]}  (n={len(vals):,})")

            med = np.median(vals)
            ax.axvline(med, color=COLORS[tbl], linestyle="--", linewidth=0.8, alpha=0.6)
            med_str = f"{med:.4g}" if med < 1000 else f"{med:,.0f}"
            ax.text(med, 0.03, f"med={med_str}", color=COLORS[tbl], fontsize=7,
                    rotation=90, va="bottom", ha="right")

        if log_x:
            ax.set_xscale("log")

        ax.set_xlabel(label, fontsize=10)
        ax.set_ylabel("ECDF", fontsize=10)
        ax.set_ylim(0, 1.02)
        ax.legend(fontsize=7.5, loc="lower right")
        ax.set_title(f"Pareto {param.split('_')[-1]} — {qty}", fontsize=11,
                     fontweight="semibold")

    fig.suptitle("Empirical CDF of fitted Pareto parameters (powerlaw best-fit users only)",
                 fontsize=13, fontweight="bold", y=1.01)
    fig.tight_layout()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTDIR / "pareto_param_ecdf.png"
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  → {out_path}", file=sys.stderr)

    # ── Bivariate ECDF: α vs xmin ───────────────────────────────────────
    fig2, axes2 = plt.subplots(2, 2, figsize=(14, 12))

    for ax_idx, qty in enumerate(["dur", "gap"]):
        best_col = f"{qty}_best"
        a_col = f"{qty}_pl_alpha"
        x_col = f"{qty}_pl_xmin"

        for col_idx, tbl in enumerate(["sessions_all", "sessions_engagement"]):
            ax = axes2[ax_idx][col_idx]
            sub = df.filter(
                (pl.col("source_table") == tbl) &
                (pl.col(best_col) == "powerlaw")
            )
            alphas = sub[a_col].drop_nulls().to_numpy().astype(float)
            xmins = sub[x_col].drop_nulls().to_numpy().astype(float)
            mask = np.isfinite(alphas) & np.isfinite(xmins) & (xmins > 0)
            alphas = alphas[mask]
            xmins = xmins[mask]

            if len(alphas) == 0:
                continue

            # Sample for hexbin if too many points
            if len(alphas) > 20_000:
                idx = np.random.default_rng(42).choice(len(alphas), 20_000, replace=False)
                a_plot, x_plot = alphas[idx], xmins[idx]
            else:
                a_plot, x_plot = alphas, xmins

            hb = ax.hexbin(np.log10(x_plot), a_plot, gridsize=50, cmap="YlOrRd",
                           bins="log", mincnt=1)
            plt.colorbar(hb, ax=ax, label="log₁₀(count)")

            # Marginal medians
            ax.axhline(np.median(alphas), color="black", linestyle="--", linewidth=0.8, alpha=0.5)
            ax.axvline(np.log10(np.median(xmins)), color="black", linestyle="--", linewidth=0.8, alpha=0.5)

            ax.set_xlabel("log₁₀(xmin / seconds)", fontsize=9)
            ax.set_ylabel("α (Pareto exponent)", fontsize=9)
            qlabel = "Session duration" if qty == "dur" else "Inter-session gap"
            ax.set_title(f"{qlabel} — {LABELS[tbl]}\n(n={len(alphas):,})", fontsize=10,
                         fontweight="semibold")

    fig2.suptitle("Bivariate distribution: Pareto α vs xmin (powerlaw best-fit users)",
                  fontsize=13, fontweight="bold", y=1.01)
    fig2.tight_layout()

    bivar_path = OUTDIR / "pareto_bivariate_ecdf.png"
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
