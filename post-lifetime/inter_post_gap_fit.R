#!/usr/bin/env Rscript
# =============================================================================
# Inter-post gap distribution fitting — Bluesky firehose
# =============================================================================
# For each user, fits power-law, exponential, log-normal, Weibull, and gamma
# distributions to their inter-post gaps (seconds between consecutive posts).
#
# Two gap types:
#   global          — all consecutive post timestamps per user
#   within_session  — only posts within the same session boundary
#
# Uses fitdistrplus for MLE + GoF and poweRlaw for KS-based power-law fitting
# with automatic xmin detection (Clauset et al. 2009).
#
# Input:  data/inter_post_gaps.csv
# Output: results/inter_post_gap_fits.csv  (per-user distribution fits)
#
# Usage:
#   Rscript post-lifetime/inter_post_gap_fit.R
#   Rscript post-lifetime/inter_post_gap_fit.R --sample 50000 --cores 8
# =============================================================================

suppressPackageStartupMessages({
  library(fitdistrplus)
  library(poweRlaw)
  library(tidyverse)
  library(data.table)
  library(parallel)
})

# ---------------------------------------------------------------------------
# CLI arguments
# ---------------------------------------------------------------------------
args <- commandArgs(trailingOnly = TRUE)

sample_n     <- 0L       # 0 = all users
min_points   <- 10L      # minimum data points per user to fit
cores        <- 4L       # parallel workers
gap_type     <- "both"   # "global", "within_session", or "both"
data_path    <- "data/inter_post_gaps.csv"
output_dir   <- "results"
significance <- 0.05

for (i in seq_along(args)) {
  if (args[i] == "--sample" && i < length(args)) {
    sample_n <- as.integer(args[i + 1])
  } else if (args[i] == "--min-points" && i < length(args)) {
    min_points <- as.integer(args[i + 1])
  } else if (args[i] == "--cores" && i < length(args)) {
    cores <- as.integer(args[i + 1])
  } else if (args[i] == "--data" && i < length(args)) {
    data_path <- args[i + 1]
  } else if (args[i] == "--output-dir" && i < length(args)) {
    output_dir <- args[i + 1]
  } else if (args[i] == "--gap-type" && i < length(args)) {
    gap_type <- args[i + 1]
  }
}

cat(sprintf("Configuration:\n"))
cat(sprintf("  sample:      %s\n", if (sample_n > 0) sample_n else "ALL"))
cat(sprintf("  min_points:  %d\n", min_points))
cat(sprintf("  cores:       %d\n", cores))
cat(sprintf("  gap_type:    %s\n", gap_type))
cat(sprintf("  data_path:   %s\n", data_path))
cat(sprintf("  output_dir:  %s\n", output_dir))

dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
cat("\n=== Loading data ===\n")
t0 <- Sys.time()

if (!file.exists(data_path)) {
  stop(sprintf("Data file not found: %s", data_path))
}

cat(sprintf("  Reading %s ...\n", data_path))
dt <- fread(data_path, showProgress = TRUE)
cat(sprintf("    → %s rows in %.0fs\n",
            format(nrow(dt), big.mark = ","),
            difftime(Sys.time(), t0, units = "secs")))

# Filter gap types
if (gap_type != "both") {
  dt <- dt[gap_type == gap_type]
  cat(sprintf("    Filtered to gap_type='%s': %s rows\n",
              gap_type, format(nrow(dt), big.mark = ",")))
}

# Remove zero/negative gaps
dt <- dt[gap_s > 0]

# Add gap_type_hours for easier interpretation later
dt[, gap_h := gap_s / 3600]

# Per-user summary
cat(sprintf("    %s unique DIDs\n", format(uniqueN(dt$did), big.mark = ",")))
cat(sprintf("    %s unique gap types\n", uniqueN(dt$gap_type)))

# Sample users if requested
if (sample_n > 0) {
  all_dids <- unique(dt$did)
  if (length(all_dids) > sample_n) {
    set.seed(42)
    selected <- sample(all_dids, sample_n)
    dt <- dt[did %in% selected]
    cat(sprintf("    Sampled %s users → %s rows\n",
                format(length(selected), big.mark = ","),
                format(nrow(dt), big.mark = ",")))
  }
}

