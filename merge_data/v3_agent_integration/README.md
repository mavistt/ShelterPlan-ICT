# ShelterPlan V3 B/C Harness Acceptance — curated upload

This directory preserves the frozen 18-task manifest/GT, all task traces and triage traces, historical reports, regression summary, source-hash provenance, and the reviewed diff for the isolated C fixes. Counts remain 15 PASS, 1 FAIL, 2 NOT_RUN. Historical reports are unchanged.

The V3 experiment used local B Module code and an isolated C source workspace that is deliberately not uploaded. The original C Git commit was not recorded. `reports/C_SOURCE_PROVENANCE.json` keeps the baseline file hashes without local paths; `reports/isolation_patch.diff` is a reviewed historical patch for that source snapshot, not a merge-ready patch for today's C branch. Current `feature/module-c` may differ.

The raw B result payloads (many duplicate 7–14 MB files), run stores, generated scenarios already represented in task traces, session state, copied C source, local build/cache files and the original workspace manifest with absolute paths are excluded. Full task-level Harness traces remain under `traces/`.

For a new run, check out a compatible separate Module C tree, set `SHELTERPLAN_MODULE_C_PATH` to its root (containing `agent/` and `tests/module_c/`), and run `python merge_data/v3_agent_integration/tools/run_v3_suite.py`. This uses the current C code and therefore creates a **new** experiment; it is not an exact replay of the historical patched snapshot. All new V3 output is local and ignored by this folder's `.gitignore`.

V3 does not include Module C source, does not widen the public 12-candidate limit, and does not reinterpret DEMO_ONLY results as field-verified advice. See `TASK_GT.json`, `TASK_MANIFEST.csv`, `BC_INTEGRATION_REPORT.md` and `FAILURE_ANALYSIS.md`.
