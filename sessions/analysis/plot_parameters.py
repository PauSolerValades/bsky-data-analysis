#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "polars",
#     "matplotlib",
#     "numpy",
#     "scipy",
# ]
# ///
"""
Parameter density plots for session distribution fitting results.

Reads distribution_fit_results.csv (from session_distribution_fit.R) and produces:
  1. CSV exports of per-user parameters for all five distributions (powerlaw,
     exponential, weibull, lognormal, gamma) — one file for durations, one for
     inter-session gaps.  These feed the simulation user generator.
  2. Density plots of parameter distributions (ggplot style via matplotlib).

Usage:
    uv run session-analysis/plot_parameter_densities.py
    uv run session-analysis/plot_parameter_densities.py --input results/distribution_fit_results.csv
    uv run session-analysis/plot_parameter_densities.py --output-dir results/params --skip-plots
"""

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import polars as pl
from scipy.stats import gaussian_kde


# ── ggplot-like style ──────────────────────────────────────────────────────

def set_ggplot_style():
    plt.style.use("ggplot")
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "#F5F5F5",
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
    })


# ── Parameter definitions ──────────────────────────────────────────────────

# (column_suffix, display_name, log_scale, clip_upper_pct)
# clip_upper_pct: percentile at which to clip extreme outliers for visualisation.
# Set to None for no clipping.  Weibull shape/scale have enormous outliers
# (k up to 468K, scale up to 256K) that squash the KDE flat.
PARAMS = {
    "powerlaw": [
        ("pl_alpha", "α (exponent)", True, None),
        ("pl_xmin", "x_min (s)", True, None),
    ],
    "exponential": [
        ("exponential_rate", "λ (rate, 1/s)", True, None),
    ],
    "weibull": [
        ("weibull_shape", "k (shape)", False, 99.5),
        ("weibull_scale", "λ (scale, s)", True, 99.5),
    ],
    "lognormal": [
        ("lognormal_meanlog", "μ (meanlog)", False, None),
        ("lognormal_sdlog", "σ (sdlog)", False, None),
    ],
    "gamma": [
        ("gamma_shape", "k (shape)", True, None),
        ("gamma_rate", "θ (rate)", True, None),
    ],
}

ALL_DIST_NAMES = list(PARAMS.keys())

# ggplot2-like qualitative palette
COLOR_PL  = "#F8766D"   # powerlaw  – red
COLOR_EXP = "#7CAE00"   # exponential – green
COLOR_W   = "#619CFF"   # weibull     – blue
COLOR_LN  = "#C77CFF"   # lognormal   – purple
COLOR_GA  = "#E76BF3"   # gamma       – pink

DIST_COLORS = {
    "powerlaw":    COLOR_PL,
    "exponential": COLOR_EXP,
    "weibull":     COLOR_W,
    "lognormal":   COLOR_LN,
    "gamma":       COLOR_GA,
}


# ── CSV export ─────────────────────────────────────────────────────────────

def export_params_csv(df: pl.DataFrame, prefix: str, output_path: Path):
    """Export clean CSV: did, best_fit, then all distribution parameters."""
    best_col = f"{prefix}_best"

    param_cols = []
    for dist_name in ALL_DIST_NAMES:
        for suffix, _, _, _ in PARAMS[dist_name]:
            col = f"{prefix}_{suffix}"
            if col in df.columns:
                param_cols.append(col)

    out_cols = ["did", best_col] + param_cols
    sub = df.select([c for c in out_cols if c in df.columns])

    # Only users whose best fit is one of the five
    sub = sub.filter(pl.col(best_col).is_in(ALL_DIST_NAMES))

    sub.write_csv(output_path)
    n_best = (sub.group_by(best_col).len().sort("len", descending=True)
              if not sub.is_empty() else None)
    print(f"  → {output_path}  ({sub.shape[0]:,} users, {sub.shape[1]} cols)",
          file=sys.stderr)
    if n_best is not None:
        parts = [f"{r[best_col]}={r['len']:,}" for r in n_best.iter_rows(named=True)]
        print(f"     best-fit breakdown: {', '.join(parts)}", file=sys.stderr)


# ── Density plotting ───────────────────────────────────────────────────────

