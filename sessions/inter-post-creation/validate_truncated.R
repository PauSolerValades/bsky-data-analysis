#!/usr/bin/env Rscript
## Truncation-regime validation for within-session inter-post fits.
##
## Within-session gaps are only observable when they fit inside a session:
## the observation process is x | x < T with T ~ session length (median 109s,
## p99 ~1700s). This mirrors that: simulate each family, keep draws < T,
## fit with the SAME untruncated candidates + AIC as the production pipeline,
## and see which families AIC confuses. The confusion matrix defines the
## resolution at which within-session composition can be honestly reported.
##
## Usage: Rscript inter-post-creation/validate_truncated.R [cores]

source("distribution-fit/fit_lib.R")
set.seed(42)

args <- commandArgs(trailingOnly = TRUE)
cores <- if (length(args) >= 1) as.integer(args[1]) else 64

PT <- c("pareto", "lomax", "genpareto")
to_family <- function(d) ifelse(d %in% PT, "power_tail", d)

GENS <- list(
  expon       = function(n) rexp(n, rate = 1 / 100),
  gamma       = function(n) rgamma(n, shape = 2, rate = 1 / 50),
  lognorm     = function(n) rlnorm(n, meanlog = 4.5, sdlog = 1),
  weibull_min = function(n) rweibull(n, shape = 0.7, scale = 100),
  fisk        = function(n) rllogis(n, shape = 2, scale = 100),
  power_tail  = function(n) rgpd(n, loc = 0, scale = 100, shape = 0.5)
)
CEILINGS <- c(60, 120, 300, 900, 3600)
NS <- c(30, 100)
REPS <- 30

trunc_sample <- function(gen, n, T) {
  out <- c()
  while (length(out) < n) {
    x <- gen(n)
    out <- c(out, x[x < T & x > 0])
  }
  out[seq_len(n)]
}

grid <- expand.grid(true = names(GENS), n = NS, T = CEILINGS, rep = seq_len(REPS),
                    stringsAsFactors = FALSE)
one <- function(i) {
  g <- grid[i, ]
  x <- trunc_sample(GENS[[g$true]], g$n, g$T)
  f <- fit_one(x)
  if (is.null(f)) return(NA_character_)
  to_family(f$gof$distribution[which.min(f$gof$aic)])
}

t0 <- Sys.time()
picked <- parallel::mclapply(seq_len(nrow(grid)), one, mc.cores = cores)
grid$picked <- unlist(picked)
cat(sprintf("(%.0fs for %d fits)\n", difftime(Sys.time(), t0, units = "secs"), nrow(grid)))

suppressMessages(library(data.table))
dt <- as.data.table(grid)
fams <- c("expon", "gamma", "lognorm", "weibull_min", "fisk", "power_tail")

## Headline: confusion at n = 100 across ceilings
for (nn in NS) {
  cat(sprintf("\n=== n = %d: AIC-picked family (rows = true), share of %d reps ===\n",
              nn, REPS * length(CEILINGS)))
  tab <- dt[n == nn & !is.na(picked),
            .(pct = round(100 * .N / (REPS * length(CEILINGS)), 1)),
            by = .(true, picked)]
  m <- matrix(0, length(fams), length(fams), dimnames = list(fams, fams))
  for (i in seq_len(nrow(tab))) m[tab$true[i], tab$picked[i]] <- tab$pct[i]
  print(m)
}

## Identifiability vs ceiling: % of reps where AIC recovers the true family
cat("\n=== % correct (family level) by ceiling and n ===\n")
acc <- dt[!is.na(picked), .(acc = round(100 * mean(picked == true), 1)),
          by = .(true, n, T)][order(true, n, T)]
print(dcast(acc, true + n ~ T, value.var = "acc"))
