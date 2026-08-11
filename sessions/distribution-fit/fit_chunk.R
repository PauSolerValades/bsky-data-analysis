#!/usr/bin/env Rscript
## Per-user fitting over one parquet chunk. Parallel via mclapply.
##
## Usage: Rscript fit_chunk.R <chunk> [cores] [data_dir] [out_dir]
## Input:  <data_dir>/chunk{K}.parquet (did, col, value) — col "gap" shifted -300
## Output: <out_dir>/gof__chunk{K}.tsv, <out_dir>/params__chunk{K}.tsv

args <- commandArgs(trailingOnly = TRUE)
chunk <- as.integer(args[1])
cores <- if (length(args) >= 2) as.integer(args[2]) else 64
data_dir <- if (length(args) >= 3) args[3] else "data"
out_dir <- if (length(args) >= 4) args[4] else "results"
GAP_SHIFT <- 300  # ponytail: only col == "gap" is shifted; interpost cols pass through

suppressMessages({library(arrow); library(data.table)})
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE)[1])), "fit_lib.R"))

df <- as.data.table(read_parquet(sprintf("%s/chunk%d.parquet", data_dir, chunk)))
df[col == "gap", value := value - GAP_SHIFT]   # gaps > eps by construction; shift to support (0, Inf)
df <- df[value > 0]

units <- split(df$value, paste(df$did, df$col, sep = "\r"))
cat(sprintf("chunk %d: %s units, %d cores\n", chunk, format(length(units), big.mark = ","), cores))

t0 <- Sys.time()
res <- parallel::mclapply(units, fit_one, mc.cores = cores)
cat(sprintf("fitted in %.0f min\n", as.numeric(difftime(Sys.time(), t0, units = "mins"))))

dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
keys <- names(res)
ok <- !sapply(res, is.null)
res <- res[ok]; keys <- keys[ok]
dk <- do.call(rbind, strsplit(keys, "\r", fixed = TRUE))

gof_all <- rbindlist(lapply(seq_along(res), function(i) {
  cbind(did = dk[i, 1], col = dk[i, 2], res[[i]]$gof)
}))
params_all <- rbindlist(lapply(seq_along(res), function(i) {
  cbind(did = dk[i, 1], col = dk[i, 2], res[[i]]$params)
}))

fwrite(gof_all, sprintf("%s/gof__chunk%d.tsv", out_dir, chunk), sep = "\t")
fwrite(params_all, sprintf("%s/params__chunk%d.tsv", out_dir, chunk), sep = "\t")
cat(sprintf("wrote %s gof rows, %s param rows (%d units skipped)\n",
            format(nrow(gof_all), big.mark = ","),
            format(nrow(params_all), big.mark = ","), sum(!ok)))