def density_plot(values: np.ndarray, log_scale: bool, color: str, ax: plt.Axes,
                clip_upper_pct: float | None = None):
    """KDE density with median marker and rug ticks.

    clip_upper_pct: if set, clip values above this percentile before plotting.
        This is useful for heavy-tailed parameter distributions (e.g. Weibull
        shape/scale) where a few extreme outliers squash the KDE flat.
    """
    # Cast to float, drop NaN/Inf
    v = values.astype(float, copy=False)
    v = v[np.isfinite(v)]
    if len(v) < 10:
        ax.text(0.5, 0.5, f"n={len(v)} (too few)", transform=ax.transAxes,
                ha="center", va="center", fontsize=9, color="#999999")
        return

    n_full = len(v)

    # Optionally clip extreme outliers for visualisation
    if clip_upper_pct is not None:
        clip_val = np.percentile(v, clip_upper_pct)
        orig_len = len(v)
        v = v[v <= clip_val]
        n_trimmed = orig_len - len(v)

    if log_scale and (v > 0).all():
        v_plot = np.log10(v)
    else:
        v_plot = v

    try:
        kde = gaussian_kde(v_plot)
        x = np.linspace(v_plot.min(), v_plot.max(), 300)
        y = kde(x)
        ax.fill_between(x, 0, y, alpha=0.22, color=color)
        ax.plot(x, y, color=color, linewidth=1.2)
    except Exception:
        ax.hist(v_plot, bins=60, density=True, alpha=0.35, color=color, edgecolor="none")

    # rug
    rng = np.random.default_rng(42)
    rug = rng.choice(v_plot, size=min(500, len(v_plot)), replace=False)
    if rug.size > 0:
        ylim = ax.get_ylim()
        ax.plot(rug, np.full_like(rug, -0.02 * ylim[1]), "|",
                color=color, alpha=0.12, markersize=4)

    # median line on clipped data (visible range), label on full data
    med_plot = np.median(v_plot)
    ax.axvline(med_plot, color=color, linestyle="--", linewidth=0.8, alpha=0.6)

    # median label from full (unclipped) data
    med_full = np.median(values[np.isfinite(values)])
    if log_scale and (values[np.isfinite(values)] > 0).any():
        try:
            med_label = "%.3g" % float(med_full)
        except (OverflowError, ValueError):
            med_label = "%.3g" % float(med_full)
    else:
        med_label = "%.3g" % float(med_full)

    note_parts = [f"n={n_full:,}", f"med={med_label}"]
    if clip_upper_pct is not None and n_trimmed > 0:
        note_parts.append(f"(clipped {n_trimmed:,}>{clip_val:.3g})")
    ax.text(0.98, 0.95, "\n".join(note_parts),
            transform=ax.transAxes, ha="right", va="top",
            fontsize=7, color="#444444",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                       alpha=0.8, edgecolor="none"))

    if log_scale:
        ax.xaxis.set_major_formatter(
            mticker.FuncFormatter(lambda val, _: f"$10^{{{int(val)}}}$"))


def make_figure(df: pl.DataFrame, prefix: str, dist_name: str, output_path: Path):
    """One figure per distribution, one subplot per parameter."""
    params = PARAMS[dist_name]
    n = len(params)
    color = DIST_COLORS[dist_name]

    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 3.8))
    if n == 1:
        axes = [axes]

    for i, (suffix, label, log_scale, clip_upper_pct) in enumerate(params):
        col = f"{prefix}_{suffix}"
        if col not in df.columns:
            axes[i].text(0.5, 0.5, "—", transform=axes[i].transAxes,
                         ha="center", va="center")
            continue
        vals = df[col].to_numpy()
        density_plot(vals, log_scale, color, axes[i], clip_upper_pct=clip_upper_pct)
        axes[i].set_xlabel(label, fontsize=10)
        axes[i].set_ylabel("Density", fontsize=9)

    quantity = "Session duration" if prefix == "dur" else "Inter-session gap"
    fig.suptitle(f"{dist_name}  —  {quantity}",
                 fontsize=13, fontweight="semibold", y=1.02)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  → {output_path}", file=sys.stderr)


# ── Summary figure ─────────────────────────────────────────────────────────
# One compact figure: five distributions × 2 quantities, arranged as rows

