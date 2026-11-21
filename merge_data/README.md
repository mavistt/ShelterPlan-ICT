# ShelterPlan V1/V2/V3 Benchmark Assets

This upload keeps benchmark definitions and selected evidence under `merge_data/`. Large run dumps, duplicate assignment payloads, the C source workspace, local state and caches are excluded. Original V1 and V2 reports are copied unchanged; V3 task results and traces are historical records and are not recomputed during upload.

## V1 — GIS-grounded generation and baseline

V1 creates 26 overlay scenarios (A/B/C/D groups) from Module A-derived shelter, demand and travel data. The retained inputs are candidate JSON, `SCENARIO_MANIFEST.json`, generator configuration, source manifest, all 26 V1 GT files, the GIS structure audit, and the original V1 reports plus `CASE_SUMMARY.csv`. The large denormalized `run_summary.json` and node-level per-case outputs are excluded; a compact exact Greedy/MIP summary with selected IDs and the original summary SHA-256 is at `gt/v1_greedy_mip_summary.json`.

## V2 — hard instances and controlled stress

V2 reuses the same A GIS graph and creates hypothetical investment options. It preserves the complete `REAL-000-TARGETED-TRAP` and `REAL-081` case JSONs, including Greedy/MIP outputs, independent Oracle evidence and verifier rows; the selected 8-item fully synthetic Greedy trap; the synthetic stress Oracle over 8–64 candidates; all fixtures, ablation outputs, screening records, seeds/configuration and original reports.

The original 53.5 MB aggregate phase-B JSON repeated node assignments and verifier rows also stored in per-run cases. It is represented by `v2_difficulty_discovery/oracles/phase_b_grounded_search_summary.json`, which keeps all 100 screening records, nine optimized-case metrics and Oracle fields, and SHA-256 digests for omitted large assignment/check arrays. The two important real GIS cases remain full and unchanged. The seven non-target full assignment dumps are not included.

## V3 — B/C Harness history

V3 preserves the 18-task frozen `TASK_GT.json`, `TASK_MANIFEST.csv`, task traces, Harness JSONL, triage traces, reports, cached-graph observation and the reviewed isolated-C patch diff. Historical result counts remain 15 PASS, 1 FAIL, 2 NOT_RUN. T04 is the real explanation-output failure. No `workspaces/module_c`, B result payload directory, cached session store or copied A data is uploaded.

The V3 run did not record the exact C Git commit. A redacted file-level hash manifest is in `v3_agent_integration/reports/C_SOURCE_PROVENANCE.json`; the historical runs used an isolated C snapshot plus local fixes. The current remote `feature/module-c` was `9d69cbfffb5e9330b095ef993fe110ab7b287c34` when checked on 2026-11-21, but this is not asserted to be the historical snapshot. Replaying with another C revision is a new run, not an exact rerun of V3.

## A data and status labels

No A data are copied into `merge_data`. The B branch contains the processed source files used by the experiments: `configs/pilot.yaml`, `data/manifests/pilot_manifest.json`, `data/processed/demand_nodes.geojson`, `data/processed/shelters_geocoded.geojson`, `data/processed/travel_matrix.parquet`, and `data/processed/a7_quality_summary.json`. The population raster `data/raw/chn_pop_2025_CN_100m_R2025A_v1.tif` is only an LFS pointer in the recorded checkout (134 bytes versus 922,251,437 expected); rebuilding the upstream A preprocessing from raw raster data therefore needs the authorized LFS object. The retained processed inputs suffice only when their hashes match the manifest. The `feature/module-b` base checked for this upload currently has two mismatches: `data/processed/demand_nodes.geojson` is 2,641,754 bytes while the manifest requires 2,633,782, and `data/processed/a7_quality_summary.json` is 4,586 bytes while the manifest requires 4,457. The pilot configuration, shelter GeoJSON and travel matrix match. V1 GIS generation and V2 GIS search therefore fail the manifest gate on this base until the matching A v1 processed artifacts are supplied. This upload does not replace Module A files.

