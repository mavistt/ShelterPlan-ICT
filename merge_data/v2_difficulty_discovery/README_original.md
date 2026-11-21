# ShelterPlan Module B V2 difficulty discovery

This directory contains the V2 A-anchored search, fully synthetic stress tests, ablations, regression fixtures and reports. No output from the previous V1 run was overwritten. A source files, B production solver/API, and Module C were not edited. All planning outputs retain `DEMO_ONLY` or `FULLY_SYNTHETIC_STRESS` provenance as appropriate.

## Reproduce

From the Module B project root in PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = '.'
.\.venv\Scripts\python.exe merge_data\v2_difficulty_discovery\tools\analyze_real_structure.py
.\.venv\Scripts\python.exe merge_data\v2_difficulty_discovery\tools\run_grounded_search.py
.\.venv\Scripts\python.exe merge_data\v2_difficulty_discovery\tools\run_synthetic_stress.py
.\.venv\Scripts\python.exe merge_data\v2_difficulty_discovery\tools\generate_reports.py
.\.venv\Scripts\python.exe -m pytest tests\module_b -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest merge_data\v2_difficulty_discovery\tests -q -p no:cacheprovider
$env:PYTHONPYCACHEPREFIX = 'merge_data\v2_difficulty_discovery\runs\compile_cache'
.\.venv\Scripts\python.exe -m compileall -q backend
```

The grounded search fixes seed `20261114`, screens at most 100 option sets, and ran 9 of at most 30 full MIPs. Each full A MIP has a 10-second total solver limit. Synthetic stress uses seed `20261114`, 8/16/32/48/64 candidates, simple and competitive graphs, and 2/10-second solver limits.

## Contents

- `configs/v2_experiment_config.json`: fixed seeds, sizes, limits and integrity constraints.
- `reports/`: seven requested phase reports.
- `scenarios/`: generated candidate sets and fully synthetic fixtures.
- `oracles/`: structural metrics, search evidence, stage bounds, enumeration proofs, and ablations.
- `runs/`: per-case full results and traces, including verifier outputs.
- `all_cases.csv`: indexed grounded, synthetic, counterexample and ablation cases.
- `environment.json`: versions, seeds, tests and A-source hash evidence.
- `tests/`: V2 synthetic provenance and counterexample regression checks.

See `reports/07_FINAL_RECOMMENDATIONS.md` for explicit answers A–I. MIP outputs remain DEMO_ONLY; no operational recommendation or verified infrastructure fact is implied.
