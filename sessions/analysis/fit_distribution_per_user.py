"""Per-user distribution fitting — one row per (did, distribution, GOF method).

Usage:
    uv run sessions/analysis/fit_distribution_per_user.py --table-name sessions_hdbscan_mcs2_ms1_e60 --column duration_s --gof wasserstein --sample-users 100
"""

import argparse
import csv
import random
import signal
import sys
from pathlib import Path

import numpy as np
from distfit import distfit

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from running_locally.local_db import Where, get_connection as local_connect

WHERE = Where.from_env()
TBL_PREFIX = "pau_db." if WHERE == Where.SERVER else ""

DEFAULT_OUT = Path(__file__).resolve().parent / "results"

TIME_DISTS = ['expon', 'gamma', 'lognorm', 'weibull_min', 'fisk',
              'pareto', 'lomax', 'genpareto']


class _Timeout(Exception):
    pass


def _fit_user(data, dists, gof, n_boots):
    dfit = distfit(distr=dists, stats=gof, n_boots=n_boots, verbose=0)
    dfit.fit_transform(data)
    return dfit


def _fit_with_timeout(data, dists, gof, n_boots, timeout):
    result = [None]

    def handler(signum, frame):
        raise _Timeout()

    old = signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        result[0] = _fit_user(data, dists, gof, n_boots)
    except _Timeout:
        pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    return result[0]


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Per-user distribution fitting.")
    parser.add_argument("--table-name", type=str, required=True)
    parser.add_argument("--column", type=str, required=True,
                        choices=["duration_s", "gap_s"])
    parser.add_argument("--gof", type=str, default="wasserstein",
                        choices=["RSS", "wasserstein", "ks", "energy", "goodness_of_fit"])
    parser.add_argument("--n-boots", type=int, default=10)
    parser.add_argument("--min-sessions", type=int, default=3,
                        help="Min sessions/gaps per user to fit")
    parser.add_argument("--sample-users", type=int, default=0,
                        help="Max users (0 = all)")
    parser.add_argument("--per-user-timeout", type=float, default=10.0,
                        help="Seconds max per user")
    parser.add_argument("--plot-dir", type=str, default=str(DEFAULT_OUT))
    args = parser.parse_args()
    plot_dir = Path(args.plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    conn = local_connect(Where.from_env(), repo_root=str(REPO))

    if args.column == "duration_s":
        rows = conn.query(f"""
            SELECT did, duration_s
            FROM {TBL_PREFIX}{args.table_name}
            WHERE duration_s > 0
            ORDER BY did
        """)
        col_label = "duration"
    else:
        rows = conn.query(f"""
            SELECT did, gap_s FROM (
                SELECT did,
                    (LEAD(session_start) OVER (PARTITION BY did ORDER BY session_start)
                     - session_end) / 1000000.0 AS gap_s
                FROM {TBL_PREFIX}{args.table_name}
            ) t
            WHERE gap_s IS NOT NULL AND gap_s > 0
            ORDER BY did
        """)
        col_label = "gap"

    conn.close()

    # ── Group by DID ───────────────────────────────────────────────────

    user_data = {}
    for did, val in rows:
        user_data.setdefault(did, []).append(float(val))

    print(f"  {len(user_data):,} users loaded", file=sys.stderr)
    if args.sample_users > 0:
        sampled = dict(random.sample(list(user_data.items()),
                       min(args.sample_users, len(user_data))))
        user_data = sampled
        print(f"  → sampled {len(user_data):,} users", file=sys.stderr)

    user_data = {did: vals for did, vals in user_data.items()
                 if len(vals) >= args.min_sessions}
    print(f"  → {len(user_data):,} users with ≥{args.min_sessions} {col_label}s",
          file=sys.stderr)

    # ── Fit per user ───────────────────────────────────────────────────

    all_rows = []
    n_processed = 0
    n_skipped = 0

    for did, vals in user_data.items():
        data = np.array(vals, dtype=np.float64)
        data = data[data > 0]
        if len(data) < 2:
            continue

        dfit = _fit_with_timeout(data, TIME_DISTS, args.gof, args.n_boots,
                                 args.per_user_timeout)
        if dfit is None:
            n_skipped += 1
            continue

        for _, row in dfit.summary.iterrows():
            all_rows.append({
                "did": did,
                "distribution": row["name"],
                "score": row["score"],
                "bootstrap_score": row.get("bootstrap_score", None),
                "bootstrap_pass": row.get("bootstrap_pass", None),
                "loc": row["loc"],
                "scale": row["scale"],
                "arg": str(row["arg"]) if row.get("arg") else "",
                "gof": args.gof,
                "n_obs": len(data),
            })

        n_processed += 1
        if n_processed % 100 == 0:
            print(f"\r  {n_processed}/{len(user_data)} users...",
                  end="", file=sys.stderr, flush=True)

    print(f"\r  {n_processed}/{len(user_data)} users done"
          f" ({n_skipped} skipped)", file=sys.stderr)

    # ── Save TSV ───────────────────────────────────────────────────────

    out_path = plot_dir / f"fit_{col_label}_{args.gof}_per_user__{args.table_name}.tsv"
    fieldnames = ["did", "distribution", "score", "bootstrap_score",
                  "bootstrap_pass", "loc", "scale", "arg", "gof", "n_obs"]

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"  → saved {out_path} ({len(all_rows):,} rows)", file=sys.stderr)


if __name__ == "__main__":
    main()
