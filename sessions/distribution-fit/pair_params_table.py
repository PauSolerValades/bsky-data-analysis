"""Per-pair fitted-parameter medians + spread (analysis of export_pair_params output).

For each (dur_dist, gap_dist) pair, one row per (pair, param): median [Q1-Q3]
of the fitted values across users, for the duration winner and the gap winner.
[Q1-Q3] is the middle 50% of users — a point median alone hides how much
per-user fits disagree, and these params are heavy-tailed across users.

Usage:
    uv run distribution-fit/pair_params_table.py
"""

import csv
import statistics
from collections import defaultdict
from pathlib import Path

WIDE = Path(__file__).resolve().parent / "results/pair_params_wide.tsv"

PARAMS = ["shape", "scale", "rate", "meanlog", "sdlog"]

# (pair, side, param) -> [values]; pair = (dur_dist, gap_dist)
vals = defaultdict(list)
n_users = defaultdict(int)
with open(WIDE, newline="") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        pair = (row["dur_dist"], row["gap_dist"])
        n_users[pair] += 1
        for side in ("dur", "gap"):
            for p in PARAMS:
                v = row.get(f"{side}_{p}")
                if v:
                    vals[(pair, side, p)].append(float(v))

order = sorted(n_users, key=n_users.get, reverse=True)


def fmt(v):
    if abs(v) >= 100:
        return f"{v:,.0f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.3g}"


def cell(vs):
    if not vs:
        return "-"
    med = statistics.median(vs)
    if len(vs) < 2:
        return fmt(med)
    q1, _, q3 = statistics.quantiles(vs, n=4)
    return f"{fmt(med)} [{fmt(q1)}-{fmt(q3)}]"


W = 32
print(f"{'pair':<26}{'param':<9}{'dur: median [Q1-Q3]':<{W}}{'gap: median [Q1-Q3]':<{W}}{'n':>8}")
for pair in order:
    first = True
    for p in PARAMS:
        dv, gv = vals[(pair, "dur", p)], vals[(pair, "gap", p)]
        if not dv and not gv:
            continue
        print(f"{f'{pair[0]}->{pair[1]}' if first else '':<26}{p:<9}"
              f"{cell(dv):<{W}}{cell(gv):<{W}}{f'{n_users[pair]:,}' if first else '':>8}")
        first = False
    print()