# ---------------------------------------------------------------------------
# Prepare per-user data
# ---------------------------------------------------------------------------
cat("\n=== Preparing per-user data ===\n")

# Split by gap_type and did
build_user_data <- function(dt_sub, type_label) {
  users <- dt_sub[, .(
    gap_values = list(gap_s),
    n_gaps = .N
  ), by = did]
  users <- users[n_gaps >= min_points]
  cat(sprintf("  %s: %s users with ≥%d gaps\n",
              type_label, format(nrow(users), big.mark = ","), min_points))
  users
}

gap_types <- unique(dt$gap_type)
user_list <- list()
for (gt in gap_types) {
  label <- if (gt == "global") "Global" else "Within-session"
  user_list[[gt]] <- build_user_data(dt[gap_type == gt], label)
}

# ---------------------------------------------------------------------------
# Distribution fitting (per-user)
# ---------------------------------------------------------------------------
cat("\n=== Fitting distributions (per-user) ===\n")

# Fit all five candidates to a single user's vector
fit_one_user <- function(values, min_pts = min_points) {
  if (length(values) < min_pts) return(NULL)

  values <- values[values > 0]
  if (length(values) < min_pts) return(NULL)

  n <- length(values)

  # ---- Power-law with KS-based xmin ----
  pl <- tryCatch({
    pl_obj <- conpl$new(values)
    # Use a reasonable set of xmin candidates
    xmin_candidates <- unique(round(
      exp(seq(log(max(1, min(values))), log(max(values)), length.out = 50)), 0))
    if (length(xmin_candidates) < 2) return(NULL)
    xmin_est <- estimate_xmin(pl_obj, xmins = xmin_candidates)
    pl_obj$setXmin(xmin_est)
    pl_obj$setPars(estimate_pars(pl_obj))
    list(
      alpha    = pl_obj$pars,
      xmin     = pl_obj$xmin,
      logLik   = dist_ll(pl_obj),
      n_tail   = sum(values >= pl_obj$xmin),
      pl_obj   = pl_obj
    )
  }, error = function(e) NULL)

  if (is.null(pl) || pl$n_tail < 5) {
    # Not enough tail data for power-law; fit others on full data
    tail <- values
    pl <- NULL
  } else {
    tail <- values[values >= pl$xmin]
  }

  n_tail <- length(tail)

  # ---- MLE fits on tail data ----
  fit_exp  <- tryCatch(fitdist(tail, "exp"), error = function(e) NULL)
  fit_ln   <- tryCatch(fitdist(tail, "lnorm"), error = function(e) NULL)
  fit_w    <- tryCatch(fitdist(tail, "weibull"), error = function(e) NULL)
  fit_gam  <- tryCatch(fitdist(tail, "gamma"), error = function(e) NULL)

  fits <- list(
    powerlaw    = pl,
    exponential = fit_exp,
    lognormal   = fit_ln,
    weibull     = fit_w,
    gamma       = fit_gam
  )

  # ---- Vuong's test: powerlaw vs alternatives ----
  llr_tests <- list()
  if (!is.null(pl)) {
    for (alt_name in c("exponential", "lognormal")) {
      alt_fit <- fits[[alt_name]]
      if (!is.null(alt_fit)) {
        vuong <- tryCatch({
          if (alt_name == "exponential") {
            alt_obj <- conexp$new(tail)
          } else {
            alt_obj <- conlnorm$new(tail)
          }
          alt_obj$setXmin(pl$xmin)
          alt_obj$setPars(estimate_pars(alt_obj))
          comp <- compare_distributions(pl$pl_obj, alt_obj)
          c(R = comp$test_statistic, p = comp$p_two_sided)
        }, error = function(e) c(R = NA_real_, p = NA_real_))
        llr_tests[[alt_name]] <- vuong
      }
    }
  }

  # ---- AIC values ----
  aic_vals <- c(
    powerlaw    = if (!is.null(pl)) 2 * 2 - 2 * pl$logLik else NA_real_,
    exponential = if (!is.null(fit_exp)) fit_exp$aic else NA_real_,
    lognormal   = if (!is.null(fit_ln)) fit_ln$aic else NA_real_,
    weibull     = if (!is.null(fit_w)) fit_w$aic else NA_real_,
    gamma       = if (!is.null(fit_gam)) fit_gam$aic else NA_real_
  )

  # ---- Pick best distribution ----
  supported <- c()
  rejected  <- c()
  for (alt in names(llr_tests)) {
    if (!is.na(llr_tests[[alt]]["p"])) {
      if (llr_tests[[alt]]["R"] > 0 && llr_tests[[alt]]["p"] < significance)
        supported <- c(supported, alt)
      else if (llr_tests[[alt]]["R"] < 0 && llr_tests[[alt]]["p"] < significance)
        rejected <- c(rejected, alt)
    }
  }

  tested_alternatives <- intersect(names(llr_tests), names(aic_vals))
  if (length(supported) > 0 && length(rejected) == 0 &&
      all(tested_alternatives %in% supported)) {
    best <- "powerlaw"
  } else if (length(rejected) > 0) {
    best <- rejected[1]
  } else {
    best <- names(which.min(aic_vals[!is.na(aic_vals)]))
  }

  # ---- Extract parameters as flat list ----
  params <- list()
  if (!is.null(pl)) {
    params$pl_alpha    <- unname(pl$alpha)
    params$pl_xmin     <- pl$xmin
    params$pl_xmin_h   <- pl$xmin / 3600
    params$pl_ntail    <- pl$n_tail
  }
  for (dist_name in c("exponential", "lognormal", "weibull", "gamma")) {
    f <- fits[[dist_name]]
    if (!is.null(f)) {
      est <- f$estimate
      for (j in seq_along(est)) {
        pname <- names(est)[j]
        params[[paste0(dist_name, "_", pname)]] <- unname(est[j])
      }
    }
  }

  # LLR test results
  for (alt in names(llr_tests)) {
    params[[paste0("llr_", alt, "_R")]] <- unname(llr_tests[[alt]]["R"])
    params[[paste0("llr_", alt, "_p")]] <- unname(llr_tests[[alt]]["p"])
  }

  # AICs
  for (dn in names(aic_vals)) {
    params[[paste0("aic_", dn)]] <- aic_vals[dn]
  }

  params$best   <- best
  params$n      <- n
  params$n_tail <- n_tail

  params
}

