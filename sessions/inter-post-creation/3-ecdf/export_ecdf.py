"""Export the pooled within-session inter-post ECDF for the simulator.

Subsamples the pooled interpost_within gaps (all chunks, value > 0) and
writes one gap per line (seconds, plain text — same convention as the other
params/*.txt files; trivially loadable and sortable into ECDF bins).

Usage:
    uv run inter-post-creation/export_ecdf.py [--n 1000000]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1_000_000)
    args = ap.parse_args()

    df = (pl.scan_parquet(str(HERE.parent / "data/chunk*.parquet"))
            .filter((pl.col("col") == "interpost_within") & (pl.col("value") > 0))
            .select("value").collect())
    gaps = df["value"].to_numpy()
    print(f"source: {len(gaps):,} within gaps", file=sys.stderr)

    rng = np.random.default_rng(42)
    idx = rng.choice(len(gaps), size=min(args.n, len(gaps)), replace=False)
    sample = gaps[idx]

    out = HERE / "ecdfs" / "within_interpost_ecdf.txt"
    with open(out, "w") as f:
        f.writelines(f"{v:.6f}\n" for v in sample)

    print(f"→ {out} ({len(sample):,} lines, {out.stat().st_size/1e6:.1f} MB)",
          file=sys.stderr)
    for q in (0.25, 0.5, 0.75, 0.9, 0.99):
        print(f"  p{int(q*100):<3} {np.quantile(sample, q):>10.1f}s", file=sys.stderr)
    print(f"  max  {sample.max():>10.1f}s", file=sys.stderr)

    # check: reload must reproduce count and median
    back = np.loadtxt(out)
    assert len(back) == len(sample) and abs(np.median(back) - np.median(sample)) < 1e-3
    print("reload check OK", file=sys.stderr)


if __name__ == "__main__":
    main()
