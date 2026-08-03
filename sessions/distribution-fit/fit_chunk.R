#!/usr/bin/env Rscript
## Per-user fitting over one parquet chunk. Parallel via mclapply.
##
## Usage: Rscript fit_chunk.R <chunk> [cores]
## Input:  data/chunk{K}.parquet (did, col, value)  — gaps shifted by -300 here
## Output: results/gof__chunk{K}.tsv, results/params__chunk{K}.tsv

args <- commandArgs(trailingOnly = TRUE)
chunk <- as.integer(args[1])
cores <- if (length(args) >= 2) as.integer(args[2]) else 64
GAP_SHIFT <- 300

suppressMessages({library(arrow); library(data.table)})
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE)[1])), "fit_lib.R"))

df <- as.data.table(read_parquet(sprintf("data/chunk%d.parquet", chunk)))
df[col == "gap", value := value - GAP_SHIFT]   # known left-truncation at eps
df <- df[value > 0]

units <- split(df$value, paste(df$did, df$col, sep = "\r"))
cat(sprintf("chunk %d: %s units, %d cores\n", chunk, format(length(units), big.mark = ","), cores))

t0 <- Sys.time()
res <- parallel::mclapply(units, fit_one, mc.cores = cores)
cat(sprintf("fitted in %.0f min\n", as.numeric(difftime(Sys.time(), t0, units = "mins"))))

dir.create("results", showWarnings = FALSE)
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

fwrite(gof_all, sprintf("results/gof__chunk%d.tsv", chunk), sep = "\t")
fwrite(params_all, sprintf("results/params__chunk%d.tsv", chunk), sep = "\t")
cat(sprintf("wrote %s gof rows, %s param rows (%d units skipped)\n",
            format(nrow(gof_all), big.mark = ","),
            format(nrow(params_all), big.mark = ","), sum(!ok)))
