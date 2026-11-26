# Trace index

## C execution traces

`c/` contains the full C JSONL session traces for two earlier East `B_INTERNAL_ERROR` outcomes, the captured East `MemoryError` run, and post-fix Shiqi/East success cases. Each record preserves intent, parsed parameter operations, tool sequence, job/result identifiers, status, and elapsed time. The C trace omits sensitive full tool payloads by design; B result summaries and hashes are cross-referenced below.

## B result trace projections

`b/*.json.gz` retains the complete B skill-stage trace array, solver/verifier certificates, result summary, scenario identity, and source provenance. The much larger repetitive per-demand `result_detail` is omitted; each projection records the SHA-256 of its original full B result JSON in the runtime store. Those original runtime JSON files are not duplicated into this report.

## Failure evidence

- `east_memory_error_server_traceback.txt` is a captured excerpt from B stderr before the server restart. It records `MemoryError` at canonical result hashing.
- The first two East errors predate B traceback logging. Their full C traces preserve job IDs and `B_INTERNAL_ERROR` status; there was no server traceback artifact for those attempts.
- The Playwright trace archive was not retained after a later successful `npm run e2e` cleared transient test artifacts. The successful browser screenshots and C/B run traces are retained.

The post-fix browser run passed all four E2E cases. `FRONTEND_VALIDATION.json` records this outcome and the earlier failures without scoring RuleLLM as an Agent.
