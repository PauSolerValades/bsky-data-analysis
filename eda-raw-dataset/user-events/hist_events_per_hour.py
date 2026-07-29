"""Log-log histogram — events per active hour per user."""

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

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

OUT = Path(__file__).resolve().parent / "plots"
OUT.mkdir(exist_ok=True)

# ── Fetch ─────────────────────────────────────────────────────────────────

conn = local_connect(Where.from_env(), repo_root=str(REPO))
rows = conn.query("""
    SELECT total, hours
    FROM (
        SELECT did, COUNT(*) AS total,
               COUNT(DISTINCT DATE_TRUNC('hour', TO_TIMESTAMP(time_us / 1000000))) AS hours
        FROM events GROUP BY did
    )
""")
conn.close()

data = np.array([float(t) / max(int(h), 1) for t, h in rows])
data = data[data > 0]

# ── Plot ──────────────────────────────────────────────────────────────────

fig, ax = plt.subplots(figsize=(8, 5))
palette = sns.color_palette("colorblind")

lo, hi = np.log10(data.min()), np.log10(data.max())
bins = np.logspace(lo, hi, 80)
ax.hist(data, bins=bins, color=palette[0], alpha=0.7,
        edgecolor="white", linewidth=0.2)
ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlabel("Events per active hour")
ax.set_ylabel("Users")
ax.set_title("Events per active hour", fontsize=12, fontweight="bold")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

text = f"n = {len(data):,}\nmedian = {np.median(data):,.1f}\nmean = {data.mean():,.1f}"
ax.text(0.95, 0.95, text, transform=ax.transAxes, ha="right", va="top",
        fontsize=9, bbox=dict(boxstyle="round,pad=0.3",
                              facecolor="white", alpha=0.85))

fig.tight_layout()
path = OUT / "hist_events_per_hour.png"
fig.savefig(path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"Saved → {path}")
