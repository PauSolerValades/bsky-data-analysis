"""Best-fit family composition as a function of the n_obs cutoff.

For session durations and inter-session gaps, reports the share of users
whose best-fit family wins at each n_obs cutoff. Justifies the "active
enough user" criterion: the duration composition collapses from ~45% to
~11% power-law between cutoff 0 and 20-30 and stabilises there, while the
gap side keeps drifting (power-law returns for very active users).

Usage:
    uv run distribution-fit/composition_by_cutoff.py
    uv run distribution-fit/composition_by_cutoff.py --out results/composition_by_cutoff.tsv
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TSV = HERE / "results" / "best_per_user.tsv"

FAMILIES = ["power_tail", "weibull_min", "lognorm", "gamma", "expon", "fisk"]
CUTOFFS = [0, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 200, 300, 500, 750, 1000]


def main():
    parser = argparse.ArgumentParser(description="Composition vs n_obs cutoff.")
    parser.add_argument(
        "--out",
        type=Path,
        default=HERE / "results" / "composition_by_cutoff.tsv",
    )
    args = parser.parse_args()

    rows = defaultdict(list)  # col -> [(n_obs, family), ...]
    with open(TSV, newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            rows[row["col"]].append((int(row["n_obs"]), row["family"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["col", "cutoff", "n_users"] + FAMILIES)
        for col in ("duration", "gap"):
            data = rows[col]
            for cut in CUTOFFS:
                sub = [fam for n, fam in data if n > cut]
                n = len(sub)
                counts = defaultdict(int)
                for fam in sub:
                    counts[fam] += 1
                pcts = [f"{100 * counts[fam] / n:.1f}" if n else "0" for fam in FAMILIES]
                w.writerow([col, cut, n] + pcts)
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
