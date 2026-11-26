# A/B/C + ShelterPlan-Eval integration report

Generated: 2026-11-26T19:12:45.590769+00:00

## Frozen evaluation and measured results

- Frozen tasks and GT: 24; `TASK_GT.json` is byte-identical to the release task dataset (SHA-256 `a2e2a846b903e738e6bdece6e9835ea8bd2b847eb515870dc2e3dfee9ef9ac4b`).
- B structured pilot: 8 selected tasks; PASS=6, UNSCORABLE=2; traces=6; remaining frozen tasks NOT_RUN=16.
- The 4 true Agent arms (Direct LLM, Flat Tools, Fixed Workflow, Composite + Harness) are all NOT_RUN. No paid API was called.
- The RuleLLM C Session/Turn → B Composite smoke is recorded separately and excluded from Agent scores.
- B results remain `DEMO_ONLY`; `PASS_DEMO` does not imply verified real-world planning.

## A v2 data admission

- 87 shelters audited; 56 DEMO candidates; 0 VERIFIED; 30 needs review; 1 excluded.
- Shiqi (25) and East District (31) are DEMO_ONLY. Xiaolan is disabled (30 needs review, 1 excluded).
- Region manifest validation was read-only; see `SOURCE_HASH_AUDIT.json`.

## Verification

- Module A / Eval selected regression: 53 passed (PASS).
- Module B: {'passed': 35, 'failed': 3, 'skipped': 0} (full log: `reports/integration/eval_run_v1/module_b_pytest.log`).
- Module C: {'passed': 266, 'failed': 48, 'skipped': 0} (full log: `reports/integration/eval_run_v1/module_c_pytest.log`).
- Frontend: `PENDING` (details: `FRONTEND_VALIDATION.json`).
- The checked-in backend/frontend code runs on loopback ports 8000 / 8080 / 5173; the start script uses RuleLLM smoke mode only.

## Scope and caveats

This is an integration/evidence report, not a claim of a completed comparative LLM benchmark. Solver comparisons, Agent success rates, and verified real GIS planning are not inferred from absent runs.
The legacy `configs/pilot.yaml` v1 source has a separate manifest mismatch for `data/processed/demand_nodes.geojson`; see `FAILURE_ANALYSIS.md` for retained test outcomes and classification.
