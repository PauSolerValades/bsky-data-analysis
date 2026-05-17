#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "numpy",
#     "matplotlib",
#     "pymysql",
# ]
# ///
"""
Phase 6 — Engagement cascade ordering.

What is the typical sequence of engagement on a post?
  - Which event type arrives first? (like → repost, or repost → like?)
  - Markov transition matrix between consecutive event types.
  - Conditional probabilities: given a repost, what's P(also liked)?

Uses post_lifetime for first-event comparisons, and post_engagement_events
for per-post transition matrices.

Output:
  - Console: first-event stats, transition matrix, conditional probs
  - eda/results/cascade_first_event.png   (pie/bar of first event type)
  - eda/results/cascade_transitions.png   (heatmap of transition matrix)

Usage:
    uv run post-lifetime/eda/cascade_ordering.py
    uv run post-lifetime/eda/cascade_ordering.py --sample 5000
"""

import argparse
import os
from collections import Counter
from pathlib import Path

import numpy as np
import pymysql

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def _load_env_file():
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for f in candidates:
        if f.exists():
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
            return

_load_env_file()

def _env(k, d=""):
    return os.environ.get(k, d)

DB_CONFIG = {
    "host": _env("DATABASE_HOST", "10.18.74.14"),
    "port": int(_env("DATABASE_PORT", "9030")),
    "user": _env("DATABASE_USER", "pau"),
    "password": _env("PAU_PASSWORD", ""),
    "database": "pau_db",
    "charset": "utf8mb4",
}

RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# Part 1: First-event analysis (from post_lifetime)
# ===========================================================================

def analyze_first_event(conn):
    """
    For posts that have at least two engagement types, which one arrives first?
    Uses first_* columns.
    """
    sql = """
        SELECT
            CASE
                WHEN first_reposted_us < first_liked_us
                 AND first_reposted_us < first_replied_us THEN 'repost_first'
                WHEN first_liked_us < first_reposted_us
                 AND first_liked_us < first_replied_us THEN 'like_first'
                WHEN first_replied_us < first_reposted_us
                 AND first_replied_us < first_liked_us THEN 'reply_first'
                ELSE 'tie_or_other'
            END AS first_event,
            COUNT(*) AS cnt
        FROM post_lifetime
        WHERE total_reposts > 0 AND total_likes > 0 AND total_replies > 0
        GROUP BY first_event
        ORDER BY cnt DESC
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def analyze_pairwise_first(conn):
    """
    For each pair of event types, count which arrives first.
    """
    pairs = [
        ("repost", "like",   "first_reposted_us", "first_liked_us"),
        ("repost", "reply",  "first_reposted_us", "first_replied_us"),
        ("like",   "reply",  "first_liked_us",    "first_replied_us"),
    ]
    results = {}
    for a, b, col_a, col_b in pairs:
        sql = f"""
            SELECT
                SUM(CASE WHEN {col_a} < {col_b} THEN 1 ELSE 0 END) AS a_first,
                SUM(CASE WHEN {col_b} < {col_a} THEN 1 ELSE 0 END) AS b_first,
                SUM(CASE WHEN {col_a} = {col_b} THEN 1 ELSE 0 END) AS tie
            FROM post_lifetime
            WHERE {col_a} IS NOT NULL AND {col_b} IS NOT NULL
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            results[(a, b)] = (int(row[0]), int(row[1]), int(row[2]))
    return results


# ===========================================================================
# Part 2: Transition matrix (from post_engagement_events, sampled posts)
# ===========================================================================

