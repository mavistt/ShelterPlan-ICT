# ShelterPlan A/B/C/D + ShelterPlan-Eval integration run v1

This directory records an integration run on the composite-recovery UI bridge branch. It preserves the frozen 24-task evaluation set and distinguishes solver/API smoke evidence from scored Agent benchmark results.

## What is included

- `TASK_GT.json` is byte-identical to `reports/final_release/task_dataset.json` (SHA-256 `a2e2a846b903e738e6bdece6e9835ea8bd2b847eb515870dc2e3dfee9ef9ac4b`). `TASK_MANIFEST.csv` is an index; it does not alter the frozen GT.
- `b_structured/` contains the selected 8-task direct structured B pilot, its assertions, outcomes, and B execution traces. Result: PASS=6, UNSCORABLE=2, FAIL=0; the other 16 frozen tasks are NOT_RUN in this pilot.
- RuleLLM runtime artifacts are smoke checks only and are excluded from all Agent metrics.
- `traces/` keeps selected complete C JSONL traces, compressed B result records, and the B traceback for prior East failures. `screenshots/` captures the front-end evidence. `FRONTEND_VALIDATION.json` records passed paths and prior failure runs.

## Data and limits

The A v2 region data is `DEMO_ONLY`, with 0 shelters marked VERIFIED. Shiqi and East are enabled for demonstration; Xiaolan is disabled by the admission policy. DEMO_ONLY means data has not passed real-world field validation. `HYPOTHETICAL` options, costs, capacities, or user-supplied changes are planning assumptions and not verified facts. Do not present the demo as an operational recommendation.

The frozen GT remains the independent task definition. Oracle status is task-specific: only independently proven mathematical assertions count as an Oracle; `NOT_PROVEN` or `NOT_RUN` must not be replaced with the solver's answer. The B structured pilot reports the B solver and its available verification evidence, not a C Agent success rate.

## Reproduce on Windows PowerShell

From repository root, install the project Python dependencies and frontend lockfile dependencies (`python -m pip install -r requirements.txt`, then `cd frontend; npm ci`). Set Python explicitly if needed:

```powershell
$env:SHELTERPLAN_PYTHON = 'C:\path\to\python.exe'
..\scripts\start_integration_demo.ps1
```

Open <http://127.0.0.1:5173>. B health is <http://127.0.0.1:8000/healthz>; C health is <http://127.0.0.1:8080/healthz>. This start script uses deterministic RuleLLM smoke mode and does not call a paid or external LLM API. Stop the three owned local processes with `..\scripts\stop_integration_demo.ps1` from the frontend folder, or `scripts\stop_integration_demo.ps1` from repository root.

The B structured pilot is run with `python -B -m benchmark.shelterplan_eval.stage3_pilot --no-http --output-dir reports/integration/eval_run_v1/b_structured`. Selected A/Eval regressions use the command recorded in `RUN_MANIFEST.json` and `FRONTEND_VALIDATION.json`. Frontend checks run from `frontend/`: `npm run test`, `npm run typecheck`, `npm run build`, and `npm run e2e`.

Reproduction requires the exact A v2 files listed in `SOURCE_HASH_AUDIT.json` under `data/processed/regions/{shiqi,east_district,xiaolan}/`; those GIS files are not copied into this integration report. The E2E map reads the checked-in A v2 assets. No raw GIS duplicate is included here.

## Branch and reproducibility caveats

See `MERGE_PROVENANCE.md` for source commits. The Eval/Stage 3 artifacts are selectively integrated; B/C and frontend remain based on the UI bridge plus compositional harness source. The full historical Module B and C suites include failures caused by the legacy v1 `configs/pilot.yaml` fixture's source-manifest mismatch; see `FAILURE_ANALYSIS.md`. The currently supported demo path uses A v2. Frozen GT provenance fields are preserved exactly even where they record earlier source-admission state.

The four real-LLM comparison arms (Direct LLM, Flat Tools, Fixed Workflow, Composite + Harness) are `NOT_RUN`: no real LLM endpoint was configured, and no paid API was called. RuleLLM smoke results are not Agent performance evidence. Xiaolan remains disabled; no region status was upgraded.

The retained pytest logs include full failure stacks and final counters. Local checkout, temporary-user, and Python-environment paths are replaced with placeholders for portability and privacy.
