"""Power-law fit of reposts-per-post (cascade size).

Reads cascade_size from results/cascades_rows.csv (built by build_cascades):
reposts = cascade_size - 1 (the root node is the post author, not a repost).
Fits a discrete power law (MLE alpha + KS-optimal xmin) via the `powerlaw`
package, compares against lognormal via likelihood ratio, and plots the CCDF
on log-log axes with the fitted tail overlaid.

Usage:
    uv run --project sessions cascade-creation/reposts_powerlaw.py
"""

import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import powerlaw
import seaborn as sns

HERE = Path(__file__).resolve().parent
CSV = HERE / "results" / "cascades_rows.csv"

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    # ponytail: no LaTeX on this machine; AGENTS.md style where available
    "text.usetex": shutil.which("latex") is not None,
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})


def main():
    df = pl.read_csv(CSV, has_header=False, quote_char='"',
                     new_columns=["post_uri", "author_did", "creation_time_us",
                                  "cascade_size", "depth", "max_out_degree", "sv"])
    reposts = df["cascade_size"].to_numpy().astype(float) - 1.0
    reposts = reposts[reposts >= 1]  # posts with >=1 repost
    print(f"posts with >=1 repost: {len(reposts):,} "
          f"(max = {int(reposts.max())})")

    # ── Fit ──────────────────────────────────────────────────────────────
    fit = powerlaw.Fit(reposts, discrete=True, verbose=False)
    print(f"\n== power-law fit ==")
    print(f"  xmin = {fit.xmin}, alpha = {fit.alpha:.3f} +/- {fit.sigma:.3f}")
    print(f"  posts >= xmin: {int((reposts >= fit.xmin).sum()):,} "
          f"({(reposts >= fit.xmin).mean():.2%}), holding "
          f"{reposts[reposts >= fit.xmin].sum() / reposts.sum():.2%} of all reposts")
    R, p = fit.loglikelihood_ratio("power_law", "lognormal", normalized_ratio=True)
    verdict = ("power-law favored" if R > 0 and p < 0.05 else
               "lognormal favored" if R < 0 and p < 0.05 else "undecided")
    print(f"  power-law vs lognormal: R = {R:.2f}, p = {p:.3g} ({verdict})")

    # ── Fit table CSV ────────────────────────────────────────────────────
    tail = reposts[reposts >= fit.xmin]
    rows = [
        ("metric", "value"),
        ("n_posts", f"{len(reposts)}"),
        ("max_reposts", f"{int(reposts.max())}"),
        ("alpha", f"{fit.alpha:.3f}"),
        ("alpha_sigma", f"{fit.sigma:.3f}"),
        ("xmin", f"{fit.xmin:g}"),
        ("tail_posts_pct", f"{len(tail) / len(reposts) * 100:.2f}"),
        ("tail_reposts_share_pct", f"{tail.sum() / reposts.sum() * 100:.2f}"),
        ("llr_R", f"{R:.2f}"),
        ("llr_p", f"{p:.3g}"),
        ("llr_winner", verdict),
    ]
    csv_out = HERE / "results" / "reposts_powerlaw_fit.csv"
    with open(csv_out, "w") as f:
        for k, v in rows:
            f.write(f"{k},{v}\n")
    print(f"  → saved {csv_out}")

    # ── CCDF plot ────────────────────────────────────────────────────────
    x = np.sort(reposts)
    ccdf = 1 - np.arange(len(x)) / len(x)

    xmin, alpha = fit.xmin, fit.alpha
    xfit = np.logspace(np.log10(xmin), np.log10(reposts.max()), 200)
    ccdf_fit = (xfit / xmin) ** (-(alpha - 1))

    palette = sns.color_palette("colorblind")
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.step(x, ccdf, where="post", color=palette[0], linewidth=1.2,
            label="reposts per post (data)")
    ax.plot(xfit, ccdf_fit, color=palette[1], linewidth=2,
            label=rf"power law $\alpha={alpha:.2f}$")
    ax.axvline(xmin, color=palette[2], linestyle=":", linewidth=1.2,
               label=rf"$x_{{min}}={xmin:g}$")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Reposts per post")
    ax.set_ylabel(r"$P(X \geq x)$")
    ax.set_title("CCDF of reposts per post", fontsize=12, fontweight="bold")
    ax.legend()
    fig.tight_layout()

    out = HERE / "results" / "reposts_powerlaw.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  → saved {out}")

    # ── Histogram (log-binned, log-log) ──────────────────────────────────
    fig, ax = plt.subplots(figsize=(7, 5))
    bins = np.logspace(np.log10(reposts.min()), np.log10(reposts.max()), 60)
    ax.hist(reposts, bins=bins, density=True, color=palette[0], alpha=0.7,
            edgecolor="white", linewidth=0.3)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Reposts per post")
    ax.set_ylabel("Density")
    ax.set_title("Histogram of reposts per post", fontsize=12, fontweight="bold")
    fig.tight_layout()
    hist_out = HERE / "results" / "reposts_histogram.png"
    fig.savefig(hist_out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {hist_out}")


if __name__ == "__main__":
    main()