def build_transition_matrix(conn, n_posts=5000):
    """
    For n_posts random engaged posts, fetch event sequences and count
    transitions between consecutive event types.
    Returns (transition_counts, total_by_type).
    """
    print(f"  Sampling {n_posts} posts for transition analysis …")

    sql = f"""
        SELECT post_did, post_rkey
        FROM post_lifetime
        WHERE total_engagement >= 3
        ORDER BY RAND()
        LIMIT {n_posts}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        posts = cur.fetchall()

    types = ["repost", "like", "reply"]
    transitions = Counter()
    type_count = Counter()
    total_pairs = 0

    for i, (did, rkey) in enumerate(posts):
        if (i + 1) % 1000 == 0:
            print(f"    {i+1}/{len(posts)} …")

        sql_events = """
            SELECT event_type
            FROM post_engagement_events
            WHERE post_did = %s AND post_rkey = %s
            ORDER BY event_time_us
        """
        with conn.cursor() as cur:
            cur.execute(sql_events, (did, rkey))
            event_types = [r[0] for r in cur.fetchall()]

        if len(event_types) < 2:
            continue

        for et in event_types:
            type_count[et] += 1

        for j in range(len(event_types) - 1):
            a, b = event_types[j], event_types[j + 1]
            transitions[(a, b)] += 1
            total_pairs += 1

    print(f"    {total_pairs:,} transitions from {len(posts)} posts")
    return transitions, type_count, types


def normalize_transitions(transitions, type_count, types):
    """
    Build a Markov transition matrix: P(B | A) = count(A→B) / count(A).
    """
    n = len(types)
    matrix = np.zeros((n, n))
    for i, a in enumerate(types):
        row_total = type_count.get(a, 0)
        for j, b in enumerate(types):
            matrix[i, j] = transitions.get((a, b), 0) / row_total if row_total else 0
    return matrix


# ===========================================================================
# Plotting
# ===========================================================================

def plot_first_event(first_event_rows, pairwise_results, output_path):
    """Pie chart of first-event type + pairwise bar chart."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Pie: first event
    labels = [r[0] for r in first_event_rows]
    sizes = [r[1] for r in first_event_rows]
    colors = ["#e0245e", "#1d9bf0", "#17bf63", "gray"]
    explode = [0.02] * len(labels)
    ax1.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=140,
            colors=colors[:len(labels)], explode=explode)
    ax1.set_title("First engagement type\n(posts with all 3 types)")

    # Bar: pairwise
    pair_labels = []
    a_first_vals = []
    b_first_vals = []
    for (a, b), (af, bf, tie) in pairwise_results.items():
        pair_labels.append(f"{a}\nvs\n{b}")
        a_first_vals.append(af)
        b_first_vals.append(bf)

    x = np.arange(len(pair_labels))
    width = 0.35
    bars1 = ax2.bar(x - width / 2, a_first_vals, width, label="first",
                    color=["#1d9bf0", "#1d9bf0", "#e0245e"])
    bars2 = ax2.bar(x + width / 2, b_first_vals, width, label="second",
                    color=["#e0245e", "#17bf63", "#17bf63"])

    # Add percentage labels
    for i, (af, bf) in enumerate(zip(a_first_vals, b_first_vals)):
        total = af + bf
        if total:
            ax2.text(i - width / 2, af + total * 0.02, f"{100*af/total:.0f}%",
                     ha="center", fontsize=8)
            ax2.text(i + width / 2, bf + total * 0.02, f"{100*bf/total:.0f}%",
                     ha="center", fontsize=8)

    ax2.set_xticks(x)
    ax2.set_xticklabels(pair_labels)
    ax2.set_ylabel("Number of posts")
    ax2.set_title("Pairwise: which type arrives first?")
    ax2.legend(fontsize=8)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ {output_path}")


