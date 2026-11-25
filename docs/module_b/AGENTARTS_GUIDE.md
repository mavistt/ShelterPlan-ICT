# AgentArts integration guide for Module C

## Responsibility boundary

```text
User's natural-language request
  -> AgentArts interprets intent and asks for missing decisions
  -> Module B validates the finite Skill Plan and scenario contract
  -> Module B computes the constrained assignment or scenario analysis
  -> Module B independently verifies the result
  -> AgentArts explains only the verified, status-aware evidence in the UI
```

AgentArts owns conversation, intent extraction, user clarification, orchestration, and presentation. Module B owns the registered Skill Graph, versioned scenario, sparse reachability/LP/MIP computations, verifier certificate, and execution trace. Module A owns source data and its acceptance status.

## Expose coarse tools

Expose these four B operations to AgentArts:

1. `get_capabilities` -> `GET /planning/capabilities`.
2. `submit_task` -> the appropriate POST endpoint (`/planning/tasks`, `/planning/stress-tests`, or `/planning/comparisons`).
3. `get_job` -> `GET /planning/jobs/{job_id}`.
4. `get_result` -> `GET /planning/results/{result_id}`.

Do not expose all 12 atomic Skills as separate LLM tools. The atomic Skills are internal stages with prerequisites and evidence contracts; the API already accepts a finite `task_type` and validates an optional full DAG. Coarse tools reduce invalid intermediate calls and make the end-to-end certificate the handoff unit.

## Submitting a proposed Skill Plan

Use `GET /planning/capabilities` as the source of `skill_graph_version`, task types, skill IDs, dependencies, and skill version. Send `proposed_skill_plan.nodes[]` with only `node_id`, `skill_id`, `skill_version`, and `depends_on[]` (the schema also has a bounded `condition`). The graph version is `module-b-skillgraph-0.1.0`; the current atomic skill version is `1.0.0`.

B checks unknown/duplicate skills, versions, dependency references, cycles, prerequisites, task-family membership, and canonical order. Unknown skills, cycles, dependency bypasses, or incompatible tasks are rejected. A safe but incomplete proposal is deterministically rewritten to the registered task template. For `diagnose_intervene`, candidate-audit/optimization stages run only when eligible options exist.

For the minimal-risk path, omit `proposed_skill_plan`; B will choose its registered fallback. A valid and invalid request are in [`examples/module_b/valid_skill_plan_toy.json`](../../examples/module_b/valid_skill_plan_toy.json) and [`examples/module_b/invalid_skill_plan_toy.json`](../../examples/module_b/invalid_skill_plan_toy.json).

## Scenario clarification and duplicate control

AgentArts should ask the user for values that materially change the intended analysis, such as the population-demand ratio, walk threshold, budget, candidate sites, or disruption. Do not infer real budgets or mark a hypothetical option human-confirmed. If the user accepts configured defaults, submit the configured scenario and include that fact in the explanation.

Use a unique `task_id` and `idempotency_key` for each logical request. Reuse the same key only for an exact retry. A corrected scenario or changed budget is a new logical request and needs a new key; use `parent_scenario_id` when the new result is an intentional scenario fork. B has an internal dependency cache, but it is not a substitute for C's request de-duplication. Idempotency storage is process-local and is cleared on restart.

## Reading and explaining results

Always carry `data_version`, `data_status`, `analysis_mode`, `scenario_id`, `solver_status`, `optimality_proven`, `verification_status`, and `warnings` into the response to the user. Use `result_id` to retrieve detailed evidence and assignments. `scenario_id` identifies the normalized scenario snapshot; `parent_scenario_id` and `scenario_changes` explain the fork; `execution_trace_id` joins B's per-skill trace to the real AgentArts trace.

The current A release is `zhongshan_pilot_v0.1`, status `DEMO_ONLY`. `PASS_DEMO` means internal constraints passed on demo/synthetic inputs; it does not mean the shelters, capacity, official geography, or real emergency coverage have been validated. Do not describe a `PASS_DEMO` plan as an operational recommendation. Only an accepted source can yield `PASS_VERIFIED`.

For MIP results, say `OPTIMAL` only when `optimality_proven=true`. A feasible time-limited incumbent is `FEASIBLE` and must be described as not proven optimal. A timeout without an incumbent fails the job and returns no publishable plan. If a verifier returns `FAIL` or `BLOCKED`, do not present the plan as cleared.

## Trace and future AgentArts evaluation

Persist C's actual model/AgentArts trace ID, prompt/tool-call metadata allowed by the team's privacy policy, B `execution_trace_id`, request/task ID, returned `result_id`, graph/skill versions, outcome, retry count, and latency. Do not fabricate or backfill an AgentArts trace from a B API smoke. Keep user secrets and raw prompts out of public artifacts. Future comparisons can join the real C trace to B's trace and measure invalid plan rate, retries, verification failures, latency, and token cost. AgentArts integration and those measurements are not implemented by this handoff.
