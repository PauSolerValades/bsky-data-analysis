"""Log-log histogram — events per user."""

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

WHERE = Where.from_env()
TBL = "pau_db." if WHERE == Where.SERVER else ""

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
rows = conn.query(f"""
    SELECT cnt, COUNT(*) AS n_users
    FROM (SELECT did, COUNT(*) AS cnt FROM {TBL}events GROUP BY did) t
    GROUP BY cnt ORDER BY cnt
""")
conn.close()

data = np.array([float(v) for v, n in rows for _ in range(int(n))])
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
ax.set_xlabel("Events per user")
ax.set_ylabel("Users")
ax.set_title("Events per user", fontsize=12, fontweight="bold")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

text = f"n = {len(data):,}\nmedian = {np.median(data):,.1f}\nmean = {data.mean():,.1f}"
ax.text(0.95, 0.95, text, transform=ax.transAxes, ha="right", va="top",
        fontsize=9, bbox=dict(boxstyle="round,pad=0.3",
                              facecolor="white", alpha=0.85))

fig.tight_layout()
path = OUT / "hist_events_per_user.png"
fig.savefig(path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"Saved → {path}")
