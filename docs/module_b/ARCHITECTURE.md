# Module B architecture

Module B reads Module A inputs and writes results only under Module B-owned paths. The configured A adapter validates hashes, schemas, IDs, coordinates, OD foreign keys, reachability semantics, versions, A7 status, and the full pair relation. Parquet rows are streamed into the planning input; no dense demand-by-shelter matrix is built during loading.

```text
AgentArts request
   -> strict TaskRequest
   -> finite Skill Registry and DAG validator
   -> deterministic registered template
   -> sparse OD eligibility -> sparse two-stage LP -> optional bounded budget MIP
   -> independent verifier certificate -> result, evidence, and trace IDs
```

## Planning model

Scenario demand is `pop_est * population_demand_ratio`. An OD edge is eligible when A marks it reachable, its time is non-null and within the selected threshold, and its shelter is not disabled. The LP creates allocation variables for eligible pairs plus explicit unserved variables; demand conservation and site-capacity constraints are sparse SciPy matrices. It first maximizes served demand, then minimizes weighted walk time within a 1e-9 absolute primary-objective tolerance.

Budget expansion uses SciPy HiGHS mixed-integer optimization with continuous assignments and binary option choices. The sparse model enforces demand conservation, capacity, budget, standalone stacking, and named exclusive groups. Four stages optimize served demand, weighted walk time, cost, then option count. The solver accepts up to 64 options and has a 10-second total time limit. A time-limited feasible incumbent is `FEASIBLE` without an optimality claim; a timeout without an incumbent returns `SOLVER_TIMEOUT`.

## Skill graph and execution

The finite registry contains atomic skills S01–S12 and composites C01–C05. Proposed nodes are checked for registered IDs, matching versions, unique IDs, known dependencies, acyclicity, required ancestors, task-family membership, and canonical order. Invalid plans are rejected; safe incomplete proposals can be rewritten to a registered template. C03 has one registered branch: S06/S07 run only when eligible options exist. User requests cannot submit code or dynamic branch expressions.

The executor records input/output hashes, duration, status, skill version, and cache hits per node. Dependency-cache keys include the A data/source version and scenario parameters used by the corresponding skill. Scenario snapshots are immutable JSON content-addressed records with parent IDs and changes attached to results.

## Verification and evidence

The verifier recomputes OD legality, nonnegativity, demand conservation, shelter capacity, totals, deficits, and weighted time from assignments. Budget results also check eligibility, selected IDs, total cost, and combination rules. A valid synthetic or unaccepted A result is `PASS_DEMO`; only an accepted source release can produce `PASS_VERIFIED`. Outcome helpers check result identity, summary claims, and assumption disclosure.

## Storage and API

Runs, scenario snapshots, integration audits, experiment reports, and benchmark outputs are local JSON/CSV/Markdown under `outputs/module_b/`. FastAPI uses an in-process two-worker queue and idempotency keys. It is a local development service, not a distributed queue or public Huawei deployment. The configured A source is now available in `DEMO_ONLY` mode; omitting `fixture_id` loads `configs/pilot.yaml` and its processed A files.
