# ShelterPlan-Eval Stage 3 Pilot

本报告复用冻结的 Stage 2.1 24 个 Pilot；未重新随机生成场景，未修改 A v2、旧 Pilot 或 main。

## Run summary

- Cases: 8
- Actual B executions: 6
- Trace-bearing results: 7
- Status: `{"PASS": 6, "UNSCORABLE": 2}`
- Oracle: `{"NOT_RUN": 5, "ORACLE_PROVEN": 3}`
- Measured difficulty evidence: `{"REAL_B_TRACE_AND_VERIFIER": 6, "STATE_PROBE_ONLY_NO_AGENT_OUTPUT": 2}`
- Real Greedy comparisons: `2`
- VETS mean: `{"U": 0.9999999999934247, "W": 1.0, "P": 1.0, "E": 0.875, "overall": 0.9999999999986849}`
- LLM: `NOT_RUN`; this run has no Agent/LLM output and therefore CLARIFY/SAFE_STOP are UNSCORABLE.
- VETS means apply only to cases/dimensions with evidence; the two UNSCORABLE cases were not silently converted to zeros.

## Route policy

- Shiqi uses the configured Module B HTTP route when enabled.
- East District uses its explicit `configs/module_a_v2_b/east_district.yaml` source with the real B Solver. The checked-in HTTP route remains Shiqi-only and is recorded as `UNSUPPORTED`; there is no silent fallback.

## Case results

| scenario | region | family | expected | execution | evaluation | oracle | trace |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ST21-shiqi-000 | shiqi | coverage_baseline | EXECUTE | SUCCEEDED | PASS | NOT_RUN | ea13ee9b-e2d9-4f65-a389-a88269e5da95 |
| ST21-east_district-001 | east_district | budget_tradeoff | EXECUTE | SUCCEEDED | PASS | ORACLE_PROVEN | 48dd9fc9-9db0-403b-8a52-1ea7fdc996a4 |
| ST21-shiqi-002 | shiqi | capacity_competition | EXECUTE | SUCCEEDED | PASS | ORACLE_PROVEN | 991378b8-24d9-47ad-b38d-4f7deaa65289 |
| ST21-east_district-003 | east_district | facility_disruption | EXECUTE | SUCCEEDED | PASS | NOT_RUN | 30092deb-17b1-4a8f-b635-755b5eff3cce |
| ST21-shiqi-004 | shiqi | multi_stage_recovery | EXECUTE | SUCCEEDED | PASS | ORACLE_PROVEN | ffd168e6-0263-4f38-907f-645bd08bc046 |
| ST21-east_district-011 | east_district | clarification_and_safe_stop | SAFE_STOP | SUCCEEDED | UNSCORABLE | NOT_RUN | 2627659b-18a3-450a-850f-ec29c3a682e4 |
| ST21-east_district-029 | east_district | clarification_and_safe_stop | CLARIFY | FAILED | UNSCORABLE | NOT_RUN | NONE |
| ST21-shiqi-016 | shiqi | multi_stage_recovery | REJECT | FAILED | PASS | NOT_RUN | a1962a29-2610-44a5-b26a-5ae607eed6b8 |

## Measured difficulty and execution evidence

| scenario | solver | baseline served | optimized/oracle served | greedy served | runtime ms | measured difficulty |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| ST21-shiqi-000 | OPTIMAL | 7274.593472765874 | N/A | N/A | 4253.229 | MEASURED_EXECUTION_ONLY |
| ST21-east_district-001 | OPTIMAL | 10257.165950297309 | 10617.165950198309 | 10617.165950297309 | 5855.221 | MEASURED_ORACLE_BACKED |
| ST21-shiqi-002 | OPTIMAL | 16355.087031491968 | 16715.087031392977 | 16715.087031490984 | 3643.342 | MEASURED_ORACLE_BACKED |
| ST21-east_district-003 | OPTIMAL | 11506.999999999016 | N/A | N/A | 2652.013 | MEASURED_EXECUTION_ONLY |
| ST21-shiqi-004 | OPTIMAL | 8548.355784415198 | 8908.355784316198 | N/A | 1779.623 | MEASURED_ORACLE_BACKED |
| ST21-east_district-011 | OPTIMAL | 6490.739257357509 | N/A | N/A | 1841.677 | NOT_MEASURED |
| ST21-east_district-029 | NOT_RUN | N/A | N/A | N/A | N/A | NOT_MEASURED |
| ST21-shiqi-016 | NOT_RUN | N/A | N/A | N/A | N/A | MEASURED_EXECUTION_ONLY |

### Safety and multi-round evidence

- `ST21-shiqi-004` multi-round: runtime parent=`ST21-shiqi-004-parent`, child parent reference=`ST21-shiqi-004-parent`, state_ok=`True`.
- `ST21-east_district-011` SAFE_STOP probe: baseline served 6490.739/502793.117; capacity-unbounded upper bound 39327.723; supported=`False`. No Agent score was awarded.
- `ST21-shiqi-016` REJECT evidence: observed error=`CANDIDATE_INVALID`, HTTP status=`202`, trace=`a1962a29-2610-44a5-b26a-5ae607eed6b8`.

## Scoring contract

VETS 输出 U/W/P/E：Utility、Workflow、Policy/Safety、Evidence。默认加权为 U 0.30、W 0.25、P 0.25、E 0.20；仅对适用维度求归一化均值。正确 REJECT 可得成功分，但必须有实际 HTTP/B 错误证据；CLARIFY 与 SAFE_STOP 在没有 Agent 输出时保持 UNSCORABLE。

## Limitations and blockers

- All real GIS runs remain `DEMO_ONLY`; no result is `VERIFIED`.
- No LLM/Agent baseline was run, so this is not yet a formal Flat Tool Agent vs Fixed Workflow vs Composite Skill + Harness comparison.
- Any East District HTTP route integration must be implemented and tested by B/C; this evaluation does not change B/C configuration.
