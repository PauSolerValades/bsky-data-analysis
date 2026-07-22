"""Plot event type distribution from event_types.tsv.

Horizontal bar chart with each create/update/delete operation
shown as a separate bar (e.g. feed_like_create, feed_like_delete),
sorted by % of total events descending. The 6 smallest types
are dropped — mentioned in text instead.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import polars as pl
import seaborn as sns

# ── Config ────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
TSV = HERE / "plots" / "event_types.tsv"
OUT = HERE / "plots" / "event_type_distribution.png"

# Fossil events excluded from the merged view
FOSSILS = {
    "graph.repost",
    "graph.verification",
    "lexicon.collection",
    "graph.cancellation",
    "draft.createDraft",
}

# ── Load & clean ──────────────────────────────────────────────────────────

df = pl.read_csv(TSV, separator="\t")

# Remove fossils and the raw feed.post rows (posts come from bsky.posts table)
df = df.filter(~pl.col("name").is_in(FOSSILS))
df = df.filter(~pl.col("name").is_in(["feed.post", "feed.post.reply"]))

# Build label: replace dots with underscores, append _type
label_map = {
    "feed.like": "feed_like",
    "feed.repost": "feed_repost",
    "graph.follow": "graph_follow",
    "graph.block": "graph_block",
    "feed.threadgate": "feed_threadgate",
    "actor.profile": "actor_profile",
    "feed.postgate": "feed_postgate",
    "graph.listitem": "graph_listitem",
    "actor.status": "actor_status",
    "labeler.service": "labeler_service",
    "feed.generator": "feed_generator",
    "graph.list": "graph_list",
    "graph.listblock": "graph_listblock",
    "graph.starterpack": "graph_starterpack",
    "notification.declaration": "notification_declaration",
}
df = df.with_columns(pl.col("name").replace_strict(label_map).alias("label"))
df = df.with_columns((pl.col("label") + "_" + pl.col("type")).alias("event"))

# Posts come from a separate table, add them manually
total = df["number"].sum() + 15_282_626 + 12_791_049
df = df.with_columns((100 * pl.col("number") / total).alias("pct"))

post_rows = pl.DataFrame(
    {
        "event": ["post_top", "post_reply"],
        "number": [15_282_626, 12_791_049],
        "pct": [100 * 15_282_626 / total, 100 * 12_791_049 / total],
    }
)
plot_df = pl.concat([df.select(["event", "number", "pct"]), post_rows])
plot_df = plot_df.sort("pct")

# Keep only the top 14 (rest are \<0.1% each, mentioned in text)
plot_df = plot_df.tail(14)

# ── Plot ──────────────────────────────────────────────────────────────────

sns.set_style("whitegrid")
fig, ax = plt.subplots(figsize=(10, 7))

palette = sns.color_palette("Blues_d", n_colors=len(plot_df))
bars = ax.barh(
    plot_df["event"], plot_df["pct"], color=palette, edgecolor="white", height=0.7
)

for bar, pct in zip(bars, plot_df["pct"]):
    ax.text(
        bar.get_width() + 0.3,
        bar.get_y() + bar.get_height() / 2,
        f"{pct:.1f}%",
        va="center",
        fontsize=8,
    )

ax.set_xlabel("% of all events")
ax.set_title(
    "Event type distribution from 11/05–18/05, 240.6M total events",
    fontsize=12,
    fontweight="bold",
)
ax.set_xlim(0, max(plot_df["pct"]) * 1.15)

sns.despine()
fig.tight_layout()
fig.savefig(OUT, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"Saved → {OUT}")
