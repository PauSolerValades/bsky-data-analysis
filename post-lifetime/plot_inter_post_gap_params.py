#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pandas",
#     "numpy",
#     "matplotlib",
# ]
# ///
"""
Parameter distribution plots for inter-post gap distribution fitting.

Reads results/inter_post_gap_fits.csv and generates:
  1. Best-distribution bar chart (global vs within-session)
  2. Power-law alpha histogram (filtered, per gap_type)
  3. Power-law xmin histogram
  4. Lognormal meanlog/sdlog scatter
  5. Weibull shape histogram (with k=1 reference line)
  6. Gap size comparison boxplots by best-fit distribution

Usage:
    uv run post-lifetime/plot_inter_post_gap_params.py
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
FITS_CSV = RESULTS_DIR / "inter_post_gap_fits.csv"
PLOTS_DIR = Path(__file__).resolve().parent / "results"

GAP_TYPE_LABELS = {
    "global": "Global (all posts)",
    "within_session": "Within-session",
}

DIST_COLORS = {
    "powerlaw":     "#e0245e",
    "lognormal":    "#1d9bf0",
    "weibull":      "#17bf63",
    "gamma":        "#f5a623",
    "exponential":  "#794bc4",
}

DIST_ORDER = ["powerlaw", "lognormal", "weibull", "gamma", "exponential"]


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_data() -> pd.DataFrame:
    if not FITS_CSV.exists():
        print(f"ERROR: {FITS_CSV} not found. Run inter_post_gap_fit.R first.",
              file=sys.stderr)
        sys.exit(1)

    print(f"Loading {FITS_CSV} ...", file=sys.stderr)
    df = pd.read_csv(FITS_CSV)
    print(f"  → {len(df):,} rows, {len(df.columns)} columns", file=sys.stderr)

    # Filter to users with a valid best-fit
    df = df[df["gap_best"].notna() & (df["gap_best"] != "NA")]
    print(f"  → {len(df):,} with valid best-fit distribution", file=sys.stderr)

    # Map gap_type labels
    df["gap_type_label"] = df["gap_type"].map(GAP_TYPE_LABELS)
    return df


# ---------------------------------------------------------------------------
# Plot 1: Best-distribution bar chart
# ---------------------------------------------------------------------------

def plot_best_distribution(df: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=True)

    for ax, (gt, label) in zip(axes, GAP_TYPE_LABELS.items()):
        sub = df[df["gap_type"] == gt]
        counts = sub["gap_best"].value_counts()
        # Reorder by our canonical order
        ordered = {d: counts.get(d, 0) for d in DIST_ORDER}
        labels = list(ordered.keys())
        values = list(ordered.values())
        colors = [DIST_COLORS[d] for d in labels]
        pcts = [100 * v / sum(values) for v in values]

        bars = ax.bar(labels, values, color=colors, edgecolor="white", linewidth=0.5)
        ax.set_title(f"{label}\n(n={len(sub):,} users)", fontsize=13, fontweight="bold")
        ax.set_ylabel("Number of users")
        ax.set_xlabel("Best-fit distribution")
        ax.grid(axis="y", alpha=0.3)

        # Annotate with %
        for bar, pct in zip(bars, pcts):
            if pct > 0.5:
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + max(values) * 0.01,
                        f"{pct:.1f}%", ha="center", va="bottom",
                        fontsize=9, fontweight="bold", color=bar.get_facecolor())

    fig.suptitle("Best-Fit Distribution for Inter-Post Gaps", fontsize=15, fontweight="bold", y=1.02)
    fig.tight_layout()

    path = PLOTS_DIR / "inter_post_gap_best_dist.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ Best-distribution bar chart → {path}")


# ---------------------------------------------------------------------------
# Plot 2: Power-law alpha histogram (per gap_type, filtered)
# ---------------------------------------------------------------------------

def plot_powerlaw_alpha(df: pd.DataFrame):
    pl = df[df["gap_best"] == "powerlaw"].copy()
    # Filter extreme outliers: keep α between sensible bounds
    pl["alpha"] = pl["gap_pl_alpha"].astype(float)
    # Remove NaN and extreme values (keep 1-10 for visualization)
    pl_filt = pl[(pl["alpha"] >= 1.0) & (pl["alpha"] <= 10.0) & pl["alpha"].notna()]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    for ax, (gt, label) in zip(axes, GAP_TYPE_LABELS.items()):
        sub = pl_filt[pl_filt["gap_type"] == gt]
        if len(sub) == 0:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(f"{label}")
            continue

        ax.hist(sub["alpha"], bins=50, color=DIST_COLORS["powerlaw"],
                edgecolor="white", linewidth=0.3, alpha=0.85)
        ax.axvline(sub["alpha"].median(), color="black", linestyle="--",
                   linewidth=1.5, label=f"median α = {sub['alpha'].median():.2f}")
        ax.axvline(2.0, color="gray", linestyle=":", linewidth=1,
                   label="α = 2 (∞ variance)")
        ax.set_title(f"{label}\n(n={len(sub):,} power-law users, α ∈ [1, 10])")
        ax.set_xlabel("Power-law exponent α")
        ax.set_ylabel("Number of users")
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(alpha=0.2)

    fig.suptitle("Power-Law Exponent (α) Distribution", fontsize=14, fontweight="bold")
    fig.tight_layout()

    path = PLOTS_DIR / "inter_post_gap_alpha_hist.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ Alpha histogram → {path}")


# ---------------------------------------------------------------------------
# Plot 3: Power-law xmin histogram
# ---------------------------------------------------------------------------

def plot_powerlaw_xmin(df: pd.DataFrame):
    pl = df[df["gap_best"] == "powerlaw"].copy()
    pl["xmin_h"] = pl["gap_pl_xmin_h"].astype(float)
    # Filter reasonable range
    pl_filt = pl[(pl["xmin_h"] >= 0.001) & (pl["xmin_h"] <= 24) & pl["xmin_h"].notna()]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    for ax, (gt, label) in zip(axes, GAP_TYPE_LABELS.items()):
        sub = pl_filt[pl_filt["gap_type"] == gt]
        if len(sub) == 0:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(f"{label}")
            continue

        ax.hist(sub["xmin_h"], bins=50, color=DIST_COLORS["powerlaw"],
                edgecolor="white", linewidth=0.3, alpha=0.85)
        ax.axvline(sub["xmin_h"].median(), color="black", linestyle="--",
                   linewidth=1.5, label=f"median = {sub['xmin_h'].median():.2f} h")
        ax.set_title(f"{label}\n(n={len(sub):,})")
        ax.set_xlabel("xmin (hours)")
        ax.set_ylabel("Number of users")
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(alpha=0.2)

    fig.suptitle("Power-Law xmin Distribution (hours)", fontsize=14, fontweight="bold")
    fig.tight_layout()

    path = PLOTS_DIR / "inter_post_gap_xmin_hist.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ xmin histogram → {path}")


# ---------------------------------------------------------------------------
# Plot 4: Lognormal meanlog vs sdlog scatter
# ---------------------------------------------------------------------------

def plot_lognormal_params(df: pd.DataFrame):
    ln = df[df["gap_best"] == "lognormal"].copy()
    ln["meanlog"] = ln["gap_lognormal_meanlog"].astype(float)
    ln["sdlog"] = ln["gap_lognormal_sdlog"].astype(float)
    ln = ln[ln["meanlog"].notna() & ln["sdlog"].notna()]
    # Filter outliers
    ln = ln[(ln["sdlog"] > 0) & (ln["sdlog"] < 10)]

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    for ax, (gt, label) in zip(axes, GAP_TYPE_LABELS.items()):
        sub = ln[ln["gap_type"] == gt]
        if len(sub) == 0:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(f"{label}")
            continue

        # Sample if too many for scatter
        plot_data = sub if len(sub) <= 5000 else sub.sample(5000, random_state=42)
        hb = ax.hexbin(plot_data["meanlog"], plot_data["sdlog"],
                       gridsize=40, cmap="YlOrRd", bins="log", mincnt=1)
        ax.set_title(f"{label}\n(n={len(sub):,} users)")
        ax.set_xlabel("meanlog μ")
        ax.set_ylabel("sdlog σ")
        plt.colorbar(hb, ax=ax, label="log₁₀(count)")

    fig.suptitle("Lognormal Parameters: meanlog vs sdlog", fontsize=14, fontweight="bold")
    fig.tight_layout()

    path = PLOTS_DIR / "inter_post_gap_lognormal_scatter.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ Lognormal scatter → {path}")


# ---------------------------------------------------------------------------
# Plot 5: Weibull shape histogram
# ---------------------------------------------------------------------------

def plot_weibull_shape(df: pd.DataFrame):
    wb = df[df["gap_best"] == "weibull"].copy()
    wb["shape"] = wb["gap_weibull_shape"].astype(float)
    # Filter extreme outliers
    wb_filt = wb[(wb["shape"] >= 0.01) & (wb["shape"] <= 5.0) & wb["shape"].notna()]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    for ax, (gt, label) in zip(axes, GAP_TYPE_LABELS.items()):
        sub = wb_filt[wb_filt["gap_type"] == gt]
        if len(sub) == 0:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(f"{label}")
            continue

        bins = np.logspace(np.log10(0.01), np.log10(5), 40)
        ax.hist(sub["shape"], bins=bins, color=DIST_COLORS["weibull"],
                edgecolor="white", linewidth=0.3, alpha=0.85)
        ax.axvline(1.0, color="black", linestyle=":", linewidth=1.5,
                   label="k = 1 (constant hazard)")
        ax.axvline(sub["shape"].median(), color="darkgreen", linestyle="--",
                   linewidth=1.5, label=f"median k = {sub['shape'].median():.2f}")
        p_lt1 = 100 * (sub["shape"] < 1).mean()
        ax.set_title(f"{label}\n(n={len(sub):,}, {p_lt1:.0f}% have k < 1)")
        ax.set_xscale("log")
        ax.set_xlabel("Weibull shape k")
        ax.set_ylabel("Number of users")
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(alpha=0.2)

    fig.suptitle("Weibull Shape Parameter (k) Distribution", fontsize=14, fontweight="bold")
    fig.tight_layout()

    path = PLOTS_DIR / "inter_post_gap_weibull_shape.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ Weibull shape histogram → {path}")


# ---------------------------------------------------------------------------
# Plot 6: Parameter summary for top distributions
# ---------------------------------------------------------------------------

def plot_param_summary(df: pd.DataFrame):
    """Grid of parameter distributions for power-law, lognormal, Weibull."""
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))

    # --- Power-law alpha ---
    ax = axes[0, 0]
    pl = df[df["gap_best"] == "powerlaw"].copy()
    pl["alpha"] = pd.to_numeric(pl["gap_pl_alpha"], errors="coerce")
    for gt, label, ls in [
        ("global", "Global", "-"),
        ("within_session", "Within-session", "--")
    ]:
        sub = pl[(pl["gap_type"] == gt) & (pl["alpha"] >= 0.5) & (pl["alpha"] <= 10)]
        if len(sub) > 0:
            ax.hist(sub["alpha"], bins=60, alpha=0.5, label=f"{label} (n={len(sub):,}, med={sub['alpha'].median():.2f})",
                    color=plt.cm.tab10(0) if gt == "global" else plt.cm.tab10(1),
                    edgecolor="white", linewidth=0.2)
    ax.axvline(2, color="gray", linestyle=":", linewidth=1)
    ax.set_title("Power-law α (1–10)")
    ax.set_xlabel("α")
    ax.legend(fontsize=7, loc="upper right")

    # --- Power-law xmin ---
    ax = axes[0, 1]
    for gt, label, ls in [("global", "Global", "-"), ("within_session", "Within-session", "--")]:
        sub = pl[(pl["gap_type"] == gt) & (pl["gap_pl_xmin_h"] >= 0.001) & (pl["gap_pl_xmin_h"] <= 24)]
        if len(sub) > 0:
            ax.hist(sub["gap_pl_xmin_h"], bins=60, alpha=0.5,
                    label=f"{label} (med={sub['gap_pl_xmin_h'].median():.2f}h)",
                    color=plt.cm.tab10(0) if gt == "global" else plt.cm.tab10(1),
                    edgecolor="white", linewidth=0.2)
    ax.set_title("Power-law xmin (hours)")
    ax.set_xlabel("xmin (h)")
    ax.legend(fontsize=7, loc="upper right")

    # --- Lognormal meanlog ---
    ax = axes[0, 2]
    ln = df[df["gap_best"] == "lognormal"].copy()
    ln["meanlog"] = pd.to_numeric(ln["gap_lognormal_meanlog"], errors="coerce")
    for gt, label in [("global", "Global"), ("within_session", "Within-session")]:
        sub = ln[(ln["gap_type"] == gt) & ln["meanlog"].notna()]
        if len(sub) > 0:
            ax.hist(sub["meanlog"], bins=50, alpha=0.5,
                    label=f"{label} (med={sub['meanlog'].median():.1f})",
                    color=plt.cm.tab10(0) if gt == "global" else plt.cm.tab10(1),
                    edgecolor="white", linewidth=0.2)
    ax.set_title("Lognormal meanlog μ")
    ax.set_xlabel("μ")
    ax.legend(fontsize=7, loc="upper right")

    # --- Lognormal sdlog ---
    ax = axes[1, 0]
    ln["sdlog"] = pd.to_numeric(ln["gap_lognormal_sdlog"], errors="coerce")
    for gt, label in [("global", "Global"), ("within_session", "Within-session")]:
        sub = ln[(ln["gap_type"] == gt) & (ln["sdlog"] > 0) & (ln["sdlog"] < 10)]
        if len(sub) > 0:
            ax.hist(sub["sdlog"], bins=50, alpha=0.5,
                    label=f"{label} (med={sub['sdlog'].median():.2f})",
                    color=plt.cm.tab10(0) if gt == "global" else plt.cm.tab10(1),
                    edgecolor="white", linewidth=0.2)
    ax.set_title("Lognormal sdlog σ")
    ax.set_xlabel("σ")
    ax.legend(fontsize=7, loc="upper right")

    # --- Weibull shape ---
    ax = axes[1, 1]
    wb = df[df["gap_best"] == "weibull"].copy()
    wb["shape"] = pd.to_numeric(wb["gap_weibull_shape"], errors="coerce")
    for gt, label in [("global", "Global"), ("within_session", "Within-session")]:
        sub = wb[(wb["gap_type"] == gt) & (wb["shape"] >= 0.01) & (wb["shape"] <= 5)]
        if len(sub) > 0:
            ax.hist(sub["shape"], bins=50, alpha=0.5,
                    label=f"{label} (med={sub['shape'].median():.3f})",
                    color=plt.cm.tab10(0) if gt == "global" else plt.cm.tab10(1),
                    edgecolor="white", linewidth=0.2)
    ax.axvline(1, color="gray", linestyle=":", linewidth=1)
    ax.set_title("Weibull shape k")
    ax.set_xlabel("k")
    ax.legend(fontsize=7, loc="upper right")

    # --- Weibull scale ---
    ax = axes[1, 2]
    wb["scale_h"] = pd.to_numeric(wb["gap_weibull_scale"], errors="coerce") / 3600
    for gt, label in [("global", "Global"), ("within_session", "Within-session")]:
        sub = wb[(wb["gap_type"] == gt) & (wb["scale_h"] > 0) & (wb["scale_h"] < 48)]
        if len(sub) > 0:
            ax.hist(sub["scale_h"], bins=50, alpha=0.5,
                    label=f"{label} (med={sub['scale_h'].median():.1f}h)",
                    color=plt.cm.tab10(0) if gt == "global" else plt.cm.tab10(1),
                    edgecolor="white", linewidth=0.2)
    ax.set_title("Weibull scale λ (hours)")
    ax.set_xlabel("λ (h)")
    ax.legend(fontsize=7, loc="upper right")

    fig.suptitle("Distribution Parameter Summary — Inter-Post Gaps", fontsize=15, fontweight="bold")
    fig.tight_layout()

    path = PLOTS_DIR / "inter_post_gap_param_summary.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ Parameter summary → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60, file=sys.stderr)
    print("  Inter-Post Gap — Parameter Distribution Plots", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    df = load_data()

    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    plot_best_distribution(df)
    plot_powerlaw_alpha(df)
    plot_powerlaw_xmin(df)
    plot_lognormal_params(df)
    plot_weibull_shape(df)
    plot_param_summary(df)

    print(f"\nAll plots saved to {PLOTS_DIR.resolve()}/", file=sys.stderr)
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
