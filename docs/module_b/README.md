# ShelterPlan Module B

Module B turns a constrained planning request into a capacity-aware shelter assignment, optional budgeted improvement plan, scenario comparison, or disruption stress test. It owns the numerical planning engine, finite Skill Graph, independent verifier, result evidence, and local API. Module A owns input data and release acceptance; Module C owns conversation, AgentArts orchestration, and the user interface.

## Current state

- The configured source `zhongshan_pilot_v0.1` loads 7,965 demand nodes, 24 shelters, and 191,160 OD edges with matching processed-file hashes.
- The current source status is `DEMO_ONLY`. The 24 shelter capacities and locations are unverified. Never describe `PASS_DEMO` as real-world validation.
- Sparse SciPy LP, bounded budget MIP, diagnostics, stress tests, scenario comparison, and the independent verifier are implemented.
- The synthetic benchmark has 40 deterministic cases across family-separated dev/validation/test splits. External field truth is not available; GT-C is `NOT_VALIDATED`.
- The configured A source passed API assess, stress-test, and comparison smoke checks. AgentArts itself has not been connected or evaluated.

See [PROGRESS.md](PROGRESS.md), [A_DATA_INTEGRATION_REPORT.md](A_DATA_INTEGRATION_REPORT.md), and [REAL_DATA_EXPERIMENTS.md](REAL_DATA_EXPERIMENTS.md) for run evidence and limits.

## Install and run on Windows

Use Python 3.12 in a clean environment. From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-b.txt
.venv\Scripts\python.exe -m backend.cli inspect-data --config configs/pilot.yaml
```

To run the local API:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

The API is local development software. It has no authentication, HTTPS, rate limiting, or public deployment setup. For a new checkout, obtain an approved A release through Module A's process and place the files exactly as described by `configs/pilot.yaml`; do not copy data from another developer's local output folder.

## Tests and evaluation

```powershell
.venv\Scripts\python.exe -m compileall -q backend tests\module_b
.venv\Scripts\python.exe -m pytest tests\module_b -q -p no:cacheprovider
.venv\Scripts\python.exe -m pytest tests\test_gis_data.py -q -p no:cacheprovider
.venv\Scripts\python.exe -m backend.cli demo --fixture tests\module_b\fixtures\toy_case.json
.venv\Scripts\python.exe -m backend.evaluation.api_smoke
.venv\Scripts\python.exe -m backend.benchmark --split all --output outputs\module_b\bench\all
```

The A GIS tests require the A geospatial dependencies and processed A files. They are read-only. The benchmark cases are synthetic and do not substitute for field validation.

## Handoff index

- [API_HANDOFF_TO_C.md](API_HANDOFF_TO_C.md): actual FastAPI routes, schemas, asynchronous jobs, statuses, and examples.
- [AGENTARTS_GUIDE.md](AGENTARTS_GUIDE.md): recommended coarse-grained AgentArts integration.
- [ARCHITECTURE.md](ARCHITECTURE.md): LP/MIP, Skill Graph, cache, versioning, verifier, and evidence.
- [BENCHMARK_EVALUATION.md](BENCHMARK_EVALUATION.md): 40-case design and measurements.
- [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md): current data and deployment limits.
- [GITHUB_HANDOFF.md](GITHUB_HANDOFF.md): exact Module B upload allowlist, exclusions, and review steps.
- [`examples/module_b/`](../../examples/module_b/README.md): request JSON files validated against the current Pydantic contract.

The shortest C integration path is: read `/planning/capabilities`, submit one `TaskRequest` to `/planning/tasks`, poll `/planning/jobs/{job_id}`, then fetch `/planning/results/{result_id}`. Keep the returned `data_status`, warnings, solver status, and verifier status visible to downstream reasoning.
