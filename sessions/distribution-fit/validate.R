#!/usr/bin/env Rscript
## Validation: simulate from each known family, fit, check AIC recovery.
## Must pass before the production run.

source("distribution-fit/fit_lib.R")
set.seed(42)

n <- 100
reps <- 50
generators <- list(
  expon       = function(n) rexp(n, rate = 0.01),
  gamma       = function(n) rgamma(n, shape = 2, rate = 0.01),
  lognorm     = function(n) rlnorm(n, meanlog = 4, sdlog = 1),
  weibull_min = function(n) rweibull(n, shape = 1.5, scale = 200),
  fisk        = function(n) rllogis(n, shape = 2, scale = 100),
  pareto      = function(n) rpareto(n, shape = 3, scale = 50),
  lomax       = function(n) rpareto2(n, min = 0, shape = 3, scale = 100),
  genpareto   = function(n) rgpd(n, loc = 0, scale = 100, shape = 0.3)
)

confusion <- matrix(0L, length(generators), length(DISTS),
                    dimnames = list(names(generators), names(DISTS)))
t0 <- Sys.time()
for (true_nm in names(generators)) {
  for (r in seq_len(reps)) {
    x <- generators[[true_nm]](n)
    f <- fit_one(x)
    if (is.null(f)) next
    best <- f$gof$distribution[which.min(f$gof$aic)]
    confusion[true_nm, best] <- confusion[true_nm, best] + 1L
  }
}
cat(sprintf("(%.0fs for %d fits)\n", as.numeric(difftime(Sys.time(), t0, units = "secs")),
            length(generators) * reps))
cat("\nAIC-selected distribution (rows = true family):\n")
print(confusion)
