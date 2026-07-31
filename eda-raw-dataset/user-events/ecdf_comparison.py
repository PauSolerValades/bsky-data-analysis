"""Overlay ECDFs of events per user, day, and hour with their lognormal CDF fits.

fit_event_distributions.py establishes that all three distributions are
lognormal (power-law vs lognormal LLR test: lognormal wins). This script
overlaps the empirical CDFs with the fitted lognormal CDFs on a single plot
to visualise the quality of fit.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from dotenv import load_dotenv
from matplotlib.lines import Line2D
from scipy import stats

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
    "legend.fontsize": 10,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

OUT = Path(__file__).resolve().parent / "plots"
OUT.mkdir(exist_ok=True)

# ── Fetch ─────────────────────────────────────────────────────────────────

conn = local_connect(Where.from_env(), repo_root=str(REPO))

# Events per user
rows = conn.query(f"""
    SELECT cnt, COUNT(*) AS n_users
    FROM (SELECT did, COUNT(*) AS cnt FROM {TBL}events GROUP BY did) t
    GROUP BY cnt ORDER BY cnt
""")
data_user = np.array([float(v) for v, n in rows for _ in range(int(n))])
data_user = data_user[data_user > 0]

# Events per day
rows = conn.query(f"""
    SELECT total, days
    FROM (
        SELECT did, COUNT(*) AS total,
               COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))) AS days
        FROM {TBL}events GROUP BY did
    ) t
""")
data_day = np.array([float(t) / max(int(d), 1) for t, d in rows])
data_day = data_day[data_day > 0]

# Events per hour
rows = conn.query(f"""
    SELECT total, hours
    FROM (
        SELECT did, COUNT(*) AS total,
               COUNT(DISTINCT DATE_TRUNC('hour', FROM_UNIXTIME(time_us / 1000000))) AS hours
        FROM {TBL}events GROUP BY did
    ) t
""")
data_hour = np.array([float(t) / max(int(h), 1) for t, h in rows])
data_hour = data_hour[data_hour > 0]

conn.close()

datasets = [
    (data_user, "Events per user"),
    (data_day,  "Events per day"),
    (data_hour, "Events per hour"),
]

for data, name in datasets:
    print(f"  {name:<18s} n={len(data):>10,}  median={np.median(data):>8.1f}")

# ── Lognormal fits ───────────────────────────────────────────────────────

print("\n── Lognormal fits ──")
fits = []
for data, name in datasets:
    shape, loc, scale = stats.lognorm.fit(data, floc=0)
    mu, sigma = np.log(scale), shape
    fits.append((mu, sigma, scale))
    print(f"  {name:<18s} μ={mu:.4f}  σ={sigma:.4f}  median={np.exp(mu):.1f}")

# ── Plot ─────────────────────────────────────────────────────────────────

palette = sns.color_palette("colorblind", n_colors=3)
fig, ax = plt.subplots(figsize=(8, 5))

for i, (data, name) in enumerate(datasets):
    mu, sigma, scale = fits[i]

    # ECDF
    sorted_d = np.sort(data)
    y_ecdf = np.arange(1, len(sorted_d) + 1) / len(sorted_d)
    ax.step(sorted_d, y_ecdf, where="post", color=palette[i], linewidth=1.2)

    # Lognormal CDF
    x_fit = np.logspace(np.log10(data.min()), np.log10(data.max()), 200)
    y_cdf = stats.lognorm.cdf(x_fit, sigma, loc=0, scale=scale)
    ax.plot(x_fit, y_cdf, color=palette[i], linestyle="--", linewidth=1.5, alpha=0.7)

ax.set_xscale("log")
ax.set_xlabel("Events")
ax.set_ylabel("P(Events ≤ x)")
ax.set_title("ECDF + lognormal fit — events per user, day, hour",
             fontsize=12, fontweight="bold")

handles = []
for i, (_, name) in enumerate(datasets):
    handles.append(Line2D([0], [0], color=palette[i], linewidth=1.2, label=name))
handles.append(Line2D([0], [0], color="grey", linewidth=1.2, label="— ECDF"))
handles.append(Line2D([0], [0], color="grey", linewidth=1.5, linestyle="--",
                      label="- - - lognormal fit"))
ax.legend(handles=handles, fontsize=9, loc="lower right")

fig.tight_layout()
path = OUT / "ecdf_comparison.png"
fig.savefig(path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"\n  → saved {path}")

# ── TSV: lognormal parameters ────────────────────────────────────────────

tsv_path = OUT / "ecdf_parameters.tsv"
with open(tsv_path, "w") as f:
    f.write("distribution\tmu\tsigma\tmedian\tn\n")
    for (data, name), (mu, sigma, _) in zip(datasets, fits):
        f.write(f"{name}\t{mu:.4f}\t{sigma:.4f}\t{np.exp(mu):.1f}\t{len(data)}\n")
print(f"  → saved {tsv_path}")

print("\nDone.")