SUMMARIES = [
    # (col_suffix, xlabel, log_scale, color, clip_upper_pct)
    ("pl_alpha",           "α (power-law exponent)", True,  COLOR_PL,  None),
    ("pl_xmin",            "x_min (seconds)",         True,  COLOR_PL,  None),
    ("exponential_rate",   "λ (exponential rate)",    True,  COLOR_EXP, None),
    ("weibull_shape",      "k (Weibull shape)",       False, COLOR_W,   99.5),
    ("weibull_scale",      "λ (Weibull scale, s)",    True,  COLOR_W,   99.5),
    ("lognormal_meanlog",  "μ (lognormal meanlog)",   False, COLOR_LN,  None),
    ("lognormal_sdlog",    "σ (lognormal sdlog)",     False, COLOR_LN,  None),
    ("gamma_shape",        "k (gamma shape)",         True,  COLOR_GA,  None),
    ("gamma_rate",         "θ (gamma rate)",          True,  COLOR_GA,  None),
]


def make_summary_figure(df: pl.DataFrame, output_dir: Path):
    """2-row × N-col figure: top row = duration params, bottom = gap params."""
    n_cols = len(SUMMARIES)
    fig, axes = plt.subplots(2, n_cols, figsize=(n_cols * 2.8, 6.5))

    for r, (prefix, qlabel) in enumerate([("dur", "Session duration"),
                                            ("gap", "Inter-session gap")]):
        for c, (suffix, xlabel, log_scale, color, clip_upper_pct) in enumerate(SUMMARIES):
            ax = axes[r, c]
            col = f"{prefix}_{suffix}"
            if col not in df.columns:
                ax.text(0.5, 0.5, "—", transform=ax.transAxes,
                        ha="center", va="center", fontsize=8)
                continue
            vals = df[col].to_numpy()
            density_plot(vals, log_scale, color, ax, clip_upper_pct=clip_upper_pct)
            ax.set_xlabel(xlabel, fontsize=8)
            ax.set_ylabel("", fontsize=8)
            if c == 0:
                ax.set_ylabel("Density", fontsize=9)
            ax.set_title(qlabel if c < 2 else "", fontsize=8,
                         color="#666666", style="italic")

    fig.suptitle("Parameter distributions for simulation sampling (all 5 families)",
                 fontsize=13, fontweight="semibold", y=1.01)
    fig.tight_layout()
    path = output_dir / "summary_all_params.png"
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  → {path}", file=sys.stderr)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parameter density plots for simulation user generator"
    )
    parser.add_argument("--input", type=str,
                        default="results/distribution_fit_results.csv")
    parser.add_argument("--output-dir", type=str, default="results/params")
    parser.add_argument("--skip-plots", action="store_true",
                        help="Only export CSVs, skip plotting")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        for alt in [
            Path("session-analysis") / args.input,
            Path(__file__).resolve().parent.parent / args.input,
        ]:
            if alt.exists():
                input_path = alt
                break
        if not input_path.exists():
            print(f"ERROR: {args.input} not found", file=sys.stderr)
            sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    set_ggplot_style()

    # ── Load & filter ──────────────────────────────────────────────────────
    print(f"Loading {input_path} ...", file=sys.stderr)
    df = pl.read_csv(
        input_path,
        null_values=["NA", ""],
        infer_schema_length=100000,
        ignore_errors=True,
    )
    # Force all columns except did/best/source_table to float
    str_cols = ["did", "dur_best", "gap_best", "source_table"]
    for c in df.columns:
        if c not in str_cols:
            df = df.with_columns(pl.col(c).cast(pl.Float64, strict=False))

    # ── CSV exports ────────────────────────────────────────────────────────
    print("\n=== CSV exports for simulation user generator ===", file=sys.stderr)

    export_params_csv(df, "dur", output_dir / "params_duration.csv")
    export_params_csv(df, "gap", output_dir / "params_gap.csv")

    if args.skip_plots:
        print("\nDone (--skip-plots).", file=sys.stderr)
        return

    # ── Per-distribution density plots ─────────────────────────────────────
    print("\n=== Per-distribution density plots ===", file=sys.stderr)

    for prefix, qlabel in [("dur", "duration"), ("gap", "gap")]:
        for dist_name in ALL_DIST_NAMES:
            any_col = any(
                f"{prefix}_{suffix}" in df.columns
                for suffix, _, _, _ in PARAMS[dist_name]
            )
            if not any_col:
                continue
            out = output_dir / f"{dist_name}_{qlabel}_params.png"
            make_figure(df, prefix, dist_name, out)

    # ── Summary figure ─────────────────────────────────────────────────────
    print("\n=== Summary figure ===", file=sys.stderr)
    make_summary_figure(df, output_dir)

    print(f"\nDone. All outputs in {output_dir.resolve()}/", file=sys.stderr)


if __name__ == "__main__":
    main()
