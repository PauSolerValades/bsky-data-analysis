#!/bin/bash
cd /home/psoler/firehose-analysis/sessions || exit 1
R=/home/psoler/.local/miniconda3/bin/Rscript
for k in 1 2 3 4 5 6 7 8 9; do
  echo "=== dump chunk $k $(date) ==="
  uv run python -u inter-post-creation/dump.py --chunk "$k"
  echo "=== fit chunk $k $(date) ==="
  $R distribution-fit/fit_chunk.R "$k" 64 inter-post-creation/data inter-post-creation/2-parametric-fits/results 2>/dev/null
done
echo "ALL DONE $(date)"
