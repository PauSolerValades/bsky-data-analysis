"""Pairing of best-fit distribution: session duration vs inter-session gap, per user.

Reads distribution-fit/results/best_per_user.tsv, pivots to one row per user,
counts the (duration family x gap family) contingency. Users with < 30
observations on either side (duration or gap n_obs) are excluded — the pair
is only as reliable as its weaker fit. Already enforced in step2; the check
below is a guard against stale input.

Usage:
    uv run distribution-fit/pairs_duration_gap.py
"""

import csv
from collections import Counter
from pathlib import Path

TSV = Path(__file__).resolve().parent / "results/best_per_user.tsv"
MIN_SESSIONS = 30  # exclude users with < 30 obs on duration OR gap

pairs = Counter()      # (dur_family, gap_family) -> count
pairs_concrete = Counter()  # concrete distribution level
users = 0
dropped = 0

with open(TSV, newline="") as f:
    cur = None
    for row in csv.DictReader(f, delimiter="\t"):
        if row["col"] == "duration":
            cur = {"did": row["did"], "n": int(row["n_obs"]),
                   "fam": row["family"], "dist": row["distribution"]}
        else:
            if cur is None or cur["did"] != row["did"]:
                continue  # gap row without preceding duration row (shouldn't happen)
            if cur["n"] < MIN_SESSIONS or int(row["n_obs"]) < MIN_SESSIONS:
                dropped += 1
                cur = None
                continue
            pairs[(cur["fam"], row["family"])] += 1
            pairs_concrete[(cur["dist"], row["distribution"])] += 1
            users += 1
            cur = None

print(f"users kept (>={MIN_SESSIONS} dur and gap obs): {users:,}  (dropped {dropped:,})")
print()

order = ["power_tail", "weibull_min", "lognorm", "gamma", "expon", "fisk"]

# Marginal totals
dur_marg, gap_marg = Counter(), Counter()
for (d, g), c in pairs.items():
    dur_marg[d] += c
    gap_marg[g] += c

print(f"pairs: {users:,} = 100.0%\n")
hdr = "".join(f"{g:>12}" for g in order) + f"{'total':>12}"
print(f"{'duration \\ gap':<16}{hdr}")
for d in order:
    row = "".join(f"{pairs.get((d, g), 0)/users*100:>11.1f}%" for g in order)
    print(f"{d:<16}{row}{dur_marg[d]/users*100:>11.1f}%")
row = "".join(f"{gap_marg[g]/users*100:>11.1f}%" for g in order)
print(f"{'gap total':<16}{row}{100.0:>11.1f}%")

print("\nrow-normalized: for each duration family, where do the gaps land?")
for d in order:
    if dur_marg[d] == 0:
        continue
    row = "".join(f"{pairs.get((d, g), 0)/dur_marg[d]*100:>11.1f}%" for g in order)
    print(f"{d:<16}{row}{100.0:>11.1f}%")

same = sum(c for (d, g), c in pairs.items() if d == g)
print(f"\nsame family for both: {same/users*100:.1f}%  ({same:,} users)")
print(f"different family:     {(users-same)/users*100:.1f}%  ({users-same:,} users)")
for d in order:
    if dur_marg[d] == 0:
        continue
    s = pairs.get((d, d), 0)
    print(f"  duration {d:<12} -> same family {s/dur_marg[d]*100:5.1f}%")

# Same at concrete-distribution level
same_c = sum(c for (d, g), c in pairs_concrete.items() if d == g)
print(f"same exact distribution: {same_c/users*100:.1f}%")

# Most common specific pairs
print("\ntop 15 (duration, gap) family pairs:")
for (d, g), c in pairs.most_common(15):
    tag = "same" if d == g else ""
    print(f"  {d:<14} -> {g:<14} {c/users*100:6.1f}%  ({c:,}) {tag}")

print("\ntop 15 (duration, gap) exact-distribution pairs:")
for (d, g), c in pairs_concrete.most_common(15):
    tag = "same" if d == g else ""
    print(f"  {d:<14} -> {g:<14} {c/users*100:6.1f}%  ({c:,}) {tag}")