All A-grounded outputs are `DEMO_ONLY`: the pilot shelter inventory, capacities, road network and results are not real-world verified or operational guidance. `HYPOTHETICAL` marks assumed project costs and capacity increases. Fully synthetic fixtures are labeled `FULLY_SYNTHETIC_STRESS` and are not GIS evidence.

## Ground truth and comparison

The independent V2 GT enumerates feasible candidate subsets and solves maximum flow with integer-scaled capacities using NetworkX Dinitz, independent of Module B's SciPy/HiGHS MIP. It records the scale and conservative rounding-error bound. Greedy and MIP receive the same A graph, hypothetical options, eligibility rules and budget; comparisons report service objective, selected options, solver/certificate status and the independent Oracle result. Do not treat a B result as its own independent GT.

## Dependencies and checks

Use the Module B project root and install `requirements-b.txt`; the experiment environment recorded Python 3.12.11, NumPy 1.26.4, SciPy 1.16.2, NetworkX 3.x, PyArrow 18.1.0 and PyYAML. Pytest is included in `requirements-b.txt`. Do not install a different solver or silently widen the public TaskRequest 12-candidate limit. Synthetic internal stress at 16–64 candidates is a separate internal benchmark.

```powershell
python -m pytest merge_data/tests/test_grounded_experiment.py -q -p no:cacheprovider
python -m pytest merge_data/v2_difficulty_discovery/tests -q -p no:cacheprovider
python -m compileall -q backend/evaluation merge_data
```

To regenerate V1/V2 outputs and reports, use a **disposable worktree** of this benchmark branch; the historical generator/report scripts write to fixed `merge_data` paths and can replace reports, scenario manifests and Oracle summaries inside the worktree. From the Module B repository root:

```powershell
python merge_data/tools/grounded_scenario_generator.py
python merge_data/tools/grounded_experiment_runner.py --preflight --run-id preflight-<unique-id>
python merge_data/tools/grounded_experiment_runner.py --run-id full-<unique-id>
python merge_data/v2_difficulty_discovery/tools/analyze_real_structure.py
python merge_data/v2_difficulty_discovery/tools/run_grounded_search.py
python merge_data/v2_difficulty_discovery/tools/run_synthetic_stress.py
python merge_data/v2_difficulty_discovery/tools/generate_reports.py
```

V1/V2 generation, solver and regression tests do not depend on Module C. V3 stored traces can be read without C; replaying the V3 API/Harness needs a separate Module C checkout and its historical patch state. Set `SHELTERPLAN_MODULE_C_PATH` to that checkout before running `v3_agent_integration/tools/run_v3_suite.py`. The uploaded runner accepts a separate C path; it does not bundle or alter C. The V3 C regression result (62 passed) is a recorded historical result; current C/MCP tests are not claimed by this upload. V3 A/B/Harness calls use the B code from the benchmark branch, while exact historical C replay is unavailable until the original C commit/snapshot is identified.

## What is directly reproducible

- **Directly reproducible now:** V2 synthetic regression and Oracle checks; all retained historical JSON/CSV and traces can be read and validated. Runtime timings may vary.
- **Reproducible after A-data correction:** V1 generation/solver tests and V2 GIS search/ablation require the processed A files to match `pilot_manifest.json`; the current `feature/module-b` files fail that gate for demand nodes and the A7 quality summary. No A file was changed here.
- **Historical result readable, full raw dump intentionally omitted:** all V1 reports and per-case GT; V2 screening summary for all nine completed real-GIS cases, with full result payloads retained for REAL-000 and REAL-081; compact V1 case metrics. Full assignment arrays for non-target cases are not archived here.
- **C-dependent historical results:** V3 reports/traces are directly inspectable. Exact V3 replay is not guaranteed because the exact C commit was not recorded and the patched isolated source workspace is excluded. A compatible separate C checkout is required for new C-dependent runs.

`merge_data/.gitattributes` disables line-ending conversion here so the archived byte hashes remain stable across Windows/Linux checkouts. See `UPLOAD_MANIFEST.csv` and `EXCLUDED_ASSETS.csv` for relative paths, byte sizes, SHA-256, and curation decisions. No secrets, `.env`, raw A duplicate, C source workspace, `__pycache__`, `.pytest_cache`, or V3 B result cache is included.
