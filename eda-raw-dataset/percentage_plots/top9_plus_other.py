"""Top 9 event types + 'other' as a single bar (raw dump, no filters).

Reads from raw_events: all operations, all collections, all users.
Horizontal bar chart with the 9 most common event types shown individually,
the remaining types aggregated into an 'other' bar.
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
OUT = HERE / "plots" / "top9_plus_other.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

# ── Query ─────────────────────────────────────────────────────────────────

WHERE = Where.from_env()
TBL = "pau_db." if WHERE == Where.SERVER else ""

conn = local_connect(WHERE, repo_root=str(REPO))
rows = conn.query(f"""
    SELECT event_type, COUNT(*) AS cnt
    FROM {TBL}raw_events
    GROUP BY event_type
    ORDER BY cnt DESC
""")
conn.close()

total = sum(r[1] for r in rows)
events = [(r[0], r[1], 100 * r[1] / total) for r in rows]

top9 = events[:9]
other_cnt = sum(e[1] for e in events[9:])
other_pct = 100 * other_cnt / total

labels = [e[0] for e in top9] + ["other"]
pcts = [e[2] for e in top9] + [other_pct]

# ── Plot ──────────────────────────────────────────────────────────────────

fig, ax = plt.subplots(figsize=(10, 6))

palette = sns.color_palette("colorblind", n_colors=10)
# ponytail: grey for 'other' to visually distinguish from real types
palette[-1] = (0.7, 0.7, 0.7)

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
ax.set_title("Event type distribution (top 9 + other)", fontsize=12, fontweight="bold")
ax.set_xlim(0, max(pcts) * 1.15)
ax.invert_yaxis()

fig.tight_layout()
fig.savefig(OUT, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"Saved → {OUT}")
