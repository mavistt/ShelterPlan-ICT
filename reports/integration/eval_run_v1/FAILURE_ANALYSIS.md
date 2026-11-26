# Failure analysis

## Full backend regression

- Module B: 35 passed, 3 failed, 0 skipped. The three failing tests use the legacy v1 source in `configs/pilot.yaml`; they fail at `data/processed/demand_nodes.geojson` manifest hash validation. The A v2 manifest audit is a separate exact-raw-match check and passes for its declared current files. Do not rewrite the v1 manifest or data as part of this integration run.
- Module C: 266 passed, 48 failed, 0 skipped. The suite reports 48 failures alongside 266 passes. At least 27 captured failures directly show the same legacy B v1 `B_DATA_INVALID` manifest mismatch; dependent dialogue/state assertions also fail after upstream data is rejected. These are not classified as 48 independent C defects.

## East browser run

Three East browser attempts before the memory fix returned `B_INTERNAL_ERROR`. The first two server logs had no traceback; their C traces are preserved. The third failed at `backend/graph/executor.py` while canonical JSON hashing materialized the full result and raised `MemoryError`; a captured traceback excerpt and C trace are retained under `traces/`. Streaming canonical hashing/result writes and a one-region source-bundle cache were introduced; the complete four-test Playwright suite subsequently passed, including East C-to-B result retrieval and Xiaolan rejection. The pre-fix failures remain recorded.

## Agent / architecture

All four real-LLM arms are NOT_RUN. There is no model trace to classify. RuleLLM smoke does not count as a scored agent run. No verifier false-negative rate or recovery success rate is inferable from absent Agent runs.

## Classification

Keep failure attribution separated as legacy A data/manifest validation, C state or routing, B execution, Verifier, or UI transport only when a retained trace supports it. The observed full-result hashing `MemoryError` is addressed by the encoding/cache change and passed post-fix E2E; the first two opaque internal errors cannot be independently root-caused beyond their matching symptom.
