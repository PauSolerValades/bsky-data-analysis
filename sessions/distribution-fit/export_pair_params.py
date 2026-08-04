"""Export per-user pair + fitted parameters (data prep only, no analysis).

Joins the AIC-best distributions (best_per_user.tsv) with their fitted parameters
(best_params.tsv) and writes one wide row per user:
  did, dur_family, gap_family, dur_dist, gap_dist, dur_n, gap_n,
  dur_<param>…, gap_<param>…

Param columns are generic (shape/scale/rate/meanlog/sdlog) and filled only for
the winning distribution of that column. Users with < 30 observations on
either side (duration or gap n_obs) excluded — already enforced in step2,
kept here as a guard in case best_per_user.tsv is stale.

Usage:
    uv run distribution-fit/export_pair_params.py
"""

import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BEST = HERE / "results/best_per_user.tsv"
PARAMS = HERE / "results/best_params.tsv"
OUT = HERE / "results/pair_params_wide.tsv"
MIN_SESSIONS = 30

PARAM_NAMES = ["shape", "scale", "rate", "meanlog", "sdlog"]

fields = (["did", "dur_family", "gap_family", "dur_dist", "gap_dist",
           "dur_n", "gap_n"]
          + [f"dur_{p}" for p in PARAM_NAMES]
          + [f"gap_{p}" for p in PARAM_NAMES])

dur_best, gap_best = {}, {}
with open(BEST, newline="") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        (dur_best if row["col"] == "duration" else gap_best)[row["did"]] = row

# (did, col) -> {distribution: {param: value}}
params = defaultdict(lambda: defaultdict(dict))
with open(PARAMS, newline="") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        params[(row["did"], row["col"])][row["distribution"]][row["param"]] = row["value"]

n = 0
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
    w.writeheader()
    for did, dur in dur_best.items():
        gap = gap_best.get(did)
        if (gap is None or int(dur["n_obs"]) < MIN_SESSIONS
                or int(gap["n_obs"]) < MIN_SESSIONS):
            continue
        out = {
            "did": did,
            "dur_family": dur["family"], "gap_family": gap["family"],
            "dur_dist": dur["distribution"], "gap_dist": gap["distribution"],
            "dur_n": dur["n_obs"], "gap_n": gap["n_obs"],
        }
        dp = params[(did, "duration")][dur["distribution"]]
        gp = params[(did, "gap")][gap["distribution"]]
        for p in PARAM_NAMES:
            out[f"dur_{p}"] = dp.get(p, "")
            out[f"gap_{p}"] = gp.get(p, "")
        w.writerow(out)
        n += 1

print(f"→ {OUT} ({n:,} users)")
