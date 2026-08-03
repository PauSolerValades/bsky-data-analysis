#!/bin/bash
# Sequential fit driver: waits for each chunk's parquet, then fits with 64 cores.
cd /home/psoler/firehose-analysis/sessions/distribution-fit || exit 1
R=/home/psoler/.local/miniconda3/bin/Rscript
for k in 0 1 2 3 4 5 6 7 8 9; do
  while [ ! -f "data/chunk$k.parquet" ]; do sleep 30; done
  echo "=== fit chunk $k start $(date) ==="
  $R fit_chunk.R "$k" 64
  echo "=== fit chunk $k end $(date) ==="
done
echo "ALL FITS DONE"
