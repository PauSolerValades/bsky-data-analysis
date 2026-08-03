#!/usr/bin/env Rscript
## Step 5: parameter distribution histograms with fitted PDF overlay.
## One PNG per (col, family, param) — AGENTS.md thesis style (theme_minimal).
## Log10 x-axis (with correct Jacobian on the overlay) when values span >2
## orders of magnitude; linear otherwise.

suppressMessages({library(data.table); library(ggplot2)})
MIN_OBS <- 30

my_thesis_theme <- theme_minimal(base_size = 11) +
  theme(
    plot.title = element_text(size = 12),
    axis.title = element_text(size = 11),
    axis.text = element_text(size = 10),
    legend.text = element_text(size = 11)
  )

CB_FILL <- "#0072B2"   # Okabe-Ito blue
CB_LINE <- "#D55E00"   # Okabe-Ito vermillion

best   <- fread("distribution-fit/results/best_per_user.tsv")[n_obs >= MIN_OBS]
params <- fread("distribution-fit/results/best_params.tsv")
canon  <- fread("distribution-fit/results/power_tail_canonical.tsv")
fits   <- fread("distribution-fit/results/param_distributions.tsv")

## Rebuild series (same logic as step4)
pt <- merge(best[family == "power_tail", .(did, col)], canon, by = c("did", "col"))
bp <- merge(best[, .(did, col, distribution)],
            params[, .(did, col, distribution, param, value)],
            by = c("did", "col", "distribution"))

series <- list()
add <- function(col, family, param, values)
  series[[length(series) + 1L]] <<- list(col=col, family=family, param=param, values=values)
for (cc in c("duration", "gap")) {
  for (fam in c("expon","gamma","lognorm","weibull_min","fisk")) {
    w <- dcast(bp[col == cc & distribution == fam], did ~ param, value.var = "value")
    if (nrow(w) > 1) for (p in setdiff(names(w), "did")) add(cc, fam, p, w[[p]])
  }
  ptc <- pt[col == cc]
  if (nrow(ptc) > 0) { add(cc, "power_tail", "xi", ptc$xi); add(cc, "power_tail", "sigma", ptc$sigma) }
}

dpdf <- function(dist, pars, x) switch(dist,
  exp     = dexp(x, rate = pars["rate"]),
  gamma   = dgamma(x, shape = pars["shape"], rate = pars["rate"]),
  lnorm   = dlnorm(x, meanlog = pars["meanlog"], sdlog = pars["sdlog"]),
  weibull = dweibull(x, shape = pars["shape"], scale = pars["scale"]),
  norm    = dnorm(x, mean = pars["mean"], sd = pars["sd"]),
  stop("unknown dist"))

dir.create("distribution-fit/plots", showWarnings = FALSE)
for (s in series) {
  f <- fits[col == s$col & family == s$family & param == s$param]
  if (nrow(f) == 0) next
  pars <- setNames(f$value, f$dist_param)
  dist <- f$best_dist[1]
  values <- s$values[is.finite(s$values)]
  use_log <- all(values > 0) && (max(values) / max(min(values), .Machine$double.eps) > 100)

  # ponytail: clip x-range to central mass (degenerate fits stretch axes); bars outside range dropped
  q <- quantile(values, c(0.01, 0.99))
  values <- values[values >= q[1] & values <= q[2]]

  if (use_log) {
    lx <- log10(values)
    xs <- seq(min(lx), max(lx), length.out = 400)
    ys <- dpdf(dist, pars, 10^xs) * 10^xs * log(10)   # Jacobian
    g <- ggplot(data.frame(lx), aes(lx)) +
      geom_histogram(aes(y = after_stat(density)), bins = 60,
                     fill = CB_FILL, alpha = 0.55, color = NA) +
      geom_line(data = data.frame(xs, ys), aes(xs, ys), color = CB_LINE, linewidth = 0.9) +
      labs(x = sprintf("log10(%s)", s$param), y = "density")
  } else {
    xs <- seq(min(values), max(values), length.out = 400)
    ys <- dpdf(dist, pars, xs)
    g <- ggplot(data.frame(values), aes(values)) +
      geom_histogram(aes(y = after_stat(density)), bins = 60,
                     fill = CB_FILL, alpha = 0.55, color = NA) +
      geom_line(data = data.frame(xs, ys), aes(xs, ys), color = CB_LINE, linewidth = 0.9) +
      labs(x = s$param, y = "density")
  }
  par_str <- paste(sprintf("%s=%.3g", names(pars), pars), collapse = ", ")
  g <- g + labs(title = sprintf("%s / %s — %s ~ %s(%s)",
                                s$col, s$family, s$param, dist, par_str),
                caption = sprintf("n = %s users", format(length(values), big.mark = ","))) +
    my_thesis_theme
  ggsave(sprintf("distribution-fit/plots/param__%s_%s_%s.png", s$col, s$family, s$param),
         g, width = 7, height = 4.5, dpi = 150)
}
cat("plots written:", length(list.files("distribution-fit/plots", pattern = "png$")), "\n")
