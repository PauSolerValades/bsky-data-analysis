"""Single-pass export of per-pair ECDF samples for the simulator.

One DB sweep over the trusted users (pair_params_wide.tsv) collects, per
(duration_family, gap_family) pair:
  - within-session inter-post gaps  -> ecdfs/within_ecdf__<dur>__<gap>.txt
  - first-post offsets (session start -> first post)
                                    -> ecdfs/offset_ecdf__<dur>__<gap>.txt
Replaces the chunk-parquet route of ecdf_by_pair.py (which cannot see
offsets: dump.py drops endpoint intervals) so the data is queried once.

Default: all trusted DIDs, pairs with >= 1% of trusted users. Capped at
250k lines per file (uniform subsample, seed 42). Also writes one offset
ECDF plot per pair (within-gap plots already exist from ecdf_by_pair.py).

Usage:
    uv run inter-post-creation/export_by_pair.py [--n-dids 20000] [--pair weibull_min,lognorm]
"""

import argparse
import bisect
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "table_creation"))
from _core import get_connection, _execute, TBL_PREFIX

WIDE = HERE.parents[1] / "distribution-fit" / "results" / "pair_params_wide.tsv"
BATCH_DIDS = 2000
MIN_PAIR_SHARE = 0.01
MAX_LINES = 250_000

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

DISPLAY = {"pareto": "Power-law", "weibull_min": "Weibull", "lognorm": "Lognorm",
           "gamma": "Gamma", "expon": "Exp"}


def write_sample(path, v, rng):
    if len(v) > MAX_LINES:
        v = v[rng.choice(len(v), size=MAX_LINES, replace=False)]
    with open(path, "w") as f:
        f.writelines(f"{x:.6f}\n" for x in v)
    return len(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-dids", type=int, default=None,
                    help="stride-sample trusted DIDs (default: all)")
    ap.add_argument("--pair", default=None, help="dur,gap — single pair")
    args = ap.parse_args()

    wide = pl.read_csv(WIDE, separator="\t")
    pair_counts = (wide.group_by(["dur_family", "gap_family"]).len(name="n_users")
                       .sort("n_users", descending=True))
    total_users = int(pair_counts["n_users"].sum())
    keep = pair_counts.filter(pl.col("n_users") >= MIN_PAIR_SHARE * total_users)
    if args.pair:
        dur, gap = args.pair.split(",")
        keep = pair_counts.filter((pl.col("dur_family") == dur)
                                  & (pl.col("gap_family") == gap))
    kept = set(keep.select(pl.concat_str(["dur_family", "gap_family"],
                                         separator="__").alias("p"))["p"])
    print(f"trusted users: {total_users:,}  pairs kept: {keep.height}", file=sys.stderr)

    pair_of = {r["did"]: f"{r['dur_family']}__{r['gap_family']}"
               for r in wide.iter_rows(named=True) if True}
    dids = sorted(d for d, p in pair_of.items() if p in kept)
    if args.n_dids:
        stride = max(1, len(dids) // args.n_dids)
        dids = dids[::stride]
    print(f"{len(dids):,} DIDs to scan", file=sys.stderr)

    within, offset = {}, {}  # pair -> list of seconds
    conn = get_connection()
    for i in range(0, len(dids), BATCH_DIDS):
        batch = dids[i:i + BATCH_DIDS]
        ph = ",".join(["%s"] * len(batch))
        sessions = {}
        for did, s, e in _execute(conn, f"""
            SELECT did, session_start, session_end FROM {TBL_PREFIX}sessions
            WHERE did IN ({ph}) ORDER BY did, session_start""", batch):
            sessions.setdefault(did, []).append((s, e))
        posts = {}
        for did, t in _execute(conn, f"""
            SELECT did, time_us FROM {TBL_PREFIX}events
            WHERE did IN ({ph}) AND event_type IN ('post_top','post_reply')
            ORDER BY did, time_us""", batch):
            posts.setdefault(did, []).append(t)

        for did, ivals in sessions.items():
            p = pair_of[did]
            starts = [s for s, _ in ivals]
            prev_t, prev_o = None, None
            for t in posts.get(did, []):
                j = bisect.bisect_right(starts, t) - 1
                o = j if j >= 0 and t <= ivals[j][1] else None
                if o is not None:
                    if prev_o is None or o != prev_o:
                        offset.setdefault(p, []).append((t - ivals[o][0]) / 1e6)
                    elif prev_t is not None and t > prev_t:
                        within.setdefault(p, []).append((t - prev_t) / 1e6)
                prev_t, prev_o = t, o
        print(f"\r  {i + len(batch):,}/{len(dids):,}", end="", file=sys.stderr)
    conn.close()

    rng = np.random.default_rng(42)
    print(f"\n\n{'pair':<28}{'within':>9}{'offset':>9}", file=sys.stderr)
    for dur, gap_f, _ in keep.iter_rows():
        p = f"{dur}__{gap_f}"
        w = np.array(within.get(p, []))
        o = np.array(offset.get(p, []))
        if len(o) == 0:
            continue
        nw = write_sample(HERE / "ecdfs" / f"within_ecdf__{p}.txt", w, rng) if len(w) else 0
        no = write_sample(HERE / "ecdfs" / f"offset_ecdf__{p}.txt", o, rng)
        print(f"{p:<28}{nw:>9,}{no:>9,}", file=sys.stderr)

        fig, ax = plt.subplots(figsize=(7, 4.5))
        xs = np.sort(o)
        ax.plot(xs, np.arange(1, len(xs) + 1) / len(xs), color="#0072B2", linewidth=1.2)
        ax.set_xscale("symlog", linthresh=1)  # ~half of offsets are exactly 0; plain log drops them
        ax.set_xlabel("first-post offset (s)")
        ax.set_ylabel("ECDF")
        ax.set_title(f"First-post offset ECDF — {DISPLAY[dur]} $\\times$ "
                     f"{DISPLAY[gap_f]} users")
        fig.tight_layout()
        out = HERE / "plots" / f"offset_ecdf__{p}.png"
        fig.savefig(out, dpi=300)
        plt.close(fig)
        print(f"  → {out.name}", file=sys.stderr)


if __name__ == "__main__":
    main()
