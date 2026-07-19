"""
§4 — Solo vs real sessions.

Quantifies how many "sessions" are actually solo events (duration_s == 0).
Outputs:
  - TSV with per-source summary stats
  - Plot: real-sessions-per-user distribution (hist, log-log, PDF)
"""

import sys

import numpy as np

from _common import (
    Source,
    fetch_per_user_stats,
    get_connection,
    save_hist,
    save_loglog,
    save_pdf,
    set_subdir,
    OUT,
)


def run(source: Source):
    """Produce §4 outputs for a single source."""
    set_subdir("session_composition")
    print(f"\n── §4: Solo vs real sessions — {source.value} ──", file=sys.stderr)

    conn = get_connection()
    sql = f"""
        SELECT did, duration_s
        FROM {source.table}
        ORDER BY did
    """
    print(f"  Fetching {source.table} ...", file=sys.stderr)
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    conn.close()
    print(f"    → {len(rows):,} rows", file=sys.stderr)

    # Per-user: count solo (duration==0) and real (duration>0) sessions
    from collections import defaultdict
    user_total: dict[str, int] = defaultdict(int)
    user_real: dict[str, int] = defaultdict(int)

    for did, dur in rows:
        user_total[did] += 1
        if dur > 0:
            user_real[did] += 1

    total_sessions = len(rows)
    n_users = len(user_total)

    solo_sessions = sum(
        user_total[did] - user_real[did] for did in user_total
    )
    real_sessions = total_sessions - solo_sessions

    # Users with zero real sessions (Tukey failed entirely for them)
    all_solo_users = sum(1 for did in user_total if user_real.get(did, 0) == 0)
    real_users = [v for v in user_real.values()]

    # ── TSV ──
    from _common import OUT_SUBDIR
    tsv_dir = OUT / OUT_SUBDIR if OUT_SUBDIR else OUT
    tsv_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = tsv_dir / f"04_{source.value}_solo_vs_real.tsv"
    with open(tsv_path, "w") as f:
        f.write("\t".join([
            "source", "n_users", "n_sessions_total", "n_solo", "n_real",
            "pct_solo", "n_users_all_solo", "pct_users_all_solo",
            "median_real_per_user_having_any",
        ]) + "\n")

        median_real = np.median(real_users) if real_users else 0
        f.write("\t".join([
            source.value,
            str(n_users),
            str(total_sessions),
            str(solo_sessions),
            str(real_sessions),
            f"{100 * solo_sessions / total_sessions:.1f}",
            str(all_solo_users),
            f"{100 * all_solo_users / n_users:.1f}",
            f"{median_real:.1f}",
        ]) + "\n")
    print(f"  → {tsv_path}", file=sys.stderr)

    # Print summary
    print(f"  Sessions: {total_sessions:,} total, "
          f"{solo_sessions:,} solo ({100*solo_sessions/total_sessions:.1f}%), "
          f"{real_sessions:,} real", file=sys.stderr)
    print(f"  Users: {n_users:,} total, "
          f"{all_solo_users:,} all-solo ({100*all_solo_users/n_users:.1f}%)",
          file=sys.stderr)

    # ── Plots: real sessions per user ──
    real_arr = np.array(real_users, dtype=np.int64)
    suffix = "Real sessions per user"

    save_hist(real_arr, source, "04", f"{suffix} (hist)",
              xlabel="Real sessions per user")
    save_loglog(real_arr, source, "04", f"{suffix} (log-log)",
                xlabel="Real sessions per user")
    save_pdf(real_arr, source, "04", f"{suffix} (PDF)",
             xlabel="Real sessions per user")
