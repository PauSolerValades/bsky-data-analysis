"""Power-law fit on the events-per-user distribution.

Uses the powerlaw package (Alstott et al.) — MLE for α, KS-minimisation for xmin.
Validates whether the distribution actually follows a power law above xmin.
"""

import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import powerlaw
import pymysql
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / ".env")

DB = {
    "host": os.environ["DATABASE_HOST"],
    "port": int(os.environ["DATABASE_PORT"]),
    "user": os.environ["DATABASE_USER"],
    "password": os.environ["PAU_PASSWORD"],
    "database": "bsky",
    "charset": "utf8mb4",
}

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)

# Collections to exclude
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
    """Return flat array of event counts per user."""
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
    for val, n in rows:
        data.extend([val] * int(n))
    arr = np.array(data, dtype=np.float64)
    print(f"  events/user: {len(arr):,} users, {len(rows):,} distinct values")
    return arr


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}\n")

    print("── Fetching events per user ──")
    data = fetch_events_per_user(conn)
    conn.close()

    # ── Power-law fit ──────────────────────────────────────────────────

    print("\n── Power-law fit (powerlaw package) ──")
    fit = powerlaw.Fit(data, discrete=True, xmin=None)

    print(f"  α        = {fit.alpha:.4f}")
    print(f"  xmin     = {fit.xmin:.0f}")
    print(f"  KS       = {fit.D:.4f}")
    print(f"  n (tail) = {len(data[data >= fit.xmin]):,} "
          f"({100 * len(data[data >= fit.xmin]) / len(data):.1f}% of users)")
    print(f"  n (body) = {len(data[data < fit.xmin]):,} "
          f"({100 * len(data[data < fit.xmin]) / len(data):.1f}% of users)")

    # ── Compare alternative distributions ──────────────────────────────

    print("\n── Distribution comparison (log-likelihood ratio) ──")
    R, p = fit.distribution_compare("power_law", "exponential")
    print(f"  power_law vs exponential:    R = {R:.2f}, p = {p:.4f}", end="")
    print("  ✓ power law better" if R > 0 and p < 0.05 else "  ✗ not significant")

    R, p = fit.distribution_compare("power_law", "lognormal_positive")
    print(f"  power_law vs lognormal:      R = {R:.2f}, p = {p:.4f}", end="")
    print("  ✓ power law better" if R > 0 and p < 0.05 else "  — lognormal could also fit")

    R, p = fit.distribution_compare("power_law", "truncated_power_law")
    print(f"  power_law vs truncated PL:   R = {R:.2f}, p = {p:.4f}", end="")
    print("  ✓ power law better" if R > 0 and p < 0.05 else "  — truncated could also fit")

    # ── Plot ───────────────────────────────────────────────────────────

    fig, ax = plt.subplots(figsize=(10, 5.5))

    # CCDF
    fit.plot_ccdf(ax=ax, linewidth=1.5, color="#333333", label="data")

    # Power-law fit line (above xmin)
    xmin = fit.xmin
    tail = data[data >= xmin]
    x = np.sort(tail)
    y = 1 - np.arange(len(tail)) / len(data)
    # Theoretical power-law CCDF: (x/xmin)^(1-α)
    x_theo = np.logspace(np.log10(xmin), np.log10(x.max()), 100)
    y_theo = (xmin / x_theo) ** (fit.alpha - 1) * (len(tail) / len(data))
    ax.plot(x_theo, y_theo, "r--", linewidth=2, label=f"power-law fit  α={fit.alpha:.2f}")

    ax.axvline(xmin, color="red", linestyle=":", alpha=0.5, linewidth=1.2)
    ax.text(xmin * 1.05, 0.5, f"  xmin = {xmin:.0f}", fontsize=9, color="red",
            rotation=90, va="center")

    ax.set_xlabel("Events per user")
    ax.set_ylabel("P(Events ≥ x)")
    ax.set_title("CCDF — Events per user (all sources, excluding fossils)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)

    # Stats box
    text = (
        f"n = {len(data):,}\n"
        f"α = {fit.alpha:.2f}\n"
        f"xmin = {xmin:.0f}\n"
        f"tail = {len(tail):,} users ({100*len(tail)/len(data):.1f}%)\n"
        f"KS = {fit.D:.4f}"
    )
    ax.text(0.95, 0.50, text, transform=ax.transAxes, ha="right", va="center",
            fontsize=9, bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85))

    path = OUT / "user_powerlaw_fit.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  → saved {path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
