#!/bin/bash
cd /home/psoler/firehose-analysis/sessions || exit 1
for k in 0 1 2 3 4 5 6 7 8 9; do
  echo "=== dump chunk $k $(date) ==="
  uv run python -u distribution-fit/dump_data.py --chunk "$k"
done
echo "ALL DUMPS DONE"
