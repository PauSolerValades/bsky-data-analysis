"""ECDF comparison — events per user, per day, per hour.

All three empirical CDFs on one plot (3 colors) with their
lognormal CDF fits overlaid (same 3 colors, dashed).
"""

import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pymysql
import seaborn as sns
from dotenv import load_dotenv
from scipy import stats

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

# ── Config ────────────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO / ".env")

DB = {
    "host": os.environ["DATABASE_HOST"],
    "port": int(os.environ["DATABASE_PORT"]),
    "user": os.environ["DATABASE_USER"],
    "password": os.environ["PAU_PASSWORD"],
    "database": "bsky",
    "charset": "utf8mb4",
}

OUT = Path(__file__).resolve().parent / "plots"
OUT.mkdir(exist_ok=True)

EXCLUDE_COLLECTIONS = (
    "'app.bsky.feed.post'",
    "'app.bsky.graph.repost'",
    "'app.bsky.graph.verification'",
    "'app.bsky.lexicon.collection'",
    "'app.bsky.graph.cancellation'",
    "'app.bsky.draft.createDraft'",
)
EXCLUDE_SQL = " AND collection NOT IN (" + ", ".join(EXCLUDE_COLLECTIONS) + ")"

EVENTS_SQL = f"""
    SELECT did, time_us FROM bsky.records WHERE 1=1{EXCLUDE_SQL}
    UNION ALL
    SELECT did, time_us FROM bsky.posts
"""


# ── Helpers ───────────────────────────────────────────────────────────────

def query(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def fetch_events_per_user(conn):
    rows = query(conn, f"""
        SELECT val, COUNT(*) AS n_users
        FROM (
            SELECT did, COUNT(*) AS val
            FROM ({EVENTS_SQL}) e
            GROUP BY did
        ) per_user
        GROUP BY val
        ORDER BY val
    """)
    data = []
    for v, n in rows:
        data.extend([float(v)] * int(n))
    return np.array(data)


def fetch_events_per_day(conn):
    rows = query(conn, f"""
        SELECT total, days
        FROM (
            SELECT did,
                   COUNT(*) AS total,
                   COUNT(DISTINCT DATE(FROM_UNIXTIME(time_us / 1000000))) AS days
            FROM ({EVENTS_SQL}) e
            GROUP BY did
        ) t
    """)
    return np.array([total / max(days, 1) for total, days in rows])


def fetch_events_per_hour(conn):
    rows = query(conn, f"""
        SELECT total, hours
        FROM (
            SELECT did,
                   COUNT(*) AS total,
                   COUNT(DISTINCT DATE_FORMAT(FROM_UNIXTIME(time_us / 1000000),
                                              '%Y-%m-%d %H')) AS hours
            FROM ({EVENTS_SQL}) e
            GROUP BY did
        ) t
    """)
    return np.array([total / max(hours, 1) for total, hours in rows])


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    print("── Fetching data ──")
    data_user = fetch_events_per_user(conn)
    data_day  = fetch_events_per_day(conn)
    data_hour = fetch_events_per_hour(conn)
    conn.close()

    datasets = [
        (data_user, "Events per user"),
        (data_day,  "Events per day"),
        (data_hour, "Events per hour"),
    ]

    for data, name in datasets:
        print(f"  {name:<18s} n={len(data):>10,}  median={np.median(data):>8.1f}")

    # ── Fit lognormals ─────────────────────────────────────────────────

    print("\n── Lognormal fits ──")
    fits = []
    for data, name in datasets:
        shape, loc, scale = stats.lognorm.fit(data[data > 0], floc=0)
        mu = np.log(scale)
        sigma = shape
        fits.append((mu, sigma, scale))
        print(f"  {name:<18s} μ={mu:.4f}  σ={sigma:.4f}  median={np.exp(mu):.1f}")

    # ── Plot ───────────────────────────────────────────────────────────

    palette = sns.color_palette("colorblind", n_colors=3)
    fig, ax = plt.subplots(figsize=(8, 5))

    for i, (data, name) in enumerate(datasets):
        data_pos = data[data > 0]
        mu, sigma, scale = fits[i]

        # ECDF
        sorted_d = np.sort(data_pos)
        y_ecdf = np.arange(1, len(sorted_d) + 1) / len(sorted_d)
        ax.step(sorted_d, y_ecdf, where="post", color=palette[i], linewidth=1.2)

        # Lognormal CDF
        x_fit = np.logspace(np.log10(data_pos.min()), np.log10(data_pos.max()), 200)
        y_cdf = stats.lognorm.cdf(x_fit, sigma, loc=0, scale=scale)
        ax.plot(x_fit, y_cdf, color=palette[i], linestyle="--", linewidth=1.5,
                alpha=0.7)

    ax.set_xscale("log")
    ax.set_xlabel("Events")
    ax.set_ylabel("P(Events ≤ x)")
    ax.set_title("ECDF + lognormal fit — events per user, day, hour",
                 fontsize=12, fontweight="bold")

    # Manual legend: solid = ECDF, dashed = lognormal fit
    from matplotlib.lines import Line2D
    handles = []
    for i, (_, name) in enumerate(datasets):
        handles.append(Line2D([0], [0], color=palette[i], linewidth=1.2, label=name))
    handles.append(Line2D([0], [0], color="grey", linewidth=1.2, label="— ECDF"))
    handles.append(Line2D([0], [0], color="grey", linewidth=1.5, linestyle="--", label="- - - lognormal fit"))
    ax.legend(handles=handles, fontsize=9, loc="lower right")

    fig.tight_layout()
    path = OUT / "ecdf_comparison.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  → saved {path}")

    # ── Parameter TSV ─────────────────────────────────────────────────

    tsv_path = OUT / "ecdf_parameters.tsv"
    with open(tsv_path, "w") as f:
        f.write("distribution\tmu\tsigma\tmedian\tn\n")
        for (data, name), (mu, sigma, _) in zip(datasets, fits):
            f.write(f"{name}\t{mu:.4f}\t{sigma:.4f}\t"
                    f"{np.exp(mu):.1f}\t{len(data[data>0])}\n")
    print(f"  → saved {tsv_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
