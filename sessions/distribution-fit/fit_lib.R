## Shared fitting library — per-user distribution fits with fitdistrplus.
##
## Candidates (8): expon, gamma, lognorm, weibull_min, fisk (log-logistic),
## pareto_i (Pareto I, support x>theta), lomax (Pareto II, support x>0),
## genpareto (Pickands GPD, loc fixed at 0).
##
## GOF: gofstat KS / Cramer-von Mises / Anderson-Darling — proper statistics
## against the fitted CDF (no Monte Carlo, no distfit pdf-ordinate nonsense).
## Selection: AIC (complexity-penalized); AD as heavy-tail GOF descriptor.
## Gaps are shifted by -eps (300s) BEFORE reaching here (known truncation).

suppressMessages({
  library(fitdistrplus)
  library(actuar)   # llogis (fisk), pareto, pareto2 (lomax)
  library(evd)      # gpd (Pickands genpareto)
})

DISTS <- list(
  expon       = list(distr = "exp"),
  gamma       = list(distr = "gamma"),
  lognorm     = list(distr = "lnorm"),
  weibull_min = list(distr = "weibull"),
  fisk        = list(distr = "llogis",
                     start = function(x) list(shape = 1, scale = median(x))),
  pareto_i    = list(distr = "pareto1",
                     start = function(x) list(shape = 1.5, scale = min(x) * 0.9)),
  lomax       = list(distr = "pareto2",
                     start = function(x) list(shape = 2, scale = mean(x)),
                     fix   = function(x) list(min = 0)),
  genpareto   = list(distr = "gpd",
                     start = function(x) list(scale = sd(x), shape = 0.1),
                     fix   = function(x) list(loc = 0))
)

## Exact KS / CvM / AD of x against a fitdist object (closed form, any n).
## (gofstat errors on small n due to chisq binning — this replaces it.)
gof_stats <- function(x, f) {
  xs <- sort(x)
  n <- length(xs)
  pfun <- get(paste0("p", f$distname))
  cdf_args <- c(list(q = xs), as.list(f$estimate), f$fix.arg)
  F <- do.call(pfun, cdf_args)
  if (any(!is.finite(F))) return(c(ks = NA_real_, cvm = NA_real_, ad = NA_real_))
  Fc <- pmin(pmax(F, 1e-300), 1 - 1e-16)
  i <- seq_len(n)
  emp_hi <- i / n
  emp_lo <- (i - 1) / n
  ks <- max(abs(F - emp_lo), abs(F - emp_hi))
  cvm <- sum((F - (2 * i - 1) / (2 * n))^2) + 1 / (12 * n)
  ad <- -n - sum((2 * i - 1) * (log(Fc) + log(1 - rev(Fc)))) / n
  c(ks = ks, cvm = cvm, ad = ad)
}

## Fit all 8 distributions to x, return list(gof=df, params=df).
fit_one <- function(x) {
  n <- length(x)
  fits <- list()
  for (nm in names(DISTS)) {
    d <- DISTS[[nm]]
    f <- tryCatch(
      fitdist(x, d$distr,
              start = if (is.null(d$start)) NULL else d$start(x),
              fix.arg = if (is.null(d$fix)) NULL else d$fix(x),
              keepdata = TRUE),
      error = function(e) NULL)
    if (!is.null(f) && !any(is.na(coef(f)))) fits[[nm]] <- f
  }
  if (length(fits) == 0) return(NULL)

  stats <- t(sapply(fits, function(f) gof_stats(x, f)))
  gof_df <- data.frame(
    distribution = names(fits),
    n_obs = n,
    loglik = sapply(fits, function(f) f$loglik),
    aic = sapply(fits, AIC),
    bic = sapply(fits, BIC),
    ks = stats[, "ks"], cvm = stats[, "cvm"], ad = stats[, "ad"],
    row.names = NULL
  )

  params_df <- do.call(rbind, lapply(names(fits), function(nm) {
    cf <- coef(fits[[nm]])
    data.frame(distribution = nm, param = names(cf),
               value = as.numeric(cf), row.names = NULL)
  }))

  list(gof = gof_df, params = params_df)
}
