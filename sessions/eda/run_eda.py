#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymysql",
#     "numpy",
#     "matplotlib",
#     "scipy",
# ]
# ///
"""
EDA — Session tables comparison (sessions_all vs sessions_engagement).

Compares the two Tukey-clustered session tables side-by-side:
  1. Global summary statistics (percentiles, mean, zero-duration rate)
  2. Duration & gap histograms (log-log, overlaid)
  3. Per-user aggregates (sessions, mean duration, mean gap, threshold)
  4. CCDF plots — log-log complementary CDF for distribution shape
  5. Session composition — actions per session bar chart, type breakdown
  6. Gap vs duration scatter (hexbin)

Usage:
    uv run sessions/eda/run_eda.py
    uv run sessions/eda/run_eda.py --sample 500000
"""

import argparse
import os
import sys
import time as time_mod
from pathlib import Path

# Ensure stdout/stderr are unbuffered when piped
_print_orig = print
def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    _print_orig(*args, **kwargs)

import matplotlib.pyplot as plt
import numpy as np
import pymysql


# ═══════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════

REPO = Path(__file__).resolve().parent.parent.parent
OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)

plt.style.use("ggplot")


def loadenv():
    env_path = REPO / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def env(k, d=""):
    return os.environ.get(k, d)


loadenv()

DB = {
    "host": env("DATABASE_HOST", "10.18.74.14"),
    "port": int(env("DATABASE_PORT", "9030")),
    "user": env("DATABASE_USER", "pau"),
    "password": env("PAU_PASSWORD", ""),
    "database": "bsky",
    "charset": "utf8mb4",
}

TABLES = ["sessions_all", "sessions_engagement"]
LABELS = {
    "sessions_all": "All events (incl. likes)",
    "sessions_engagement": "Engaged events (no likes)",
}
COLORS = {
    "sessions_all": "#4A90D9",
    "sessions_engagement": "#E6842A",
}


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def save(fig, name):
    path = OUT / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {path}")


def pct(arr, q):
    return np.percentile(arr, q)


