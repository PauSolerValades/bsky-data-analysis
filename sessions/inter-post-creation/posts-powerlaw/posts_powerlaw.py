"""Power-law fit of posts-per-user ("a few users create most posts").

Uses the `powerlaw` package (Alstott et al. 2014, MLE alpha + KS-optimal
xmin, lognormal comparison via likelihood ratio) on the posts/user counts
from results/posts_per_day.parquet (all 1.15M users with >=1 post in the
8-day window). Also fits the de-botted variant (posts/day < 100) to check
robustness of the tail exponent.

Usage:
    uv run inter-post-creation/posts_powerlaw.py
"""

from pathlib import Path

import numpy as np
import polars as pl
import powerlaw

HERE = Path(__file__).resolve().parent


def fit(x, label):
    f = powerlaw.Fit(x, discrete=True, verbose=False)
    print(f"\n== {label} (n={len(x):,})")
    print(f"  xmin = {f.xmin}, alpha = {f.alpha:.3f} +/- {f.sigma:.3f}")
    print(f"  users >= xmin: {int((x >= f.xmin).sum()):,} "
          f"({(x >= f.xmin).mean():.3%}), holding {x[x >= f.xmin].sum() / x.sum():.1%} of posts")
    R, p = f.loglikelihood_ratio("power_law", "lognormal", normalized_ratio=True)
    print(f"  power-law vs lognormal: R = {R:.2f}, p = {p:.3g} "
          f"({'power-law favored' if R > 0 and p < 0.05 else 'lognormal favored' if R < 0 and p < 0.05 else 'undecided'})")
    return f


def main():
    df = pl.read_parquet(HERE / "results/posts_per_day.parquet")
    posts = df["posts"].to_numpy().astype(float)
    fit(posts, "all users")
    fit(df.filter(pl.col("posts_per_day") < 100)["posts"].to_numpy().astype(float),
        "excluding >=100 posts/day")


if __name__ == "__main__":
    main()
