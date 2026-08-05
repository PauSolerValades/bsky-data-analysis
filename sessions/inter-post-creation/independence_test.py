"""H0: posts are a session-independent point process.

Formalized as a PERMUTATION TEST on the post<->session alignment:
  statistic: per-user within-session pair count (one-sided: real > null).
  null:      R random circular time-shifts of the user's REAL post stream
             (gap sequence, order, burstiness preserved; only the alignment
             with the session windows is destroyed). p = (1+#{null>=obs})/(R+1).
  combined:  Fisher's method across users.

Descriptive replays (not part of the formal test): iid bootstrap of global
gaps (rate/shape decomposition) and conditional-truncation sampling (the
"within = truncated global" repair hypothesis — rejected, see METHODOLOGY).

Replay test, per user:
  1. real: within-session gaps from the user's real post stream vs real sessions.
  2. synthetic: bootstrap the user's own empirical GLOBAL gaps into a new post
     stream (t0 = first real post time), replay against the user's REAL session
     intervals, collect synthetic within-session gaps.

If posts ignore session state (H0), synthetic-within ≈ real-within: the within
distribution is just the global process viewed through session windows, and
the simulator only needs the global distribution + the session gate.
If real-within is much shorter (bursty posting while online), H0 fails and
within-session cadence needs its own (empirical) model.

Users: sample of those with >= 30 global gaps (the family-analysis pool).

Usage:
    uv run inter-post-creation/independence_test.py [--users 5000]
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

import matplotlib.pyplot as plt
import seaborn as sns

sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "table_creation"))
sys.path.insert(0, str(HERE))
from _core import get_connection, _execute, TBL_PREFIX
from dump import user_gaps

MIN_GLOBAL = 30
CAP_PER_USER = 200  # ponytail: heavy users would dominate the pooled ECDF


def within_count(ts, starts, ends):
    """Consecutive-post pairs inside the same session (vectorized user_gaps)."""
    sid = np.searchsorted(starts, ts, side="right") - 1
    ok = (sid >= 0) & (ts <= ends[np.clip(sid, 0, None)])
    return int(np.sum(ok[:-1] & ok[1:] & (sid[:-1] == sid[1:])))


def fetch(conn, dids):
    """Return ({did: [post_ts_us]}, {did: [(start,end)]})."""
    sessions, posts = defaultdict(list), defaultdict(list)
    for i in range(0, len(dids), 2000):
        batch = dids[i:i + 2000]
        ph = ",".join(["%s"] * len(batch))
        for d, s, e in _execute(conn, f"""
            SELECT did, session_start, session_end FROM {TBL_PREFIX}sessions
            WHERE did IN ({ph}) ORDER BY did, session_start""", batch):
            sessions[d].append((int(s), int(e)))
        for d, t in _execute(conn, f"""
            SELECT did, time_us FROM {TBL_PREFIX}events
            WHERE did IN ({ph}) AND event_type IN ('post_top','post_reply')
            ORDER BY did, time_us""", batch):
            posts[d].append(int(t))
    return posts, sessions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=5000)
    ap.add_argument("--perms", type=int, default=499, help="circular-shift null draws")
    args = ap.parse_args()
    R = args.perms

    best = pl.read_csv(HERE / "results/best_per_user.tsv", separator="\t")
    pool = best.filter((pl.col("col") == "interpost_global")
                       & (pl.col("n_obs") >= MIN_GLOBAL))["did"].to_list()
    rng = np.random.default_rng(42)
    dids = [pool[i] for i in rng.choice(len(pool), size=min(args.users, len(pool)),
                                        replace=False)]
    print(f"{len(pool):,} eligible users, sampled {len(dids):,}", file=sys.stderr)

    conn = get_connection()
    posts, sessions = fetch(conn, dids)
    conn.close()

    real_pool, synth_pool, cond_pool = [], [], []
    ratios, cond_ratios = [], []  # per-user real_median / synth_median
    count_ratios = []  # per-user len(real_w) / len(synth_w)
    shift_count_ratios = []  # obs / median null count (circular-shift replay)
    pvals = []  # per-user permutation p-values
    cond_count_ratios = []  # same for the conditional-truncation replay
    n_real_ok = n_synth_ok = n_cond_ok = 0
    for did in dids:
        ps, iv = posts.get(did), sessions.get(did, [])
        if not ps or len(ps) < MIN_GLOBAL + 1:
            continue
        real_w, real_g = user_gaps(ps, iv)
        if len(real_w) < 5:
            continue
        n_real_ok += 1
        # synthetic stream: same span, gaps bootstrapped from own global ECDF
        gaps = np.array(real_g)
        gaps = gaps[gaps > 0]
        synth = np.cumsum(rng.choice(gaps, size=len(gaps), replace=True) * 1e6) + ps[0]
        synth_ts = [int(t) for t in synth if t <= ps[-1]]
        synth_w, _ = user_gaps(synth_ts, iv)
        count_ratios.append(len(real_w) / max(len(synth_w), 1))
        # permutation null: R random circular shifts — real gaps, real order,
        # only the post<->session alignment destroyed (the formal test)
        ps_arr = np.array(ps)
        span = ps[-1] - ps[0]
        starts = np.array([s for s, _ in iv])
        ends = np.array([e for _, e in iv])
        assert within_count(ps_arr, starts, ends) == len(real_w)
        null_counts = np.empty(R)
        for r_ in range(R):
            off = int(rng.uniform(0, span))
            shifted = np.sort((ps_arr - ps[0] + off) % span + ps[0])
            null_counts[r_] = within_count(shifted, starts, ends)
        obs = len(real_w)
        pvals.append((1 + np.sum(null_counts >= obs)) / (R + 1))
        shift_count_ratios.append(obs / max(np.median(null_counts), 1))
        # conditional replay: inside each real session, sample global gaps
        # conditioned on gap <= remaining session time (first post at start)
        sg = np.sort(gaps)
        cond_w = []
        for s, e in iv:
            t = s
            while len(cond_w) < 10_000:  # ponytail: cap pathological users
                rem = (e - t) / 1e6
                k = np.searchsorted(sg, rem, side="right")
                if k == 0:
                    break
                g = sg[rng.integers(0, k)]
                cond_w.append(g)
                t += int(g * 1e6)
        cond_count_ratios.append(len(real_w) / max(len(cond_w), 1))
        if len(cond_w) >= 5:
            n_cond_ok += 1
            cond_ratios.append(np.median(real_w) / np.median(cond_w))
            cond_pool.extend(rng.choice(cond_w, min(CAP_PER_USER, len(cond_w)),
                                        replace=False))
        if len(synth_w) < 5:
            continue
        n_synth_ok += 1
        real_pool.extend(rng.choice(real_w, min(CAP_PER_USER, len(real_w)), replace=False))
        synth_pool.extend(rng.choice(synth_w, min(CAP_PER_USER, len(synth_w)), replace=False))
        ratios.append(np.median(real_w) / np.median(synth_w))

    r, s = np.array(real_pool), np.array(synth_pool)
    c = np.array(cond_pool)
    ratios = np.array(ratios)
    count_ratios = np.array(count_ratios)
    print(f"\nusers with >=5 real within gaps: {n_real_ok:,}; "
          f"of those, replay also produces >=5: {n_synth_ok:,} ({100*n_synth_ok/n_real_ok:.0f}%)")
    print(f"per-user within-COUNT ratio real/synth: median {np.median(count_ratios):.1f}x, "
          f"synth has <half of real for {100*(count_ratios>2).mean():.0f}% of users")
    scr = np.array(shift_count_ratios)
    print(f"SHIFT replay (real order preserved) count ratio: median {np.median(scr):.1f}x, "
          f"shift has <half of real for {100*(scr>2).mean():.0f}% of users")
    pv = np.array(pvals)
    fisher = -2 * np.sum(np.log(pv))
    print(f"PERMUTATION TEST ({R} shifts): {100*(pv<=0.05).mean():.0f}% of users "
          f"reject H0 at 0.05 (min attainable p={1/(R+1):.4f}), median p={np.median(pv):.4f}, "
          f"Fisher chi2={fisher:.0f} on {2*len(pv)} df")
    ccr = np.array(cond_count_ratios)
    print(f"COND replay (truncated sampling) count ratio: median {np.median(ccr):.1f}x, "
          f"cond has <half of real for {100*(ccr>2).mean():.0f}% of users, "
          f">=5 cond gaps for {n_cond_ok:,} users")
    cr = np.array(cond_ratios)
    print(f"COND per-user median(real)/median(cond): median {np.median(cr):.2f}, "
          f"real < cond for {100 * (cr < 1).mean():.0f}% of users")
    print(f"\nusers in test: {len(ratios):,}  pooled gaps: real {len(r):,} / synth {len(s):,}")
    print(f"\n{'quantile':>8} {'real within':>12} {'synth within':>13} {'cond within':>12}")
    for q in (0.25, 0.5, 0.75, 0.9, 0.99):
        rq, sq, cq = np.quantile(r, q), np.quantile(s, q), np.quantile(c, q)
        print(f"p{int(q*100):>6} {rq:>11.0f}s {sq:>12.0f}s {cq:>11.0f}s")
    print(f"\nper-user median(real)/median(synth): median {np.median(ratios):.2f}, "
          f"real < synth for {100 * (ratios < 1).mean():.0f}% of users")

    # ECDF overlay (one plot, thesis style)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for data, label, color in ((r, "real within-session", "#0072B2"),
                               (s, "synthetic (global replay)", "#D55E00"),
                               (c, "conditional (truncated sampling)", "#009E73")):
        xs = np.sort(data)
        ax.plot(xs, np.arange(1, len(xs) + 1) / len(xs), label=label, color=color,
                linewidth=1.2)
    ax.set_xscale("log")
    ax.set_xlabel("within-session gap (s)")
    ax.set_ylabel("ECDF")
    ax.legend()
    fig.tight_layout()
    out = HERE / "results" / "independence_ecdf.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"→ {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