def loglog_hist(ax, data, color, label, n_bins=60, alpha=0.8):
    """Log-log histogram with log-spaced bins."""
    data = np.asarray(data, dtype=np.float64)
    data = data[data > 0]
    if len(data) == 0:
        return
    lo = max(np.log10(data.min()), -1)
    hi = np.log10(data.max()) + 0.1
    bins = np.logspace(lo, hi, n_bins)
    hist, edges = np.histogram(data, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    nonzero = hist > 0
    ax.loglog(centers[nonzero], hist[nonzero], "-",
              color=color, linewidth=1.3, alpha=alpha, label=label)


def print_pcts(data, label, ps=None):
    if ps is None:
        ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    d = np.asarray(data, dtype=np.float64)
    d = d[~np.isnan(d)]
    print(f"\n  {label} (n={len(d):,}):")
    print(f"    Mean: {np.mean(d):.1f}")
    for pv in ps:
        print(f"    P{pv:>2d}: {pct(d, pv):>12.1f}")


def fetch_col(conn, table, column, sample=0):
    """Fetch a single column, optionally sampled."""
    limit = f" LIMIT {sample}" if sample > 0 else ""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {column} FROM pau_db.{table} {limit}")
        return np.array([r[0] for r in cur], dtype=np.float64)


def fetch_gaps(conn, table, sample=0):
    """Fetch inter-session gaps > 0 (seconds)."""
    limit = f" LIMIT {sample}" if sample > 0 else ""
    sql = f"""
        SELECT (next_session_start - session_end) / 1000000.0
        FROM pau_db.{table}
        WHERE next_session_start IS NOT NULL
        {limit}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        gaps = np.array([r[0] for r in cur], dtype=np.float64)
    return gaps[gaps > 0]  # drop non-positive


def fetch_per_user(conn, table):
    """Per-user: n_sessions, avg duration, avg gap, threshold, fallback."""
    sql = f"""
        SELECT
            did,
            COUNT(*) AS n_sessions,
            AVG(duration_s) AS avg_dur,
            AVG(CASE WHEN next_session_start IS NOT NULL
                THEN (next_session_start - session_end) / 1000000.0 END) AS avg_gap,
            SUM(total_actions) AS total_actions,
            AVG(total_actions) AS avg_actions,
            MIN(user_threshold_s) AS threshold_s,
            MAX(user_threshold_fallback) AS is_fallback
        FROM pau_db.{table}
        GROUP BY did
    """
    print(f"  Querying per-user from {table} ...", file=sys.stderr)
    t0 = time_mod.time()
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    print(f"    → {len(rows):,} users in {time_mod.time() - t0:.0f}s", file=sys.stderr)
    return {
        "n_sessions": np.array([r[1] for r in rows], dtype=np.int64),
        "avg_dur": np.array([r[2] for r in rows], dtype=np.float64),
        "avg_gap": np.array([r[3] for r in rows], dtype=np.float64),
        "total_actions": np.array([r[4] for r in rows], dtype=np.int64),
        "avg_actions": np.array([r[5] for r in rows], dtype=np.float64),
        "threshold_s": np.array([r[6] for r in rows], dtype=np.float64),
        "is_fallback": np.array([r[7] for r in rows], dtype=np.int64),
    }


def fetch_composition(conn, table, sample=0):
    """Per-session action counts + duration."""
    limit = f" LIMIT {sample}" if sample > 0 else ""
    if table == "sessions_all":
        sql = f"""
            SELECT total_actions, likes, reposts, posts_authored, replies,
                   follows, blocks, other_actions, duration_s
            FROM pau_db.sessions_all {limit}
        """
    else:
        sql = f"""
            SELECT total_actions, 0, reposts, posts_authored, replies,
                   follows, blocks, 0, duration_s
            FROM pau_db.sessions_engagement {limit}
        """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return {
        "total": np.array([r[0] for r in rows], dtype=np.int64),
        "likes": np.array([r[1] for r in rows], dtype=np.int64),
        "reposts": np.array([r[2] for r in rows], dtype=np.int64),
        "posts": np.array([r[3] for r in rows], dtype=np.int64),
        "replies": np.array([r[4] for r in rows], dtype=np.int64),
        "follows": np.array([r[5] for r in rows], dtype=np.int64),
        "blocks": np.array([r[6] for r in rows], dtype=np.int64),
        "other": np.array([r[7] for r in rows], dtype=np.int64),
        "duration_s": np.array([r[8] for r in rows], dtype=np.float64),
    }


# ═══════════════════════════════════════════════════════════════════════════
# §1 — Global stats (terminal only)
# ═══════════════════════════════════════════════════════════════════════════

def section1(conn, sample=0):
    print("\n" + "=" * 60)
    print("  §1 — Global summary statistics")
    print("=" * 60)
    results = {}
    for tbl in TABLES:
        print(f"\n  --- {LABELS[tbl]} ---")
        t0 = time_mod.time()
        dur = fetch_col(conn, tbl, "duration_s", sample)
        print(f"    durations ({time_mod.time() - t0:.0f}s)")
        print_pcts(dur, "Duration (s)")
        print(f"    Zero-duration: {(dur==0).sum():,} ({100*(dur==0).sum()/len(dur):.1f}%)")

        t0 = time_mod.time()
        gaps = fetch_gaps(conn, tbl, sample)
        print(f"    gaps ({time_mod.time() - t0:.0f}s)")
        print_pcts(gaps, "Inter-session gap (s)")
        print(f"    Gap P50: {pct(gaps,50)/60:.1f} min  "
              f"P75: {pct(gaps,75)/3600:.1f} h  "
              f"P90: {pct(gaps,90)/3600:.1f} h")
        results[tbl] = {"dur": dur, "gaps": gaps}
    return results


# ═══════════════════════════════════════════════════════════════════════════
# §2 — Log-log histograms (durations + gaps, both tables overlaid)
# ═══════════════════════════════════════════════════════════════════════════

def section2(conn, sample=0):
    print("\n" + "=" * 60)
    print("  §2 — Duration & gap histograms (log-log)")
    print("=" * 60)

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    ax = axes[0]
    for tbl in TABLES:
        dur = fetch_col(conn, tbl, "duration_s", sample)
        loglog_hist(ax, dur, COLORS[tbl], LABELS[tbl])
    ax.set_xlabel("Session duration (seconds)")
    ax.set_ylabel("Number of sessions")
    ax.set_title("§2a — Session duration distribution")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    for tbl in TABLES:
        gaps = fetch_gaps(conn, tbl, sample)
        loglog_hist(ax, gaps, COLORS[tbl], LABELS[tbl])
    ax.set_xlabel("Inter-session gap (seconds)")
    ax.set_ylabel("Number of gaps")
    ax.set_title("§2b — Inter-session gap distribution")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    save(fig, "02_histograms.png")


# ═══════════════════════════════════════════════════════════════════════════
# §3 — Per-user aggregates
# ═══════════════════════════════════════════════════════════════════════════

def section3(conn):
    print("\n" + "=" * 60)
    print("  §3 — Per-user aggregates")
    print("=" * 60)

    pu = {}
    for tbl in TABLES:
        pu[tbl] = fetch_per_user(conn, tbl)

    fig, axes = plt.subplots(2, 3, figsize=(18, 11))
    axs = axes.flatten()

    # A — Sessions per user
    for tbl in TABLES:
        d = pu[tbl]["n_sessions"].astype(np.float64)
        loglog_hist(axs[0], d, COLORS[tbl], LABELS[tbl])
    axs[0].set_xlabel("Sessions per user")
    axs[0].set_ylabel("Number of users")
    axs[0].set_title("§3a — Sessions per user")
    axs[0].legend(fontsize=8)

    # B — Avg duration per user
    for tbl in TABLES:
        d = pu[tbl]["avg_dur"]
        d = d[~np.isnan(d)]; d = d[d > 0]
        loglog_hist(axs[1], d, COLORS[tbl], LABELS[tbl])
    axs[1].set_xlabel("Mean session duration per user (s)")
    axs[1].set_ylabel("Number of users")
    axs[1].set_title("§3b — Per-user mean session duration")
    axs[1].legend(fontsize=8)

    # C — Avg gap per user
    for tbl in TABLES:
        d = pu[tbl]["avg_gap"]
        d = d[~np.isnan(d)]; d = d[d > 0]
        loglog_hist(axs[2], d, COLORS[tbl], LABELS[tbl])
    axs[2].set_xlabel("Mean inter-session gap per user (s)")
    axs[2].set_ylabel("Number of users")
    axs[2].set_title("§3c — Per-user mean inter-session gap")
    axs[2].legend(fontsize=8)

    # D — Avg actions per session per user
    for tbl in TABLES:
        d = pu[tbl]["avg_actions"]
        d = d[~np.isnan(d)]; d = d[d > 0]
        bins = np.logspace(0, np.log10(max(d.max(), 10)), 50)
        axs[3].hist(d, bins=bins, color=COLORS[tbl], alpha=0.6,
                    label=LABELS[tbl], edgecolor="white", linewidth=0.3)
    axs[3].set_xscale("log")
    axs[3].set_yscale("log")
    axs[3].set_xlabel("Mean actions per session")
    axs[3].set_ylabel("Number of users")
    axs[3].set_title("§3d — Per-user mean actions per session")
    axs[3].legend(fontsize=8)

    # E — Total actions per user
    for tbl in TABLES:
        d = pu[tbl]["total_actions"].astype(np.float64)
        loglog_hist(axs[4], d, COLORS[tbl], LABELS[tbl])
    axs[4].set_xlabel("Total actions per user")
    axs[4].set_ylabel("Number of users")
    axs[4].set_title("§3e — Total actions per user")
    axs[4].legend(fontsize=8)

    # F — Threshold distribution
    for tbl in TABLES:
        d = pu[tbl]["threshold_s"]; d = d[d > 0]
        bins = np.logspace(np.log10(d.min()), np.log10(d.max() + 1), 50)
        axs[5].hist(d, bins=bins, color=COLORS[tbl], alpha=0.6,
                    label=LABELS[tbl], edgecolor="white", linewidth=0.3)
    axs[5].set_xscale("log")
    axs[5].set_xlabel("Per-user Tukey threshold (seconds)")
    axs[5].set_ylabel("Number of users")
    axs[5].set_title("§3f — Per-user Tukey threshold distribution")
    axs[5].legend(fontsize=8)

    fig.tight_layout()
    save(fig, "03_per_user.png")

    # Print threshold stats
    for tbl in TABLES:
        th = pu[tbl]["threshold_s"]
        fb = pu[tbl]["is_fallback"]
        print(f"\n  {LABELS[tbl]} thresholds:")
        print(f"    Mean: {np.mean(th):.0f}s ({np.mean(th)/60:.1f} min)  "
              f"Median: {np.median(th):.0f}s ({np.median(th)/60:.1f} min)")
        print(f"    P25: {pct(th,25):.0f}s  P75: {pct(th,75):.0f}s")
        print(f"    Fallback: {fb.sum():,} ({100*fb.sum()/len(fb):.1f}%)")

    return pu


# ═══════════════════════════════════════════════════════════════════════════
# §4 — CCDF (complementary CDF, log-log) — best for distribution shape
# ═══════════════════════════════════════════════════════════════════════════

def section4(conn, sample=0):
    print("\n" + "=" * 60)
    print("  §4 — CCDF plots (log-log)")
    print("=" * 60)

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    ax = axes[0]
    for tbl in TABLES:
        dur = fetch_col(conn, tbl, "duration_s", sample)
        dur = dur[dur > 0]
        s = np.sort(dur)
        ccdf = 1 - np.arange(1, len(s) + 1) / len(s)
        ax.loglog(s, ccdf, linewidth=1.5, alpha=0.8,
                  color=COLORS[tbl], label=LABELS[tbl])
        for pv in [50, 90]:
            ax.axvline(x=pct(dur, pv), color=COLORS[tbl], linestyle=":", alpha=0.25)
    ax.set_xlabel("Session duration (seconds)")
    ax.set_ylabel("P(Duration ≥ x)")
    ax.set_title("§4a — Duration CCDF")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    for tbl in TABLES:
        gaps = fetch_gaps(conn, tbl, sample)
        s = np.sort(gaps)
        ccdf = 1 - np.arange(1, len(s) + 1) / len(s)
        ax.loglog(s, ccdf, linewidth=1.5, alpha=0.8,
                  color=COLORS[tbl], label=LABELS[tbl])
    ax.set_xlabel("Inter-session gap (seconds)")
    ax.set_ylabel("P(Gap ≥ x)")
    ax.set_title("§4b — Inter-session gap CCDF")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    save(fig, "04_ccdf.png")


# ═══════════════════════════════════════════════════════════════════════════
# §5 — Session composition
# ═══════════════════════════════════════════════════════════════════════════

def section5(conn, sample=0):
    print("\n" + "=" * 60)
    print("  §5 — Session composition")
    print("=" * 60)

    comp = {}
    for tbl in TABLES:
        comp[tbl] = fetch_composition(conn, tbl, sample)

    fig, axes = plt.subplots(2, 3, figsize=(18, 11))

    # ---- A: Actions per session (log-log hist) ----
    ax = axes[0, 0]
    for tbl in TABLES:
        loglog_hist(ax, comp[tbl]["total"].astype(np.float64),
                    COLORS[tbl], LABELS[tbl])
    ax.set_xlabel("Actions per session")
    ax.set_ylabel("Number of sessions")
    ax.set_title("§5a — Actions per session")
    ax.legend(fontsize=8)

    # ---- B: Bar chart — actions per session buckets ----
    ax = axes[0, 1]
    buckets = ["1", "2", "3-5", "6-10", "11-20", "21-50", "51+"]
    x = np.arange(len(buckets))
    w = 0.35
    for i, tbl in enumerate(TABLES):
        t = comp[tbl]["total"]
        counts = [
            (t == 1).sum(),
            (t == 2).sum(),
            ((t >= 3) & (t <= 5)).sum(),
            ((t >= 6) & (t <= 10)).sum(),
            ((t >= 11) & (t <= 20)).sum(),
            ((t >= 21) & (t <= 50)).sum(),
            (t >= 51).sum(),
        ]
        fracs = 100 * np.array(counts) / len(t)
        bars = ax.bar(x + i * w - w / 2, fracs, w,
                      color=COLORS[tbl], alpha=0.85, label=LABELS[tbl],
                      edgecolor="white", linewidth=0.5)
        for bar, val in zip(bars, fracs):
            if val > 2:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                        f"{val:.1f}%", ha="center", fontsize=7, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(buckets)
    ax.set_ylabel("% of sessions")
    ax.set_title("§5b — Actions per session breakdown")
    ax.legend(fontsize=8)

    # ---- C: Duration vs actions hexbin (all events) ----
    ax = axes[0, 2]
    c = comp["sessions_all"]
    dur = c["duration_s"]
    tot = c["total"].astype(np.float64)
    mask = (dur > 0) & (tot > 0)
    if mask.sum() > 0:
        n_show = min(mask.sum(), 150_000)
        idx = np.random.choice(np.where(mask)[0], n_show, replace=False)
        hb = ax.hexbin(tot[idx], dur[idx], gridsize=50, cmap="YlOrRd",
                       mincnt=1, bins="log")
        ax.set_xscale("log"); ax.set_yscale("log")
        fig.colorbar(hb, ax=ax, label="sessions")
    ax.set_xlabel("Actions per session")
    ax.set_ylabel("Session duration (s)")
    ax.set_title("§5c — Duration vs actions (all events)")

    # ---- D: Session type composition (all events) ----
    ax = axes[1, 0]
    c = comp["sessions_all"]
    type_counts = {
        "Likes only": ((c["likes"] > 0) & (c["posts"] + c["replies"] + c["reposts"] + c["follows"] + c["blocks"] + c["other"] == 0)).sum(),
        "Mixed with likes": ((c["likes"] > 0) & (c["posts"] + c["replies"] + c["reposts"] + c["follows"] + c["blocks"] + c["other"] > 0)).sum(),
        "Posts/replies": ((c["likes"] == 0) & (c["posts"] + c["replies"] > 0) & (c["reposts"] + c["follows"] + c["blocks"] + c["other"] == 0)).sum(),
        "Reposts only": ((c["likes"] == 0) & (c["reposts"] > 0) & (c["posts"] + c["replies"] + c["follows"] + c["blocks"] + c["other"] == 0)).sum(),
        "Network (follow/block)": ((c["likes"] == 0) & (c["posts"] + c["replies"] + c["reposts"] == 0) & (c["follows"] + c["blocks"] > 0) & (c["other"] == 0)).sum(),
        "Mixed no likes": ((c["likes"] == 0) & (c["posts"] + c["replies"] + c["reposts"] + c["follows"] + c["blocks"] + c["other"] > 0) & ~((c["posts"] + c["replies"] > 0) & (c["reposts"] + c["follows"] + c["blocks"] + c["other"] == 0)) & ~((c["reposts"] > 0) & (c["posts"] + c["replies"] + c["follows"] + c["blocks"] + c["other"] == 0)) & ~((c["follows"] + c["blocks"] > 0) & (c["posts"] + c["replies"] + c["reposts"] + c["other"] == 0))).sum(),
        "Other only": ((c["likes"] + c["posts"] + c["replies"] + c["reposts"] + c["follows"] + c["blocks"] == 0)).sum(),
    }
    # Sort descending
    items = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
    labels = [it[0] for it in items]
    values = [it[1] for it in items]
    fracs = 100 * np.array(values) / sum(values)
    colors_b = ["#4A90D9", "#E6842A", "#50B86C", "#9B59B6", "#E74C3C", "#1ABC9C", "#95A5A6"]
    bars = ax.barh(range(len(labels)), fracs, color=colors_b[:len(labels)],
                   edgecolor="white", linewidth=0.5)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    for i, (lbl, f) in enumerate(zip(labels, fracs)):
        ax.text(f + 0.5, i, f"{f:.1f}%", va="center", fontsize=8, fontweight="bold")
    ax.set_xlabel("% of sessions")
    ax.set_title("§5d — Session composition (all events)")
    ax.invert_yaxis()

    # ---- E: Session type composition (engaged events) ----
    ax = axes[1, 1]
    c = comp["sessions_engagement"]
    e_counts = {
        "Posts/replies only": ((c["posts"] + c["replies"] > 0) & (c["reposts"] + c["follows"] + c["blocks"] == 0)).sum(),
        "Reposts only": ((c["posts"] + c["replies"] == 0) & (c["reposts"] > 0) & (c["follows"] + c["blocks"] == 0)).sum(),
        "Follows/blocks only": ((c["posts"] + c["replies"] + c["reposts"] == 0) & (c["follows"] + c["blocks"] > 0)).sum(),
        "Mixed (≥2 types)": ((c["posts"] + c["replies"] > 0).astype(int) + (c["reposts"] > 0).astype(int) + ((c["follows"] + c["blocks"]) > 0).astype(int) >= 2).sum(),
        "Empty": ((c["posts"] + c["replies"] + c["reposts"] + c["follows"] + c["blocks"]) == 0).sum(),
    }
    items = sorted(e_counts.items(), key=lambda x: x[1], reverse=True)
    e_labels = [it[0] for it in items]
    e_values = [it[1] for it in items]
    e_fracs = 100 * np.array(e_values) / sum(e_values)
    ax.barh(range(len(e_labels)), e_fracs, color=colors_b[:len(e_labels)],
            edgecolor="white", linewidth=0.5)
    ax.set_yticks(range(len(e_labels)))
    ax.set_yticklabels(e_labels, fontsize=8)
    for i, (lbl, f) in enumerate(zip(e_labels, e_fracs)):
        ax.text(f + 0.5, i, f"{f:.1f}%", va="center", fontsize=8, fontweight="bold")
    ax.set_xlabel("% of sessions")
    ax.set_title("§5e — Session composition (engaged events)")
    ax.invert_yaxis()

    # ---- F: Duration buckets (zero-duration awareness) ----
    ax = axes[1, 2]
    d_buckets = ["0s", "(0,1s)", "[1s,5s)", "[5s,60s)", "[1min,5min)", "≥5min"]
    x = np.arange(len(d_buckets))
    w = 0.35
    for i, tbl in enumerate(TABLES):
        d = comp[tbl]["duration_s"]
        vals = [
            100 * (d == 0).sum() / len(d),
            100 * ((d > 0) & (d < 1)).sum() / len(d),
            100 * ((d >= 1) & (d < 5)).sum() / len(d),
            100 * ((d >= 5) & (d < 60)).sum() / len(d),
            100 * ((d >= 60) & (d < 300)).sum() / len(d),
            100 * (d >= 300).sum() / len(d),
        ]
        bars = ax.bar(x + i * w - w / 2, vals, w,
                      color=COLORS[tbl], alpha=0.85, label=LABELS[tbl],
                      edgecolor="white", linewidth=0.5)
        for bar, val in zip(bars, vals):
            if val > 2:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                        f"{val:.1f}%", ha="center", fontsize=7, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(d_buckets, rotation=25, ha="right")
    ax.set_ylabel("% of sessions")
    ax.set_title("§5f — Duration bucket breakdown")
    ax.legend(fontsize=8)

    # Print key numbers
    for tbl in TABLES:
        c = comp[tbl]
        t = c["total"]
        d = c["duration_s"]
        print(f"\n  {LABELS[tbl]}:")
        print(f"    1-action sessions: {(t==1).sum():,} ({100*(t==1).sum()/len(t):.1f}%)")
        print(f"    Zero-duration: {(d==0).sum():,} ({100*(d==0).sum()/len(d):.1f}%)")
        print(f"    Mean actions: {np.mean(t):.1f}  Median: {np.median(t):.0f}")

    fig.tight_layout()
    save(fig, "05_composition.png")
    return comp


# ═══════════════════════════════════════════════════════════════════════════
# §6 — Gap vs Duration
# ═══════════════════════════════════════════════════════════════════════════

def section6(conn, sample=0):
    print("\n" + "=" * 60)
    print("  §6 — Gap vs Duration relationship")
    print("=" * 60)

    limit = f" LIMIT {sample}" if sample > 0 else ""

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    for i, tbl in enumerate(TABLES):
        ax = axes[i]
        sql = f"""
            SELECT duration_s,
                   (next_session_start - session_end) / 1000000.0 AS gap_s
            FROM pau_db.{tbl}
            WHERE next_session_start IS NOT NULL
              AND duration_s > 0
            {limit}
        """
        print(f"  Fetching gap/dur pairs from {tbl} ...", file=sys.stderr)
        t0 = time_mod.time()
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        dur = np.array([r[0] for r in rows], dtype=np.float64)
        gap = np.array([r[1] for r in rows], dtype=np.float64)
        mask = (gap > 0)
        dur, gap = dur[mask], gap[mask]
        print(f"    → {len(dur):,} pairs in {time_mod.time() - t0:.0f}s", file=sys.stderr)

        if len(dur) > 200_000:
            idx = np.random.choice(len(dur), 200_000, replace=False)
            dur, gap = dur[idx], gap[idx]

        hb = ax.hexbin(dur, gap, gridsize=50, cmap="YlOrRd",
                       mincnt=1, bins="log")
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlabel("Session duration (s)")
        ax.set_ylabel("Next inter-session gap (s)")
        ax.set_title(f"§6{'ab'[i]} — Gap vs Duration ({LABELS[tbl]})")
        fig.colorbar(hb, ax=ax, label="sessions")
        # Spearman correlation
        from scipy.stats import spearmanr
        if len(dur) > 100:
            r, _ = spearmanr(dur, gap)
            ax.text(0.95, 0.05, f"ρ = {r:.3f}", transform=ax.transAxes,
                    fontsize=10, ha="right", va="bottom",
                    bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))

        # Print
        print(f"\n  {LABELS[tbl]} gap vs duration:")
        print(f"    n = {len(dur):,} pairs")
        if len(dur) > 100:
            print(f"    Spearman ρ = {r:.3f}")

    fig.tight_layout()
    save(fig, "06_gap_vs_duration.png")


# ═════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="EDA — sessions_all vs sessions_engagement")
    parser.add_argument("--sample", type=int, default=0,
                        help="Sample size for queries (0 = full data)")
    parser.add_argument("--skip", type=str, default="",
                        help="Sections to skip, comma-separated (e.g. '4,6')")
    args = parser.parse_args()
    skip_set = {int(x.strip()) for x in args.skip.split(",") if x.strip()}

    conn = pymysql.connect(**DB)
    print(f"Connected to {DB['host']}:{DB['port']}")
    total_t0 = time_mod.time()

    sections = [
        (1, section1, {"conn": conn, "sample": args.sample}),
        (2, section2, {"conn": conn, "sample": args.sample}),
        (3, section3, {"conn": conn}),
        (4, section4, {"conn": conn, "sample": args.sample}),
        (5, section5, {"conn": conn, "sample": args.sample}),
        (6, section6, {"conn": conn, "sample": args.sample}),
    ]

    for n, fn, kw in sections:
        if n in skip_set:
            print(f"\n  [§{n} — SKIPPED]")
            continue
        fn(**kw)

    conn.close()
    elapsed = time_mod.time() - total_t0
    print(f"\n{'='*60}")
    print(f"  Done in {elapsed:.0f}s  →  {OUT}/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
