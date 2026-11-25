# Module A demo-data experiments

Run ID: `285516ee-1fd7-4fa0-82cf-334938becaf9`. This is an actual solve over the configured Module A processed files, labeled `DEMO_ONLY`; it is not a real-pilot performance or coverage claim.

## Baseline assessment

The scenario uses the configured 0.2 population-demand ratio and 15-minute walk threshold. The run loaded 7,965 demand nodes, 24 shelters, and 191,160 OD rows from `zhongshan_pilot_v0.1`. All consumed data/config hashes matched the manifest.

| Measure | Result |
|---|---:|
| Estimated population | 551,028.703 |
| Scenario demand | 110,205.741 |
| Served demand | 6,456.103 |
| Scenario service rate | 5.858% |
| Demand represented by nodes with a legal 15-minute connection | 43,108.454 (39.116%) |
| Service rate within connected demand | 14.976% |
| Weighted walk time | 9.038 minutes |
| Solver / verifier | `OPTIMAL` / `PASS_DEMO` |

Diagnostics assign 5,599 nodes and 67,097.287 scenario persons to `NO_LEGAL_CONNECTION`. Another 2,035 nodes and 36,652.351 persons are classified as capacity competition or insufficiency. The 11,332 nominal shelter places are unverified.

The capacity-only reachability counterfactual raises every active site's capacity to the full scenario demand while keeping the same OD relation and 15-minute cutoff. It serves 43,108.454 persons (39.116%); the remaining 67,097.287 are unreachable under that network/time rule. This is a mathematical upper bound, not a capacity proposal.

## Stress, sensitivity, and hypothetical budgets

- All 12 combinations of demand ratios `[0.1, 0.2, 0.3, 0.4]` and thresholds `[10, 15, 20]` solved `OPTIMAL` and verified `PASS_DEMO`.
- Closing the baseline's most-loaded site, `zs_1b4df7920b`, changes served demand from 6,456.103 to 3,325.145. The stress result is `PASS_DEMO`.
- The expansion curve uses the eight highest-utilization sites from the baseline, one hypothetical 25% capacity increment per site, abstract cost 1 per option, and budgets `[0, 1, 3, 6]`.
- Greedy and MIP both return `PASS_DEMO`; every MIP budget run is `OPTIMAL`. Served demand is 6,456.103, 6,531.103, 6,581.103, and 6,636.103 at budgets 0, 1, 3, and 6 respectively. These costs and increments are comparison assumptions, not engineering estimates.

## Runtime and API smoke

The loader took 2.486 seconds, the baseline LP took 0.154 seconds, and sampled peak RSS was 498,167,808 bytes (10 ms sampling interval). Sparse LP matrices and Parquet batch loading avoid the earlier dense-allocation failure.

API smoke run `5ba22373-87cd-4ae9-9d4a-336209912677` submitted the configured A source through `POST /planning/tasks`, `/planning/stress-tests`, and `/planning/comparisons`. All three jobs succeeded with `PASS_DEMO`; the comparison returned two verified scenario rows. Machine-readable run records remain in the local `outputs/module_b/real_data/` directory and are excluded from the GitHub handoff package.

## Superseded result

The earlier record `f1e8c5c5-09a2-41b1-a5bd-f33f84d90b69` predates the fix preventing a `DEMO_ONLY` budget result from being labeled `PASS_VERIFIED`. Its budget-certificate labels are incorrect and must not be used. It is retained unchanged; the run above is the corrected experiment. A7's seven blockers and the unavailable 922 MB raw raster also prevent a verified-real-planning claim.

The machine-readable numerical output for run `285516ee-1fd7-4fa0-82cf-334938becaf9` remains local under `outputs/module_b/real_data/` and is excluded from the GitHub handoff package.
