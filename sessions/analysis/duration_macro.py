"""Check for macrosessions: heavy-tail of very long sessions.

Usage:
    uv run sessions/analysis/duration_macro.py --table-name sessions_tukey_k1_5
    uv run sessions/analysis/duration_macro.py --table-name sessions_tukey_k1_5 --plot-dir ../hyperparameter/plots/tukey/k1_5
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
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

HOURS = [1, 2, 4, 8]


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Macrosession check.")
    parser.add_argument("--table-name", type=str, required=True)
    parser.add_argument("--plot-dir", type=str, default=str(DEFAULT_OUT),
                        help="Output directory for the plot")
    args = parser.parse_args()
    plot_dir = Path(args.plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    conn = local_connect(Where.from_env(), repo_root=str(REPO))
    rows = conn.query(f"SELECT duration_s FROM {TBL_PREFIX}{args.table_name}")
    conn.close()

    durations = np.array([r[0] for r in rows], dtype=np.float64)
    durations_pos = durations[durations > 0]

    print(f"── Macrosession check: {args.table_name} ──")
    print(f"  Total sessions:    {len(durations):>12,}")
    for h in HOURS:
        n = np.sum(durations > h * 3600)
        print(f"  Duration > {h}h:     {n:>12,}  ({100*n/len(durations):.2f}%)")
    print(f"  P90:  {np.percentile(durations_pos, 90):>12.0f}s  "
          f"({np.percentile(durations_pos, 90)/60:.0f} min)")
    print(f"  P95:  {np.percentile(durations_pos, 95):>12.0f}s  "
          f"({np.percentile(durations_pos, 95)/60:.0f} min)")
    print(f"  P99:  {np.percentile(durations_pos, 99):>12.0f}s  "
          f"({np.percentile(durations_pos, 99)/3600:.1f} h)")
    print(f"  Max:  {durations_pos.max():>12.0f}s  "
          f"({durations_pos.max()/3600:.1f} h)")

    # ── Plot: CCDF ─────────────────────────────────────────────────────

    fig, ax = plt.subplots(figsize=(8, 5))
    palette = sns.color_palette("colorblind")

    sorted_d = np.sort(durations_pos)
    ccdf = 1 - np.arange(1, len(sorted_d) + 1) / len(sorted_d)
    ax.step(sorted_d, ccdf, where="post", color=palette[0], linewidth=1.5)

    for p in [90, 95, 99]:
        v = np.percentile(durations_pos, p)
        ax.axvline(v, color="red", linestyle=":", alpha=0.4, linewidth=0.8)
        ax.text(v * 1.1, 0.02 if p > 50 else 0.5,
                f"P{p}={v/60:.0f}min" if v < 3600 else f"P{p}={v/3600:.1f}h",
                fontsize=8, color="red")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Session duration (seconds)")
    ax.set_ylabel("P(Duration ≥ x)")
    ax.set_title(f"Macrosession check — {args.table_name}", fontsize=12, fontweight="bold")

    fig.tight_layout()
    path = plot_dir / f"duration_macro__{args.table_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


if __name__ == "__main__":
    main()
