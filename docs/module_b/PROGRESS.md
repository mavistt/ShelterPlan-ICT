# Module B implementation progress

Last updated: 2026-11-11. This status covers Module B files only. Module A inputs were read and hashed; no A-owned files were changed.

| Gate | Status | Evidence |
|---|---|---|
| A data integration | DONE FOR DEMO_ONLY LOAD | All configured processed files load; four consumed artifacts/config hashes match. A7 stays `DEMO_ONLY`; full audit is 41/42 because one raw population raster is an LFS pointer. See `A_DATA_INTEGRATION_REPORT.md`. |
| Sparse planning | DONE FOR CURRENT SCALE | LP uses SciPy sparse matrices; 7,965 demands and 191,160 OD rows solve `OPTIMAL`. Timeout results do not claim optimality without a feasible incumbent. |
| Real-data experiments | DONE, DEMO_ONLY | Baseline, 12 sensitivity scenarios, closure stress, reachability upper bound, greedy/MIP budgets, and performance recorded. See `REAL_DATA_EXPERIMENTS.md`. |
| API | DONE FOR LOCAL SMOKE | Configured A source passed assess, stress-test, and comparison endpoints; all returned `PASS_DEMO`. No public deployment is claimed. |
| GitHub handoff package | PREPARED LOCALLY / NOT PUSHED | Module C README, API handoff, AgentArts guide, seven request examples, schema regression test, and B-output ignore rule are present. The target repository is not available to the connected GitHub account and outbound `git ls-remote` is reset; branch comparison, commit, push, and PR creation are blocked. |
| Benchmark | DONE FOR 40 SYNTHETIC CASES | 16 dev, 12 validation, 12 test cases over ten family-separated templates. GT-A is independent for the small integer primary objective; GT-C is `NOT_VALIDATED`. See `BENCHMARK_EVALUATION.md`. |
| Verifier and refinement | DONE FOR INTERNAL CASES | Injected-fault recall 1.0, zero clean false positives. Registered-template fallback passed 12 synthetic validation cases; decision remains `KEEP_REVIEW_REQUIRED`. |
| AgentArts / field baselines | NOT_RUN | No AgentArts trace/model configuration or external field authority was supplied. |

## Reproduction and verification

- `.venv\Scripts\python.exe -m backend.cli inspect-data --config configs/pilot.yaml` — passed; loaded `zhongshan_pilot_v0.1`, 7,965 demands, 24 shelters, 191,160 OD rows, `DEMO_ONLY`.
- `.venv\Scripts\python.exe -m pytest tests/test_gis_data.py tests/module_b -q -p no:cacheprovider` — 33 passed (3 A read-only data tests and 30 Module B tests); one environment warning from `requests`/`urllib3` compatibility.
- `.venv\Scripts\python.exe -m compileall -q backend tests\module_b` — passed after code changes.
- `.venv\Scripts\python.exe -m backend.evaluation.real_data_runner` — run `285516ee-1fd7-4fa0-82cf-334938becaf9`, baseline `OPTIMAL / PASS_DEMO`, sampled peak RSS 498,167,808 bytes.
- `.venv\Scripts\python.exe -m backend.evaluation.api_smoke` — run `5ba22373-87cd-4ae9-9d4a-336209912677`; assess, stress-test, compare all succeeded.
- `.venv\Scripts\python.exe -m backend.benchmark --split all --output outputs/module_b/bench/all` — run `3dda8a61-8355-4dbf-9eee-59063af8d991`, 40 cases, 10 families.

## Remaining external dependencies

The 922,251,437-byte source raster is only a 134-byte local Git LFS pointer, so the source build is not fully reproducible. A7 lists seven blockers including unresolved source-register reconciliation, 24 unverified shelters, approximate coordinates, unverified current capacity, and a nonofficial analysis polygon. Replace those only through Module A's owned release process. Real-pilot acceptance, AgentArts integration, public deployment, and field validation remain outside this completed Module B experiment.