def plot_transition_matrix(matrix, types, output_path):
    """Heatmap of Markov transition matrix."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 5.5))
    im = ax.imshow(matrix, cmap="YlOrRd", vmin=0, vmax=1)

    for i in range(len(types)):
        for j in range(len(types)):
            val = matrix[i, j]
            color = "white" if val > 0.6 else "black"
            ax.text(j, i, f"{val:.3f}", ha="center", va="center",
                    fontsize=13, color=color, fontweight="bold")

    ax.set_xticks(range(len(types)))
    ax.set_yticks(range(len(types)))
    ax.set_xticklabels(types)
    ax.set_yticklabels(types)
    ax.set_xlabel("Next event")
    ax.set_ylabel("Current event")
    ax.set_title("Engagement transition probabilities\nP(next | current)")

    plt.colorbar(im, ax=ax, label="Probability")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  ✓ {output_path}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=5000,
                        help="Posts for transition matrix (default: 5000)")
    args = parser.parse_args()

    print("=" * 65)
    print("  Phase 6 — Engagement cascade ordering")
    print("=" * 65)
    print()

    conn = pymysql.connect(**DB_CONFIG)
    try:
        # ── Part 1: First event ─────────────────────────────────────────
        print("── First engagement type ──")
        first_rows = analyze_first_event(conn)
        total_posts_with_all = sum(r[1] for r in first_rows)
        print(f"  Posts with all 3 engagement types: {total_posts_with_all:,}")
        for label, cnt in first_rows:
            print(f"    {label:<20s}: {cnt:>10,}  ({100*cnt/total_posts_with_all:.1f}%)")
        print()

        print("── Pairwise first-event comparison ──")
        pairwise = analyze_pairwise_first(conn)
        for (a, b), (af, bf, tie) in pairwise.items():
            total = af + bf + tie
            print(f"    {a} < {b}: {af:>10,} ({100*af/total:.1f}%)  |  "
                  f"{b} < {a}: {bf:>10,} ({100*bf/total:.1f}%)  |  "
                  f"tie: {tie:,}")
        print()

        # ── Part 2: Transition matrix ───────────────────────────────────
        print("── Markov transition matrix ──")
        transitions, type_count, types = build_transition_matrix(conn, args.sample)
        matrix = normalize_transitions(transitions, type_count, types)

        print("\n  Transition probabilities P(next | current):")
        header = "        " + "".join(f"{t:>10s}" for t in types)
        print(header)
        for i, a in enumerate(types):
            row = f"  {a:<6s} " + "".join(f"{matrix[i,j]:>10.4f}" for j in range(len(types)))
            print(row)
        print()

        # Conditional probabilities
        print("── Conditional engagement probabilities ──")
        sql_cond = """
            SELECT
                COUNT(*) AS total_posts,
                SUM(CASE WHEN total_reposts > 0 AND total_likes > 0 THEN 1 ELSE 0 END)
                    AS both_rl,
                SUM(CASE WHEN total_reposts > 0 AND total_replies > 0 THEN 1 ELSE 0 END)
                    AS both_rr,
                SUM(CASE WHEN total_likes > 0 AND total_replies > 0 THEN 1 ELSE 0 END)
                    AS both_lr,
                SUM(CASE WHEN total_reposts > 0 THEN 1 ELSE 0 END) AS has_repost,
                SUM(CASE WHEN total_likes > 0 THEN 1 ELSE 0 END) AS has_like,
                SUM(CASE WHEN total_replies > 0 THEN 1 ELSE 0 END) AS has_reply
            FROM post_lifetime
        """
        with conn.cursor() as cur:
            cur.execute(sql_cond)
            row = cur.fetchone()
        total, both_rl, both_rr, both_lr, has_rp, has_lk, has_ry = row

        print(f"  Total posts: {total:,}")
        print(f"  P(like | repost)    = {both_rl/has_rp:.3f}" if has_rp else "  N/A")
        print(f"  P(repost | like)    = {both_rl/has_lk:.3f}" if has_lk else "  N/A")
        print(f"  P(reply | repost)   = {both_rr/has_rp:.3f}" if has_rp else "  N/A")
        print(f"  P(repost | reply)   = {both_rr/has_ry:.3f}" if has_ry else "  N/A")
        print(f"  P(reply | like)     = {both_lr/has_lk:.3f}" if has_lk else "  N/A")
        print(f"  P(like | reply)     = {both_lr/has_ry:.3f}" if has_ry else "  N/A")
        print()

        # ── Plots ───────────────────────────────────────────────────────
        print("Generating plots …")
        plot_first_event(first_rows, pairwise,
                         RESULTS / "cascade_first_event.png")
        plot_transition_matrix(matrix, types,
                               RESULTS / "cascade_transitions.png")
        print()
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
