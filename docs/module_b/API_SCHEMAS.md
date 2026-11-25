# API schemas and local examples

For the complete Module C handoff with endpoint details and live smoke response examples, see [API_HANDOFF_TO_C.md](API_HANDOFF_TO_C.md).

Local development base URL: `http://127.0.0.1:8000`. There is no deployed public endpoint.

## Request

`POST /planning/tasks` accepts a strict JSON `TaskRequest`:

```json
{
  "task_id": "demo-001",
  "fixture_id": "toy_case",
  "data_version": "synthetic_toy_v1",
  "analysis_mode": "demo",
  "task_type": "optimize",
  "changes": {"budget_units": 10},
  "improvement_options": [],
  "proposed_skill_plan": null,
  "idempotency_key": "demo-001"
}
```

`fixture_id` is optional. The two checked-in synthetic fixtures are `toy_case` and `mixed_bottlenecks`. When omitted, the service loads the configured `configs/pilot.yaml` A source. In this checkout, the source is present and passes integrity/schema checks, but A7 is `DEMO_ONLY`; results cannot be promoted to verified real planning.

Clients cannot submit filesystem paths or URLs. `scenario` may replace `changes`; supported changes are demand ratio, walk threshold, budget, and disabled shelter IDs. Supported task types are `assess`, `optimize`, `diagnose_intervene`, `stress_test`, `compare`, `sensitivity`, and `explain_result`.

`proposed_skill_plan.nodes[]` accepts only `node_id`, a registered `skill_id`, `skill_version`, and `depends_on[]`. Requests cannot provide code, SQL, shell commands, imports, or arbitrary branch expressions.

## Responses

Task submission returns HTTP 202 with `job_id`, `task_id`, `status`, `execution_trace_id`, and `skill_graph_version`. Poll `GET /planning/jobs/{job_id}`. On success, fetch `GET /planning/results/{result_id}` for the full result, certificate, trace, and evidence.

Results include scenario/data versions, data status, graph version, trace ID, solver and verification statuses, warnings, compact summary, artifact references, and assignment/evidence. Stable errors include `DATA_UNAVAILABLE`, `DATA_INVALID`, `SCENARIO_INVALID`, `INVALID_SKILL_DAG`, `CANDIDATE_INVALID`, `VERSION_MISMATCH`, `RESULT_NOT_FOUND`, and `INTERNAL_ERROR`.

## Local verification

```powershell
.venv\Scripts\python.exe -m backend.cli inspect-data --config configs/pilot.yaml
.venv\Scripts\python.exe -m backend.cli demo --fixture tests/module_b/fixtures/toy_case.json
.venv\Scripts\python.exe -m backend.evaluation.api_smoke
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

The API smoke uses the configured A source and sequentially verifies `/planning/tasks`, `/planning/stress-tests`, and `/planning/comparisons`. Run `5ba22373-87cd-4ae9-9d4a-336209912677` succeeded for all three with `PASS_DEMO`. The local service has no authentication, HTTPS, rate limiting, or public deployment configuration; those require the deployment owner.
