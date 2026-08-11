"""ECDF of pooled within gaps vs the top pooled parametric fits."""

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from scipy import stats

sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,  # no latex binary on this box; same as other scripts here
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
})

HERE = __file__.rsplit("/", 1)[0]
d = np.load(f"{HERE}/results/pooled_fit.npz", allow_pickle=True)
x, names, params = d["x"], d["names"], d["params"]
DISTS = {"gamma": stats.gamma, "weibull_min": stats.weibull_min,
         "lomax": stats.lomax, "lognorm": stats.lognorm}

xs = np.sort(x)
ecdf = np.arange(1, len(xs) + 1) / len(xs)

fig, ax = plt.subplots(figsize=(6.5, 4))
ax.plot(xs, ecdf, color="black", lw=1.5, label="pooled ECDF")
grid = np.logspace(0, np.log10(xs.max()), 500)
for nm in ["gamma", "weibull_min", "lomax", "lognorm"]:
    p = params[list(names).index(nm)]
    ax.plot(grid, DISTS[nm].cdf(grid, *p), lw=1.2, label=nm)
ax.set_xscale("log")
ax.set_xlabel("within-session gap (s)")
ax.set_ylabel("CDF")
ax.legend()
fig.tight_layout()
fig.savefig(f"{HERE}/plots/pooled_fit_ecdf.png", dpi=200)
print("saved plots/pooled_fit_ecdf.png")
