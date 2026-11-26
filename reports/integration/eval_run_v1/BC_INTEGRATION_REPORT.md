# B/C integration report

## Observed integration

- Local real Module B is exposed at port 8000; C Harness at 8080; Module D at 5173. All bind to loopback.
- The persisted Shiqi RuleLLM Session/Turn smoke called B and completed a composite request. It is labelled `RULE_BASED_SMOKE`, excluded from benchmark metrics, and uses DEMO_ONLY data.
- The frozen B direct structured pilot invoked B for 8 task cases: PASS=6, UNSCORABLE=2, FAIL=0; 16 frozen cases were not run. It is not a C Agent evaluation.
- A direct East District B API replay returned SUCCEEDED / PASS_DEMO for data version `shelterplan_module_a_v2.0-demo__east_district` and the 15-minute / 10% assessment.
- The final four-test browser E2E suite passed. The East C→B case completed with result and Verifier evidence. Three earlier East browser attempts failed; one captured B traceback identified `MemoryError` while hashing the full result. Streaming hash/result serialization and a one-region source cache were then added without changing solver mathematics. The before/after traces are under `traces/`.

## Integrity and status

Current A v2 source manifests were checked read-only against the checked-in artifacts; see `SOURCE_HASH_AUDIT.json`. Data status remains DEMO_ONLY. B MIP/Greedy values are solver outputs and Verifier evidence, not an independent Oracle unless the frozen GT explicitly says so. Disabled Xiaolan is rejected before planning.

## Full regression

- Module B: 35 passed, 3 failed, 0 skipped.
- Module C: 266 passed, 48 failed, 0 skipped.
- Frontend unit/typecheck/build/E2E details: `FRONTEND_VALIDATION.json`.

The Module B/C full suites exercise legacy v1 fixtures whose manifest declares hashes different from the checked-in `data/processed/demand_nodes.geojson`; failures are not hidden or repaired by changing the frozen dataset.

Test logs retain failure stacks and final counters with local checkout, temporary-user, and Python-environment paths redacted.
