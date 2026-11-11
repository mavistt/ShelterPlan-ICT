#!/usr/bin/env bash
# Rebuild derived GIS outputs from the frozen, documented project inputs.
# Does not call Amap or overwrite source/review workbooks.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

for f in \
  data/raw/chn_pop_2025_CN_100m_R2025A_v1.tif \
  configs/pilot_analysis_area.geojson \
  data/processed/demand_nodes.geojson \
  data/processed/shelters_geocoded.geojson \
  data/interim/shelters_geocoding_candidates.csv; do
  test -s "$f" || { echo "Missing frozen input: $f" >&2; exit 2; }
done

run() {
  echo "[$(date -Is)] $*"
  python -u "$@"
}
run scripts/04_clip_population.py
run scripts/05_build_walk_graph.py
run scripts/06_build_travel_matrix.py
run scripts/07_validate_outputs.py
if [[ -s data/raw/portal_shelters_batch_1.xls && -s data/raw/portal_shelters_batch_2.xls ]]; then
  run scripts/12_reconcile_portal_batches.py
else
  echo "[$(date -Is)] Portal batch reconciliation skipped; save both official batch XLS files to data/raw/ to enable it."
fi
run scripts/10_a7_quality_audit.py
run scripts/11_snap_time_sensitivity.py
run scripts/08_build_manifest.py
echo "[$(date -Is)] Stage A demo GIS rebuild complete"
