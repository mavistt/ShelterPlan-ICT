# Module B API handoff to Module C

This handoff describes the FastAPI routes in `backend/app.py` and `backend/api/planning.py` and the strict Pydantic contract in `backend/models.py`. Start with [the Module B README](README.md). A configured-source request currently returns `data_status=DEMO_ONLY`; `PASS_DEMO` is not real-pilot acceptance.

## Start the local service

From the repository root:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Default local base URL is `http://127.0.0.1:8000`. OpenAPI is at `/openapi.json` and Swagger UI at `/docs`. The service is not deployed and has no authentication, HTTPS, or rate limiting.

## Endpoints

| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/health` | Local process health | `200`, `deployed:false` |
| GET | `/planning/capabilities` | Registered tasks, Skills, composites, solver bounds | `200` |
| POST | `/planning/tasks` | Submit assess, optimize, diagnose/intervene, sensitivity, or explain task | `202` |
| POST | `/planning/stress-tests` | Submit a `stress_test` task | `202` |
| POST | `/planning/comparisons` | Submit a `compare` task | `202` |
| GET | `/planning/jobs/{job_id}` | Poll in-process job state | `200` |
| GET | `/planning/results/{result_id}` | Fetch full result, certificate, trace, and evidence | `200` |

For the three specialized POST routes, `task_type` must match the endpoint. Use `/planning/tasks` for other task types.

## Request contract

Every POST body is a `TaskRequest`. Only `task_type` is required; the other fields are optional unless the selected task needs them.

| Field | Type | Meaning |
|---|---|---|
| `task_id` | string, max 128 | Caller-chosen request ID; generated if omitted |
| `task_type` | enum | `assess`, `optimize`, `diagnose_intervene`, `stress_test`, `compare`, `sensitivity`, `explain_result` |
| `analysis_mode` | `demo` or `verified` | Defaults to `demo`; `verified` is rejected for unaccepted A or synthetic data |
| `fixture_id` | string or null | Optional `toy_case` or `mixed_bottlenecks`; omit to load the configured A source |
| `data_version` | string or null | Optional expected source version; mismatch is rejected |
| `scenario_id` | string or null | Optional scenario identity |
| `scenario` | `PlanningScenario` or null | Full scenario override; must use the selected source's `data_version` |
| `parent_scenario_id` | string or null | Optional parent snapshot ID for a scenario fork |
| `changes` | object | Allowlisted changes: demand ratio, walk threshold, budget, disabled shelter IDs |
| `parameters` | object | Task parameters; see below |
| `improvement_options` | array, max 12 | Explicit budget options using `ImprovementOption` |
| `proposed_skill_plan` | `ProposedSkillPlan` or null | Optional registered DAG proposal |
| `result_id` | string or null | Required by `explain_result` |
| `idempotency_key` | string, max 128 | Reuse only when retrying the exact same logical submission |

The JSON body is limited to 1 MiB. `parameters` supports `budget_units` for optimize/diagnose, `disabled_shelter_ids` for stress tests, `scenarios` for compare, and `parameter` plus `values` for sensitivity. Compare accepts 1–12 scenario overrides. MIP requests through `TaskRequest` accept at most 12 options, even though the solver's internal model limit is 64.

Each `ImprovementOption` requires `option_id`, `shelter_id`, `action`, `delta_capacity_persons`, `cost_units`, `eligible`, `source_status`, `hypothetical`, `human_confirmed`, and `combination_policy` (`standalone`, `stackable`, or `exclusive_group`). Exclusive-group options also require `exclusive_group`. Unconfirmed options must be marked `hypothetical:true`. Cost units are assumptions unless separately validated.

`PlanningScenario` supports `scenario_id`, `parent_scenario_id`, `data_version`, `analysis_mode`, `population_demand_ratio` (0–1), `max_walk_time_min` (>0), optional `budget_units`, `disabled_shelter_ids`, and `changes`.

## Asynchronous job and result

The POST response is HTTP 202 and contains `job_id`, `task_id`, `status`, `execution_trace_id`, `skill_graph_version`, and `idempotent_replay`. The initial status is usually `QUEUED` or `RUNNING`; poll `GET /planning/jobs/{job_id}` until `SUCCEEDED` or `FAILED`.

Successful jobs add `result_id`, `result_summary`, and `verification_status`. `job_id` identifies the API job; `result_id` identifies the persisted result document; `scenario_id` identifies the immutable normalized scenario snapshot; `execution_trace_id` joins the B trace to C's AgentArts trace. Job/idempotency state is in process memory and resets when the service process restarts; persisted result JSON remains under `outputs/module_b/runs/`.

The result document includes `data_version`, `data_status`, `analysis_mode`, `scenario_id`, `parent_scenario_id`, `scenario_hash`, `solver_status`, `verification_status`, `certificate`, `warnings`, `result_summary`, `result_detail`, `provenance`, and per-skill `trace` records. A `DEMO_ONLY` source can return `PASS_DEMO` only. `PASS_VERIFIED` requires an accepted A release.

### Solver statuses

- `OPTIMAL`: the requested objective stages completed with optimality proven.
- `FEASIBLE`: a feasible incumbent exists, but optimality was not proven before the time limit; inspect `optimality_proven` and any `optimality_gap`.
- `SOLVER_TIMEOUT`: no feasible incumbent was available. The asynchronous job fails with a structured error; do not treat this as a plan.
- `MODEL_SIZE_LIMIT`, `CANDIDATE_INVALID`, and `SCENARIO_INVALID`: correct the request and submit a new logical task.

Verifier statuses are `PASS_DEMO`, `PASS_VERIFIED`, `BLOCKED`, or `FAIL`. Never interpret `PASS_DEMO` as field verification or permission to publish operational guidance.

## Error handling and retry

- Invalid Pydantic body or route/task mismatch: HTTP 422 before job submission.
- `DATA_UNAVAILABLE` and `SCENARIO_INVALID`: HTTP 409 when raised synchronously; the same planning error inside an async job appears in `job.error` with `error_code`, `details`, `message`, `retryable`, and `required_user_action`.
- Other `PlanningError` responses: HTTP 422.
- Unknown job/result: HTTP 404 (`JOB_NOT_FOUND` / `RESULT_NOT_FOUND`).
- More than 100 active jobs: HTTP 429 (`JOB_LIMIT_REACHED`, retryable).
- Body over 1 MiB: HTTP 413 (`REQUEST_TOO_LARGE`).
- Internal async exception: job state `FAILED`, error code `INTERNAL_ERROR`.

If `retryable` is true, retry the same request with the same idempotency key while the service process remains active. For corrected inputs or a new scenario, generate a new `task_id` and `idempotency_key`; reusing a key returns the existing job rather than recalculating it.

## Complete request and response examples

The following requests and completed job/result excerpts were captured from the real configured-source API smoke `5ba22373-87cd-4ae9-9d4a-336209912677`. IDs and measurements are real; the data status remains `DEMO_ONLY`.

### 1. Current assessment

Request to `POST /planning/tasks`:

```json
{
  "analysis_mode": "demo",
  "task_id": "real-api-assess-5ba22373-87cd-4ae9-9d4a-336209912677",
  "task_type": "assess",
  "idempotency_key": "5ba22373-87cd-4ae9-9d4a-336209912677:assess"
}
```

Completed `GET /planning/jobs/{job_id}` response:

```json
{
  "job_id": "a7705b62-a300-4078-b02d-ad95dafda748",
  "task_id": "real-api-assess-5ba22373-87cd-4ae9-9d4a-336209912677",
  "status": "SUCCEEDED",
  "execution_trace_id": "9d098466-4b82-4ae4-9c37-93cb396bd216",
  "skill_graph_version": "module-b-skillgraph-0.1.0",
  "result_id": "b473accf-1943-4c8b-b45a-5d04c4f36ee8",
  "result_summary": {
    "served_demand": 6456.103026929056,
    "total_demand": 110205.74056775225,
    "weighted_walk_time_min": 9.037615807901064,
    "optimized_served_demand": null,
    "diagnosed_unserved_nodes": 7634
  },
  "verification_status": "PASS_DEMO"
}
```

The corresponding result has `data_version="zhongshan_pilot_v0.1"`, `data_status="DEMO_ONLY"`, and `solver_status="OPTIMAL"`.

### 2. Close one shelter for a stress test

Request to `POST /planning/stress-tests`:

```json
{
  "analysis_mode": "demo",
  "task_id": "real-api-stress-5ba22373-87cd-4ae9-9d4a-336209912677",
  "task_type": "stress_test",
  "parameters": {"disabled_shelter_ids": ["zs_1b4df7920b"]},
  "idempotency_key": "5ba22373-87cd-4ae9-9d4a-336209912677:stress"
}
```

Completed job response excerpt:

```json
{
  "job_id": "5ebc32ee-d1a5-48b7-9e49-f831453ac65e",
  "task_id": "real-api-stress-5ba22373-87cd-4ae9-9d4a-336209912677",
  "status": "SUCCEEDED",
  "execution_trace_id": "521788e3-6d5b-401c-b351-6b22e134e559",
  "skill_graph_version": "module-b-skillgraph-0.1.0",
  "result_id": "06f1e7f1-153a-47c2-8f44-7d7c6eb6d170",
  "verification_status": "PASS_DEMO"
}
```

The result's `result_detail.stress.fixed_plan_stress.assignment.served_demand` is `3325.1452896108144`; its `changes.disabled_shelter_ids` is `['zs_1b4df7920b']`.

### 3. Compare two demand ratios

Request to `POST /planning/comparisons`:

```json
{
  "analysis_mode": "demo",
  "task_id": "real-api-compare-5ba22373-87cd-4ae9-9d4a-336209912677",
  "task_type": "compare",
  "parameters": {
    "scenarios": [
      {"population_demand_ratio": 0.1},
      {"population_demand_ratio": 0.3}
    ]
  },
  "idempotency_key": "5ba22373-87cd-4ae9-9d4a-336209912677:compare"
}
```

Completed job response excerpt:

```json
{
  "job_id": "13a2d407-784d-4423-8d67-45738662b28e",
  "task_id": "real-api-compare-5ba22373-87cd-4ae9-9d4a-336209912677",
  "status": "SUCCEEDED",
  "execution_trace_id": "73176269-e363-4014-a899-12b0a75a7220",
  "skill_graph_version": "module-b-skillgraph-0.1.0",
  "result_id": "885e9e4e-5df0-43a3-aa95-1209f8f59c52",
  "verification_status": "PASS_DEMO"
}
```

`result_detail.comparison.scenarios` contains two rows: ratio 0.1 served `3869.036975366649`, ratio 0.3 served `8028.436605976355`; both have `solver_status="OPTIMAL"` and `verification_status="PASS_DEMO"`.

### Budget expansion request

An A-backed option request is in [`examples/module_b/budget_hypothetical_current_a.json`](../../examples/module_b/budget_hypothetical_current_a.json). Its cost units and capacity delta are explicitly hypothetical; do not present them as an engineering estimate. A synthetic MIP example is available through `toy_case` in the test suite.
