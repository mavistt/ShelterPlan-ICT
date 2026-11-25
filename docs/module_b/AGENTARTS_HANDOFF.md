# AgentArts to Module B handoff

For request/response schemas and endpoint details, see [API_HANDOFF_TO_C.md](API_HANDOFF_TO_C.md). For C-side orchestration guidance, see [AGENTARTS_GUIDE.md](AGENTARTS_GUIDE.md).

1. C reads `GET /planning/capabilities` and selects a registered task type and optional skill DAG.
2. C submits a strict `TaskRequest` to `POST /planning/tasks`. It may collect missing scenario values before submission.
3. B checks data mode, scenario and version, skill IDs, dependencies, cycles, and task compatibility. A proposal is accepted, safely rewritten, or rejected with reason codes.
4. B executes registered code, computes a sparse OD assignment and optional budget MIP, then independently verifies the result.
5. C polls `GET /planning/jobs/{job_id}` and fetches `GET /planning/results/{result_id}`. Preserve `execution_trace_id` for joining to C's AgentArts trace.
6. C explains only checked facts and evidence references. Use the claim and assumption verification helpers before any publication flow.

Suggested MCP mapping: `module_b_get_capabilities` -> `module_b_submit_task` -> `module_b_get_job` -> `module_b_get_result`. C owns conversation state and its LLM trace; B owns the finite skill graph, numerical result, data status, certificate, and execution trace.

Run locally with `uvicorn backend.app:app --host 127.0.0.1 --port 8000`. OpenAPI is `/openapi.json`; Swagger UI is `/docs`. Omit `fixture_id` to use the configured Module A source, currently `DEMO_ONLY`. `PASS_DEMO` is not acceptance for real pilot planning. No AgentArts or Huawei deployment is claimed.