# Process all users for a given gap_type (parallel over users)
process_users <- function(users_df, label, ncores = cores) {
  cat(sprintf("\n  %s: fitting %s users ...\n", label,
              format(nrow(users_df), big.mark = ",")))
  t0 <- Sys.time()

  gap_list <- users_df$gap_values

  cat(sprintf("    Fitting inter-post gaps (%d cores) ...\n", ncores))
  gap_fits <- mclapply(gap_list, fit_one_user, mc.cores = ncores)

  # Build results table
  results <- data.table(
    did          = users_df$did,
    n_gaps       = users_df$n_gaps,
    gap_best     = sapply(gap_fits, function(x) if (is.list(x)) x$best else NA_character_)
  )

  # Flatten parameter columns
  param_names <- c("pl_alpha", "pl_xmin", "pl_xmin_h", "pl_ntail",
                   "exponential_rate", "lognormal_meanlog", "lognormal_sdlog",
                   "weibull_shape", "weibull_scale",
                   "gamma_shape", "gamma_rate",
                   "llr_exponential_R", "llr_exponential_p",
                   "llr_lognormal_R", "llr_lognormal_p",
                   "aic_powerlaw", "aic_exponential", "aic_lognormal",
                   "aic_weibull", "aic_gamma",
                   "best", "n", "n_tail")

  for (pname in param_names) {
    col_name <- paste0("gap_", pname)
    vals <- sapply(gap_fits, function(x) {
      if (is.list(x) && !is.null(x[[pname]])) x[[pname]] else NA_real_
    })
    if (pname == "best") vals <- sapply(gap_fits, function(x) {
      if (is.list(x) && !is.null(x$best)) x$best else NA_character_
    })
    set(results, j = col_name, value = vals)
  }

  results[, gap_type := label]

  elapsed <- difftime(Sys.time(), t0, units = "secs")
  n_fitted <- sum(!is.na(results$gap_best))
  cat(sprintf("    Done in %.0fs — %s fits\n", elapsed, format(n_fitted, big.mark = ",")))
  results
}

