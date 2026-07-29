"""Power-law vs lognormal comparison + lognormal fits.

For events per user, per day, and per hour: determines which distribution
is the better fit (LLR test), then fits a lognormal and computes thresholds.

Output:
  - fitting_comparison.tsv  (R, p, winner per distribution)
  - lognormal_parameters.tsv (μ, σ, median, thresholds per distribution)
  - fit_events_per_day.png   (CCDF + PDF, the one used for tourist threshold)
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import powerlaw
import seaborn as sns
from dotenv import load_dotenv
from scipy import stats

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env")
from running_locally.local_db import Where, get_connection as local_connect

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 9,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

OUT = Path(__file__).resolve().parent / "plots"
OUT.mkdir(exist_ok=True)

# ── Fetch ─────────────────────────────────────────────────────────────────

conn = local_connect(Where.from_env(), repo_root=str(REPO))

# Events per user
rows = conn.query("""
    SELECT cnt, COUNT(*) AS n_users
    FROM (SELECT did, COUNT(*) AS cnt FROM events GROUP BY did)
    GROUP BY cnt ORDER BY cnt
""")
data_user = np.array([float(v) for v, n in rows for _ in range(int(n))])
data_user = data_user[data_user > 0]

# Events per day
rows = conn.query("""
    SELECT total, days
    FROM (
        SELECT did, COUNT(*) AS total,
               COUNT(DISTINCT DATE(TO_TIMESTAMP(time_us / 1000000))) AS days
        FROM events GROUP BY did
    )
""")
data_day = np.array([float(t) / max(int(d), 1) for t, d in rows])
data_day = data_day[data_day > 0]

# Events per hour
rows = conn.query("""
    SELECT total, hours
    FROM (
        SELECT did, COUNT(*) AS total,
               COUNT(DISTINCT DATE_TRUNC('hour', TO_TIMESTAMP(time_us / 1000000))) AS hours
        FROM events GROUP BY did
    )
""")
data_hour = np.array([float(t) / max(int(h), 1) for t, h in rows])
data_hour = data_hour[data_hour > 0]

conn.close()

datasets = [
    (data_user, "events_per_user"),
    (data_day,  "events_per_day"),
    (data_hour, "events_per_hour"),
]

# ── Power-law vs lognormal (LLR test) ────────────────────────────────────

print("── Power-law vs lognormal (LLR test) ──")
print(f"  {'Distribution':<20s} {'R':>14s} {'p':>10s} {'winner':>12s}")
print(f"  {'-'*58}")

llr_results = []
for data, name in datasets:
    fit = powerlaw.Fit(data, discrete=True, xmin=1, verbose=False)
    R, p = fit.distribution_compare("power_law", "lognormal_positive")
    ln_better = R < 0 and p < 0.05
    pl_better = R > 0 and p < 0.05
    winner = "lognormal" if ln_better else ("powerlaw" if pl_better else "none")
    llr_results.append((name, R, p, winner))
    print(f"  {name:<20s} {R:>14,.1f} {p:>10.4f} {winner:>12s}")

# ── Lognormal fits ───────────────────────────────────────────────────────

print("\n── Lognormal fits ──")
print(f"  {'Distribution':<20s} {'μ':>8s} {'σ':>8s} {'median':>10s}  "
      f"{'μ-2σ':>10s} {'μ-σ':>10s} {'P10':>10s}")
print(f"  {'-'*80}")

ln_params = []
for data, name in datasets:
    shape, loc, scale = stats.lognorm.fit(data, floc=0)
    mu, sigma = np.log(scale), shape
    median = np.exp(mu)
    ln_params.append((name, mu, sigma, median))
    print(f"  {name:<20s} {mu:>8.4f} {sigma:>8.4f} {median:>10.1f}  "
          f"{np.exp(mu - 2*sigma):>10.1f} {np.exp(mu - sigma):>10.1f} "
          f"{np.percentile(data, 10):>10.1f}")

# ── TSVs ─────────────────────────────────────────────────────────────────

for tsv_name, header, rows in [
    ("fitting_comparison.tsv",
     "distribution\tLLR_R\tp_value\twinner\n",
     [(n, f"{R:,.1f}", f"{p:.4f}", w) for n, R, p, w in llr_results]),
    ("lognormal_parameters.tsv",
     "distribution\tmu\tsigma\tmedian\tmu_minus_2sigma\tmu_minus_sigma\tP10\n",
     [(n, f"{mu:.4f}", f"{sigma:.4f}", f"{md:.1f}",
       f"{np.exp(mu-2*sigma):.1f}", f"{np.exp(mu-sigma):.1f}",
       f"{np.percentile(d, 10):.1f}")
      for (n, mu, sigma, md), (d, _) in zip(ln_params, datasets)]),
]:
    tsv_path = OUT / tsv_name
    with open(tsv_path, "w") as f:
        f.write(header)
        for row in rows:
            f.write("\t".join(row) + "\n")
    print(f"  → saved {tsv_path}")

# ── Plot: CCDF + PDF for events per day ──────────────────────────────────

mu, sigma = ln_params[1][1], ln_params[1][2]
scale = np.exp(mu)
palette = sns.color_palette("colorblind")

fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

# CCDF
ax = axes[0]
sorted_d = np.sort(data_day)
ccdf = 1 - np.arange(len(sorted_d)) / len(sorted_d)
ax.step(sorted_d, ccdf, where="post", color=palette[0], linewidth=1.2, label="data")

x_fit = np.logspace(np.log10(data_day.min()), np.log10(data_day.max()), 200)
y_fit = 1 - stats.lognorm.cdf(x_fit, sigma, loc=0, scale=scale)
ax.plot(x_fit, y_fit, color=palette[1], linewidth=2,
        label=f"lognormal  μ={mu:.2f}, σ={sigma:.2f}")

for label, v in [("μ-2σ", np.exp(mu - 2*sigma)),
                  ("μ-σ", np.exp(mu - sigma)),
                  ("median", np.exp(mu))]:
    ax.axvline(v, color="red", linestyle=":", alpha=0.4, linewidth=0.8)
    ax.text(v * 1.05, 0.55, f"{label}\n{v:.1f}", fontsize=8, color="red", alpha=0.7)

ax.set_xscale("log"); ax.set_yscale("log")
ax.set_xlabel("Events per day")
ax.set_ylabel("P(Events/day ≥ x)")
ax.set_title("CCDF — events per day", fontsize=12, fontweight="bold")
ax.legend(fontsize=8)

# PDF
ax = axes[1]
lo, hi = np.log10(data_day.min()), np.log10(data_day.max())
bins = np.logspace(lo, hi, 60)
ax.hist(data_day, bins=bins, color=palette[0], alpha=0.6, edgecolor="white",
        linewidth=0.3, density=True, label="data")

pdf = stats.lognorm.pdf(x_fit, sigma, loc=0, scale=scale)
ax.plot(x_fit, pdf, color=palette[1], linewidth=2, label="lognormal fit")

for label, v in [("μ-2σ", np.exp(mu - 2*sigma)),
                  ("μ-σ", np.exp(mu - sigma)),
                  ("median", np.exp(mu))]:
    ax.axvline(v, color="red", linestyle=":", alpha=0.4, linewidth=0.8)

ax.set_xscale("log")
ax.set_xlabel("Events per day")
ax.set_ylabel("Density")
ax.set_title("PDF — events per day", fontsize=12, fontweight="bold")
ax.legend(fontsize=8)

fig.tight_layout()
path = OUT / "fit_events_per_day.png"
fig.savefig(path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"  → saved {path}")

print("\nDone.")
