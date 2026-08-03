#!/usr/bin/env Rscript
## Step 4: fit distributions to the across-user parameter values.
##
## For each (col, family, parameter): MLE-fit candidate distributions to the
## per-user winning parameters (trustworthy subset n_obs >= 30), select by AIC.
## power_tail uses the canonical GPD params (xi, sigma) from step 3.
##
## Output: results/param_distributions.tsv  (col, family, param, n_users,
##         best_dist, aic, ad, dist_param, value)
##         results/param_correlations.tsv   (col, family, spearman_rho)

suppressMessages({library(data.table); library(fitdistrplus)})
source("distribution-fit/fit_lib.R")   # for gof_stats

MIN_OBS <- 30
CAND_POS <- c("exp", "gamma", "lnorm", "weibull")   # positive-valued params
CAND_ANY <- c("norm", CAND_POS)                     # meanlog / possibly xi<=0

best   <- fread("distribution-fit/results/best_per_user.tsv")
params <- fread("distribution-fit/results/best_params.tsv")
canon  <- fread("distribution-fit/results/power_tail_canonical.tsv")

best <- best[n_obs >= MIN_OBS]
pt <- merge(best[family == "power_tail", .(did, col, n_obs)], canon, by = c("did", "col"))
bp <- merge(best[, .(did, col, distribution, n_obs)],
            params[, .(did, col, distribution, param, value)],
            by = c("did", "col", "distribution"))

## Build the list of (col, family, param) -> values series
series <- list()
add <- function(col, family, param, values) {
  series[[length(series) + 1L]] <<- list(col=col, family=family, param=param, values=values)
}
for (cc in c("duration", "gap")) {
  sub <- bp[col == cc]
  for (fam in c("expon","gamma","lognorm","weibull_min","fisk")) {
    w <- sub[distribution == fam]
    if (nrow(w) == 0) next
    pw <- dcast(w, did + col ~ param, value.var = "value")
    for (p in setdiff(names(pw), c("did","col")))
      add(cc, fam, p, pw[[p]])
  }
  ptc <- pt[col == cc]
  if (nrow(ptc) > 0) {
    add(cc, "power_tail", "xi", ptc$xi)
    add(cc, "power_tail", "sigma", ptc$sigma)
  }
}

## Fit each series
fit_series <- function(values) {
  values <- values[is.finite(values)]
  cands <- if (all(values > 0)) CAND_POS else CAND_ANY
  fits <- list()
  for (d in cands) {
    f <- tryCatch(fitdist(values, d, keepdata = TRUE), error = function(e) NULL)
    if (!is.null(f) && !any(is.na(coef(f)))) fits[[d]] <- f
  }
  if (length(fits) == 0) return(NULL)
  aics <- sapply(fits, AIC)
  bf <- fits[[which.min(aics)]]
  list(dist = bf$distname, aic = AIC(bf), ad = gof_stats(values, bf)["ad"],
       coefs = coef(bf))
}

rows <- list()
for (s in series) {
  r <- fit_series(s$values)
  if (is.null(r)) next
  for (pn in names(r$coefs)) {
    rows[[length(rows) + 1L]] <- data.table(
      col = s$col, family = s$family, param = s$param, n_users = length(s$values),
      best_dist = r$dist, aic = r$aic, ad = r$ad,
      dist_param = pn, value = unname(r$coefs[pn]))
  }
}
out <- rbindlist(rows)
fwrite(out, "distribution-fit/results/param_distributions.tsv", sep = "\t")
cat(sprintf("wrote %d rows for %d series\n", nrow(out), length(series)))

## Spearman correlation between the two params of each 2-param family
cors <- list()
for (cc in c("duration", "gap")) {
  for (fam in c("gamma","lognorm","weibull_min","fisk")) {
    w <- dcast(bp[col == cc & distribution == fam], did ~ param, value.var = "value")
    if (nrow(w) > 10 && ncol(w) == 3)
      cors[[length(cors)+1L]] <- data.table(col=cc, family=fam,
        spearman_rho = cor(w[[2]], w[[3]], method = "spearman"))
  }
  ptc <- pt[col == cc]
  if (nrow(ptc) > 10)
    cors[[length(cors)+1L]] <- data.table(col=cc, family="power_tail",
      spearman_rho = cor(ptc$xi, ptc$sigma, method = "spearman"))
}
fwrite(rbindlist(cors), "distribution-fit/results/param_correlations.tsv", sep = "\t")
print(rbindlist(cors))