all_results <- rbindlist(
  Map(function(users, label) {
    process_users(users, if (label == "global") "global" else "within_session")
  }, user_list, names(user_list)),
  fill = TRUE
)

# ---------------------------------------------------------------------------
# Save per-user results
# ---------------------------------------------------------------------------
fout <- file.path(output_dir, "inter_post_gap_fits.csv")
fwrite(all_results, fout)
cat(sprintf("\nPer-user results saved to %s (%s users)\n",
            fout, format(nrow(all_results), big.mark = ",")))

# ---------------------------------------------------------------------------
# Summary: distribution breakdown
# ---------------------------------------------------------------------------
cat("\n============================================================\n")
cat("  INTER-POST GAP DISTRIBUTION FITTING SUMMARY\n")
cat("============================================================\n")

for (gt in names(user_list)) {
  sub <- all_results[gap_type == gt]
  type_label <- if (gt == "global") "Global (all posts)" else "Within-session"

  cat(sprintf("\n  Gap type: %s  (%s users)\n", type_label,
              format(nrow(sub), big.mark = ",")))

  # Best distribution counts
  counts <- sub[!is.na(gap_best), .N, by = gap_best][order(-N)]
  total   <- nrow(sub)
  fitted  <- sum(counts$N)

  cat(sprintf("\n    Best distribution  (%s/%s users with fits):\n",
              format(fitted, big.mark = ","), format(total, big.mark = ",")))
  cat(sprintf("    %-16s %8s  %6s\n", "Distribution", "Users", "%"))
  cat(sprintf("    %-16s %8s  %6s\n", "------------", "-------", "-----"))

  for (i in seq_len(nrow(counts))) {
    cat(sprintf("    %-16s %8s  %5.1f%%\n",
                counts$gap_best[i],
                format(counts$N[i], big.mark = ","),
                100 * counts$N[i] / total))
  }

  # Parameter summary for top distributions
  for (dist_name in intersect(unique(counts$gap_best),
                              c("powerlaw", "exponential", "lognormal",
                                "weibull", "gamma"))) {
    sub2 <- sub[gap_best == dist_name]

    if (dist_name == "powerlaw") {
      pc_alpha <- "gap_pl_alpha"
      pc_xmin  <- "gap_pl_xmin_h"
      if (all(c(pc_alpha, pc_xmin) %in% names(sub2))) {
        a <- sub2[[pc_alpha]]; a <- a[!is.na(a)]
        x <- sub2[[pc_xmin]]; x <- x[!is.na(x)]
        if (length(a) > 0) {
          cat(sprintf("\n      power-law params (n=%s users):\n",
                      format(length(a), big.mark = ",")))
          cat(sprintf("        alpha: μ=%.2f  med=%.2f  σ=%.2f  [%.2f, %.2f]\n",
                      mean(a), median(a), sd(a), min(a), max(a)))
          cat(sprintf("        xmin:  μ=%.2f h  med=%.2f h  σ=%.2f h\n",
                      mean(x), median(x), sd(x)))
        }
      }
    } else if (dist_name == "exponential") {
      pc <- "gap_exponential_rate"
      if (pc %in% names(sub2)) {
        r <- sub2[[pc]]; r <- r[!is.na(r)]
        if (length(r) > 0) {
          cat(sprintf("\n      exponential params (n=%s users):\n",
                      format(length(r), big.mark = ",")))
          cat(sprintf("        rate (1/mean): μ=%.6f  med=%.6f\n",
                      mean(r), median(r)))
          cat(sprintf("        → mean gap: μ=%.1fs (%.1f min)  med=%.1fs (%.1f min)\n",
                      mean(1/r), mean(1/r)/60, median(1/r), median(1/r)/60))
        }
      }
    } else if (dist_name == "lognormal") {
      pc_mu <- "gap_lognormal_meanlog"
      pc_sd <- "gap_lognormal_sdlog"
      if (all(c(pc_mu, pc_sd) %in% names(sub2))) {
        mu <- sub2[[pc_mu]]; mu <- mu[!is.na(mu)]
        sd <- sub2[[pc_sd]]; sd <- sd[!is.na(sd)]
        if (length(mu) > 0) {
          cat(sprintf("\n      lognormal params (n=%s users):\n",
                      format(length(mu), big.mark = ",")))
          cat(sprintf("        meanlog: μ=%.2f  med=%.2f  σ=%.2f\n",
                      mean(mu), median(mu), sd(mu)))
          cat(sprintf("        sdlog:   μ=%.2f  med=%.2f  σ=%.2f\n",
                      mean(sd), median(sd), sd(sd)))
          # Back-transform
          med_gap <- exp(median(mu))
          cat(sprintf("        → median gap: %.1fs (%.1f min)\n",
                      med_gap, med_gap / 60))
        }
      }
    } else if (dist_name == "weibull") {
      pc_k <- "gap_weibull_shape"
      pc_l <- "gap_weibull_scale"
      if (all(c(pc_k, pc_l) %in% names(sub2))) {
        k <- sub2[[pc_k]]; k <- k[!is.na(k)]
        l <- sub2[[pc_l]]; l <- l[!is.na(l)]
        if (length(k) > 0) {
          p_lt1 <- 100 * mean(k < 1, na.rm = TRUE)
          cat(sprintf("\n      Weibull params (n=%s users):\n",
                      format(length(k), big.mark = ",")))
          cat(sprintf("        shape: μ=%.3f  med=%.3f  σ=%.3f\n",
                      mean(k), median(k), sd(k)))
          cat(sprintf("        scale: μ=%.1fs  med=%.1fs\n",
                      mean(l), median(l)))
          cat(sprintf("        k < 1 (decreasing hazard): %.0f%%\n", p_lt1))
        }
      }
    } else if (dist_name == "gamma") {
      pc_k <- "gap_gamma_shape"
      pc_r <- "gap_gamma_rate"
      if (all(c(pc_k, pc_r) %in% names(sub2))) {
        k <- sub2[[pc_k]]; k <- k[!is.na(k)]
        r <- sub2[[pc_r]]; r <- r[!is.na(r)]
        if (length(k) > 0) {
          cat(sprintf("\n      gamma params (n=%s users):\n",
                      format(length(k), big.mark = ",")))
          cat(sprintf("        shape: μ=%.2f  med=%.2f  σ=%.2f\n",
                      mean(k), median(k), sd(k)))
          cat(sprintf("        rate:  μ=%.6f  med=%.6f\n",
                      mean(r), median(r)))
        }
      }
    }
  }

  # Gap statistics per gap_best
  cat(sprintf("\n    Gap size by best-fit distribution:\n"))
  cat(sprintf("    %-16s %10s %10s %10s\n",
              "Distribution", "Median gap", "Mean gap", "P90 gap"))
  cat(sprintf("    %-16s %10s %10s %10s\n",
              "------------", "----------", "----------", "----------"))
  for (dist_name in unique(counts$gap_best)) {
    sub2 <- sub[gap_best == dist_name]
    dids <- sub2$did
    ug <- dt[did %in% dids & gap_type == gt]
    if (nrow(ug) > 0) {
      cat(sprintf("    %-16s %8.1fm  %8.1fm  %8.1fm\n",
                  dist_name,
                  median(ug$gap_s) / 60,
                  mean(ug$gap_s) / 60,
                  quantile(ug$gap_s, 0.90) / 60))
    }
  }
}

# ---------------------------------------------------------------------------
# Overall conclusion
# ---------------------------------------------------------------------------
cat("\n============================================================\n")
cat("  DONE\n")
cat("============================================================\n")
cat(sprintf("  Full results: %s\n", file.path(output_dir, "inter_post_gap_fits.csv")))
cat(sprintf("  Data source:  %s\n", data_path))
