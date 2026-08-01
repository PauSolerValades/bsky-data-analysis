"""Top 9 event types after all exclusions applied.

Exclusions: updates, deletes, fossil collections, users with <2 events/day.
These are all enforced by the events table build, so we read directly from it.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env")
from running_locally.local_db import Where, get_connection as local_connect

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,  # ponytail: LaTeX not installed on this machine
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

# ── Config ────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
OUT = HERE / "plots" / "top9_filtered.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

# ── Query ─────────────────────────────────────────────────────────────────

WHERE = Where.from_env()
TBL = "pau_db." if WHERE == Where.SERVER else ""

conn = local_connect(WHERE, repo_root=str(REPO))
rows = conn.query(f"""
    SELECT event_type, COUNT(*) AS cnt
    FROM {TBL}events
    GROUP BY event_type
    ORDER BY cnt DESC
""")
conn.close()

total = sum(r[1] for r in rows)
events = [(r[0], r[1], 100 * r[1] / total) for r in rows]

# Top 9 only
plot_events = events[:9]
labels = [e[0] for e in plot_events]
pcts = [e[2] for e in plot_events]

# ── Plot ──────────────────────────────────────────────────────────────────

fig, ax = plt.subplots(figsize=(10, 6))

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
    "Event type distribution — top 9\n(create-only, no fossils, ≥2 events/day)",
    fontsize=12,
    fontweight="bold",
)
ax.set_xlim(0, max(pcts) * 1.15)
ax.invert_yaxis()

fig.tight_layout()
fig.savefig(OUT, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"Saved → {OUT}")
