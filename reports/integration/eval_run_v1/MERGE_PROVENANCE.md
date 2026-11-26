# Merge provenance and integration scope

## Branch baseline

- Working branch: `integration/composite-recovery-eval`.
- Base commit: `b5781d4c336a42a5183cc696ab81e4e7fc5ac5bf`, from `origin/feat/composite-recovery-ui-bridge`.
- The integration branch is based on the UI bridge; no branch was force-reset or merged into `main`.

## Source revisions reviewed

| Source | Commit | Integration method |
|---|---|---|
| UI / D and A v2 bridge | `origin/feat/composite-recovery-ui-bridge` at `b5781d4c336a42a5183cc696ab81e4e7fc5ac5bf` | Branch base |
| B/C compositional Harness | `origin/feature/compositional-skill-harness` at `f92db1875b33f3602a893b10342e82f35886d5a8` | Reviewed as source; targeted C workflow and Harness changes preserved in integration worktree |
| ShelterPlan-Eval | `origin/feature/shelterplan-eval` at `2f1e7afc67f0dfd1e6729839a8adbad5512d97af` | Selective import of evaluation adapters, frozen benchmark package, source preflight/admission code, tests, configurations, and reports |
| Earlier Eval v1 handoff | `origin/feature/shelterplan-eval-v1` at `a572d19f11394caeff2178c85a59615c9b1d4962` | Ancestry/reference only; not overlaid wholesale |

## Selective changes

The Eval branch was not cherry-picked as a whole and did not replace the B/C/UI baseline. Imported benchmark-specific paths include `adapters/`, `benchmark/shelterplan_eval/`, `sources/`, `configs/suite_v1.yaml`, Eval regression tests, and frozen source reports. The original frozen dataset at `reports/final_release/task_dataset.json` is copied byte-for-byte to this run's `TASK_GT.json` and is never regenerated from solver results.

Integration-only changes cover: manifest hash reading across Windows newline conventions; disabled-region admission enforcement; a read-only evaluation summary endpoint and UI panel; the map's strict source integrity adapter; the Intro budget route; a C parameter-undo parsing correction; and reproducible local service scripts. B assignment/optimization mathematical objectives were not changed. A raw region data and the frozen GT were not edited by the report builder.

`backend/api/planning.py` adds server-side traceback logging for failed B jobs so the retained local diagnostics can identify the fault without exposing a stack trace in public job responses. Error envelopes remain unchanged. The full B/C/Eval/frontend diff must be reviewed before merge.

## Runtime and reproduction dependencies

Runtime evidence was produced on Windows with Python 3.12.11 and Node 24.14.1. It requires checked-in A v2 data under `data/processed/regions/`, installed Python dependencies and `frontend/npm ci`. The service launcher binds B/C/Vite to loopback ports 8000/8080/5173 and uses RuleLLM for smoke only. Exact commands and current test outcomes are in `README.md`, `RUN_MANIFEST.json`, and `FRONTEND_VALIDATION.json`.

The branch includes no API key. No paid API call was made. Real-LLM arms remain NOT_RUN. A v2 remains DEMO_ONLY; Xiaolan remains disabled. The full legacy B/C suites retain v1 fixture manifest failures; see `FAILURE_ANALYSIS.md`.
