#!/bin/bash
# Full-data production run: DBSCAN e300 ms2 → pau_db.sessions (+ sessions_users + analysis)
cd /home/psoler/firehose-analysis/sessions || exit 1

uv run table_creation/dbscan_cluster.py \
  --table-name sessions --epsilon 300 --min-samples 2 --summary || exit 1

uv run python - <<'EOF'
import sys
sys.path.insert(0, "table_creation")
from _core import get_connection, _execute, TBL_PREFIX

conn = get_connection()
_execute(conn, f"DROP TABLE IF EXISTS {TBL_PREFIX}sessions_users")
_execute(conn, f"""
    CREATE TABLE {TBL_PREFIX}sessions_users AS
    SELECT did,
           COUNT(*) AS n_sessions,
           SUM(is_singleton) AS n_singletons,
           SUM(duration_s) AS total_session_s,
           AVG(duration_s) AS mean_duration_s,
           PERCENTILE_APPROX(duration_s, 0.5) AS median_duration_s,
           MAX(duration_s) AS max_duration_s
    FROM {TBL_PREFIX}sessions
    GROUP BY did
""")
conn.close()
print("sessions_users created", file=sys.stderr)
EOF

d=hyperparameter/plots_new/final_sessions
for s in duration_analysis hist_session_length hist_session_gaps circadian; do
  uv run analysis/$s.py --table-name sessions --plot-dir $d
done
uv run analysis/fit_distribution.py --table-name sessions --column gap_s --plot-dir $d
uv run analysis/fit_distribution.py --table-name sessions --column duration_s --plot-dir $d

echo "ALL DONE"
