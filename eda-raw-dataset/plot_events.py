"""Plot event type distribution from the events table.

Horizontal bar chart with each event type shown as a separate bar,
sorted by % of total events descending. The smallest types
(<0.1% each) are dropped — mentioned in text instead.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env")
from running_locally.local_db import Where, get_connection as local_connect

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

# ── Config ────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "event_type_distribution.png"

# ── Query events table ──────────────────────────────────────────────────

conn = local_connect(Where.from_env(), repo_root=str(REPO))
rows = conn.query("""
    SELECT event_type, COUNT(*) AS cnt
    FROM events
    GROUP BY event_type
    ORDER BY cnt DESC
""")
total = sum(r[1] for r in rows)

events = [(r[0], r[1], 100 * r[1] / total) for r in rows]

# Keep top 14, drop the tail (<0.1% each, mentioned in text)
plot_events = events[:14]
labels = [e[0] for e in plot_events]
pcts = [e[2] for e in plot_events]

# ── Plot ──────────────────────────────────────────────────────────────────

fig, ax = plt.subplots(figsize=(10, 7))

palette = sns.color_palette("colorblind", n_colors=len(labels))
bars = ax.barh(labels, pcts, color=palette, edgecolor="white", height=0.7)

for bar, pct in zip(bars, pcts):
    ax.text(
        bar.get_width() + 0.3,
        bar.get_y() + bar.get_height() / 2,
        f"{pct:.1f}%",
        va="center",
        fontsize=8,
    )

ax.set_xlabel("% of all events")
ax.set_title(
    f"Event type distribution — {total:,} total events",
    fontsize=12,
    fontweight="bold",
)
ax.set_xlim(0, max(pcts) * 1.15)
ax.invert_yaxis()

fig.tight_layout()
fig.savefig(OUT, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"Saved → {OUT}")
