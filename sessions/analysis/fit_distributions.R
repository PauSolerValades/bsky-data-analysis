#!/usr/bin/env Rscript
# =============================================================================
# Session distribution fitting — Bluesky firehose
# =============================================================================
# For each user, fits power-law, exponential, log-normal, Weibull, and gamma
# distributions to:
#   1. Session durations (duration_s)
#   2. Inter-session gaps (next_session_start - session_end)
#
# Uses fitdistrplus for MLE + GoF and poweRlaw for KS-based power-law fitting
# with automatic xmin detection (Clauset et al. 2009).
#
# Compares session types: sessions_all (all events incl. likes) vs
# sessions_engagement (engaged events, no likes) — both clustered via Tukey IQR.
#
# Usage:
#   Rscript fit_distributions.R
#   Rscript fit_distributions.R --sample 50000 --cores 8 --tables sessions_all,sessions_engagement --data-dir data_new --output-dir results_new
# =============================================================================

suppressPackageStartupMessages({
  library(fitdistrplus)
  library(poweRlaw)
  library(tidyverse)
  library(broom)
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
data_dir     <- "data"
output_dir     <- "sessions/analysis/results"
tables       <- c("sessions_all", "sessions_engagement")
significance <- 0.05

for (i in seq_along(args)) {
  if (args[i] == "--sample" && i < length(args)) {
    sample_n <- as.integer(args[i + 1])
  } else if (args[i] == "--min-points" && i < length(args)) {
    min_points <- as.integer(args[i + 1])
  } else if (args[i] == "--cores" && i < length(args)) {
    cores <- as.integer(args[i + 1])
  } else if (args[i] == "--data-dir" && i < length(args)) {
    data_dir <- args[i + 1]
  } else if (args[i] == "--output-dir" && i < length(args)) {
    output_dir <- args[i + 1]
  } else if (args[i] == "--tables" && i < length(args)) {
    tables <- strsplit(args[i + 1], ",")[[1]]
  }
}

cat(sprintf("Configuration:\n"))
cat(sprintf("  sample:    %s\n", if (sample_n > 0) sample_n else "ALL"))
cat(sprintf("  min_points: %d\n", min_points))
cat(sprintf("  cores:      %d\n", cores))
cat(sprintf("  data_dir:   %s\n", data_dir))
cat(sprintf("  output_dir: %s\n", output_dir))

dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
cat("\n=== Loading data ===\n")

load_table <- function(tbl_name) {
  fname <- file.path(data_dir, paste0(tbl_name, ".csv"))
  if (!file.exists(fname)) {
    # Try sample variant
    fname <- file.path(data_dir, paste0(tbl_name, "_sample", sample_n, ".csv"))
  }
  if (!file.exists(fname)) stop(sprintf("File not found: %s", fname))

  cat(sprintf("  Reading %s ...\n", fname))
  t0 <- Sys.time()
  dt <- fread(fname, showProgress = TRUE)
  cat(sprintf("    → %s rows in %.0fs\n",
              format(nrow(dt), big.mark = ","),
              difftime(Sys.time(), t0, units = "secs")))

  # Compute inter-session gaps (seconds)
  dt[, gap_s := (next_session_start - session_end) / 1e6]

  # Filter: only positive durations and gaps
  dt <- dt[duration_s > 0 | gap_s > 0]

  # If sampling users (not rows), pick distinct DIDs
  if (sample_n > 0) {
    all_dids <- unique(dt$did)
    if (length(all_dids) > sample_n) {
      set.seed(42)
      selected <- sample(all_dids, sample_n)
      dt <- dt[did %in% selected]
    }
  }

  cat(sprintf("    → %s users, %s sessions\n",
              format(uniqueN(dt$did), big.mark = ","),
              format(nrow(dt), big.mark = ",")))
  dt
}

dt_list <- setNames(lapply(tables, load_table), tables)

# ---------------------------------------------------------------------------
# Per-user data preparation
# ---------------------------------------------------------------------------
cat("\n=== Preparing per-user data ===\n")

prepare_user_data <- function(dt) {
  # Per-user: collect duration vectors and gap vectors
  user_dur <- dt[duration_s > 0,
                 .(dur_values = list(duration_s)),
                 by = did]
  user_gap <- dt[gap_s > 0,
                 .(gap_values = list(gap_s)),
                 by = did]

  # Merge
  users <- merge(user_dur, user_gap, by = "did", all = TRUE)
  users[, n_dur := sapply(dur_values, length)]
  users[, n_gap := sapply(gap_values, length)]

  # Filter to users with enough data
  users <- users[n_dur >= min_points | n_gap >= min_points]
  users
}

user_list <- lapply(dt_list, prepare_user_data)
for (nm in names(user_list)) {
  cat(sprintf("  %s: %s users with ≥%d data points\n",
              nm, format(nrow(user_list[[nm]]), big.mark = ","), min_points))
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
    xmin_est <- estimate_xmin(pl_obj, xmins = unique(round(
      exp(seq(log(min(values)), log(max(values)), length.out = 50)), 0)))
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
          # Build alternative poweRlaw object for comparison
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
    params$pl_alpha <- unname(pl$alpha)
    params$pl_xmin  <- pl$xmin
    params$pl_ntail  <- pl$n_tail
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

  params$best <- best
  params$n    <- n
  params$n_tail <- n_tail

  params
}

# Process all users for a table (parallel over users)
process_users <- function(users_df, label, ncores = cores) {
  cat(sprintf("\n  %s: fitting %s users ...\n", label,
              format(nrow(users_df), big.mark = ",")))
  t0 <- Sys.time()

  # Prepare list of user vectors for durations
  dur_list <- users_df$dur_values
  gap_list <- users_df$gap_values

  cat(sprintf("    Fitting durations (%d cores) ...\n", ncores))
  dur_fits <- mclapply(dur_list, fit_one_user, mc.cores = ncores)

  cat(sprintf("    Fitting gaps (%d cores) ...\n", ncores))
  gap_fits <- mclapply(gap_list, fit_one_user, mc.cores = ncores)

  # Build results table
  results <- data.table(
    did          = users_df$did,
    n_sessions   = users_df$n_dur,
    n_gaps       = users_df$n_gap,
    dur_best     = sapply(dur_fits, function(x) if (is.list(x)) x$best else NA_character_),
    gap_best     = sapply(gap_fits, function(x) if (is.list(x)) x$best else NA_character_)
  )

  # Flatten parameter columns
  for (prefix in c("dur_", "gap_")) {
    fits <- if (prefix == "dur_") dur_fits else gap_fits
    param_names <- c("pl_alpha", "pl_xmin", "pl_ntail",
                     "exponential_rate", "lognormal_meanlog", "lognormal_sdlog",
                     "weibull_shape", "weibull_scale",
                     "gamma_shape", "gamma_rate",
                     "llr_exponential_R", "llr_exponential_p",
                     "llr_lognormal_R", "llr_lognormal_p",
                     "aic_powerlaw", "aic_exponential", "aic_lognormal",
                     "aic_weibull", "aic_gamma",
                     "best", "n", "n_tail")
    for (pname in param_names) {
      col_name <- paste0(prefix, pname)
      vals <- sapply(fits, function(x) {
        if (is.list(x) && !is.null(x[[pname]])) x[[pname]] else NA_real_
      })
      if (pname == "best") vals <- sapply(fits, function(x) {
        if (is.list(x) && !is.null(x$best)) x$best else NA_character_
      })
      set(results, j = col_name, value = vals)
    }
  }

  results[, source_table := label]

  elapsed <- difftime(Sys.time(), t0, units = "secs")
  n_fitted_dur <- sum(!is.na(results$dur_best))
  n_fitted_gap <- sum(!is.na(results$gap_best))
  cat(sprintf("    Done in %.0fs — %s duration fits, %s gap fits\n",
              elapsed,
              format(n_fitted_dur, big.mark = ","),
              format(n_fitted_gap, big.mark = ",")))
  results
}

all_results <- rbindlist(
  Map(process_users, user_list, names(user_list)),
  fill = TRUE
)

# Save per-user results
fout <- file.path(output_dir, "distribution_fit_results.csv")
fwrite(all_results, fout)
cat(sprintf("\nPer-user results saved to %s (%s users)\n",
            fout, format(nrow(all_results), big.mark = ",")))

# ---------------------------------------------------------------------------
# Summary: distribution breakdown
# ---------------------------------------------------------------------------
cat("\n============================================================\n")
cat("  DISTRIBUTION FITTING SUMMARY\n")
cat("============================================================\n")

for (tbl in names(user_list)) {
  sub <- all_results[source_table == tbl]

  cat(sprintf("\n  Table: %s  (%s users)\n", tbl,
              format(nrow(sub), big.mark = ",")))

  for (quantity in c("dur", "gap")) {
    best_col <- paste0(quantity, "_best")
    qlabel <- if (quantity == "dur") "Session duration" else "Inter-session gap"

    counts <- sub[!is.na(get(best_col)), .N, by = best_col][order(-N)]
    total   <- nrow(sub)
    fitted  <- sum(counts$N)

    cat(sprintf("\n    %s distribution  (%s/%s users with fits):\n",
                qlabel, format(fitted, big.mark = ","), format(total, big.mark = ",")))
    cat(sprintf("    %-16s %8s  %6s\n", "Distribution", "Users", "%"))
    cat(sprintf("    %-16s %8s  %6s\n", "------------", "-------", "-----"))

    for (i in seq_len(nrow(counts))) {
      cat(sprintf("    %-16s %8s  %5.1f%%\n",
                  counts[[best_col]][i],
                  format(counts$N[i], big.mark = ","),
                  100 * counts$N[i] / total))
    }

    # Parameter summary for top distributions
    for (dist_name in intersect(unique(counts[[best_col]]),
                                c("powerlaw", "exponential", "lognormal",
                                  "weibull", "gamma"))) {
      sub2 <- sub[get(best_col) == dist_name]

      # Parameter columns for this distribution
      if (dist_name == "powerlaw") {
        pc <- c(paste0(quantity, "_pl_alpha"), paste0(quantity, "_pl_xmin"))
        if (all(pc %in% names(sub2))) {
          a <- sub2[[pc[1]]]; a <- a[!is.na(a)]
          x <- sub2[[pc[2]]]; x <- x[!is.na(x)]
          if (length(a) > 0) {
            cat(sprintf("\n      powerlaw params (n=%s):\n", format(length(a), big.mark = ",")))
            cat(sprintf("        alpha: μ=%.2f  med=%.2f  σ=%.2f\n",
                        mean(a), median(a), sd(a)))
            cat(sprintf("        xmin:  μ=%.1f  med=%.1f  σ=%.1f\n",
                        mean(x), median(x), sd(x)))
          }
        }
      } else if (dist_name == "exponential") {
        pc <- paste0(quantity, "_exponential_rate")
        if (pc %in% names(sub2)) {
          r <- sub2[[pc]]; r <- r[!is.na(r)]
          if (length(r) > 0) {
            cat(sprintf("\n      exponential params (n=%s):\n", format(length(r), big.mark = ",")))
            cat(sprintf("        rate (1/scale): μ=%.4f  med=%.4f  σ=%.4f\n",
                        mean(r), median(r), sd(r)))
            cat(sprintf("        → mean interval: μ=%.1fs  med=%.1fs\n",
                        mean(1/r), median(1/r)))
          }
        }
      } else if (dist_name == "lognormal") {
        pc_mu <- paste0(quantity, "_lognormal_meanlog")
        pc_sd <- paste0(quantity, "_lognormal_sdlog")
        if (all(c(pc_mu, pc_sd) %in% names(sub2))) {
          mu <- sub2[[pc_mu]]; mu <- mu[!is.na(mu)]
          sd <- sub2[[pc_sd]]; sd <- sd[!is.na(sd)]
          if (length(mu) > 0) {
            cat(sprintf("\n      lognormal params (n=%s):\n", format(length(mu), big.mark = ",")))
            cat(sprintf("        meanlog: μ=%.2f  med=%.2f  σ=%.2f\n",
                        mean(mu), median(mu), sd(mu)))
            cat(sprintf("        sdlog:   μ=%.2f  med=%.2f  σ=%.2f\n",
                        mean(sd), median(sd), sd(sd)))
          }
        }
      } else if (dist_name == "weibull") {
        pc_k <- paste0(quantity, "_weibull_shape")
        pc_l <- paste0(quantity, "_weibull_scale")
        if (all(c(pc_k, pc_l) %in% names(sub2))) {
          k <- sub2[[pc_k]]; k <- k[!is.na(k)]
          l <- sub2[[pc_l]]; l <- l[!is.na(l)]
          if (length(k) > 0) {
            cat(sprintf("\n      Weibull params (n=%s):\n", format(length(k), big.mark = ",")))
            cat(sprintf("        shape: μ=%.2f  med=%.2f  σ=%.2f\n",
                        mean(k), median(k), sd(k)))
            cat(sprintf("        scale: μ=%.1f  med=%.1f  σ=%.1f\n",
                        mean(l), median(l), sd(l)))
            # k < 1 → decreasing hazard (sessions more likely to end early)
            # k > 1 → increasing hazard
            p_lt1 <- 100 * mean(k < 1, na.rm = TRUE)
            cat(sprintf("        k<1 (decr. hazard): %.0f%%\n", p_lt1))
          }
        }
      } else if (dist_name == "gamma") {
        pc_k <- paste0(quantity, "_gamma_shape")
        pc_r <- paste0(quantity, "_gamma_rate")
        if (all(c(pc_k, pc_r) %in% names(sub2))) {
          k <- sub2[[pc_k]]; k <- k[!is.na(k)]
          r <- sub2[[pc_r]]; r <- r[!is.na(r)]
          if (length(k) > 0) {
            cat(sprintf("\n      gamma params (n=%s):\n", format(length(k), big.mark = ",")))
            cat(sprintf("        shape: μ=%.2f  med=%.2f  σ=%.2f\n",
                        mean(k), median(k), sd(k)))
            cat(sprintf("        rate:  μ=%.4f  med=%.4f  σ=%.4f\n",
                        mean(r), median(r), sd(r)))
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Overall conclusion
# ---------------------------------------------------------------------------
cat("\n============================================================\n")
cat("  DONE\n")
cat("============================================================\n")
cat(sprintf("  Full results: %s\n", file.path(output_dir, "distribution_fit_results.csv")))
