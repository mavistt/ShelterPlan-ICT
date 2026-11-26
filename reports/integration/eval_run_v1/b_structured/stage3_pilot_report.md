# ShelterPlan-Eval Stage 3 Pilot

本报告复用冻结的 Stage 2.1 24 个 Pilot；未重新随机生成场景，未修改 A v2、旧 Pilot 或 main。

## Run summary

- Cases: 8
- Actual B executions: 6
- Trace-bearing results: 6
- Status: `{"PASS": 6, "UNSCORABLE": 2}`
- Oracle: `{"NOT_RUN": 5, "CONDITIONAL_SUBSET_OPTIMUM": 3}`
- Measured difficulty evidence: `{"REAL_B_TRACE_AND_VERIFIER": 6, "STATE_PROBE_ONLY_NO_AGENT_OUTPUT": 2}`
- Real Greedy comparisons: `3`
- VETS hard-success: `6/8` overall; scored=`6/6`, unscorable/not-run=`2`.
- VETS status counts: `{"PASS": 6, "UNSCORABLE": 2}`; U/W/P/E diagnostic means: `{"U": 0.9999999999912329, "W": 1.0, "P": 1.0, "E": 1.0, "diagnostics_overall": 0.9999999999988728}`.
- LLM: `NOT_RUN`; this run has no Agent/LLM output and therefore CLARIFY/SAFE_STOP are UNSCORABLE.
- VETS is a binary hard-assertion outcome. U/W/P/E, planning quality, evidence and cost are reported separately; UNSCORABLE/NOT_RUN/INFRA_ERROR are not silently counted as success.

## Route policy

- Shiqi uses the configured Module B HTTP route when enabled.
- East District uses its explicit `configs/module_a_v2_b/east_district.yaml` source with the real B Solver. The checked-in HTTP route remains Shiqi-only and is recorded as `UNSUPPORTED`; there is no silent fallback.

## Case results

| scenario | region | family | expected | execution | evaluation | oracle | trace |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ST21-shiqi-000 | shiqi | coverage_baseline | EXECUTE | SUCCEEDED | PASS | NOT_RUN | 880137d2-2ede-4af5-acd6-0cdeb7230be5 |
| ST21-east_district-001 | east_district | budget_tradeoff | EXECUTE | SUCCEEDED | PASS | CONDITIONAL_SUBSET_OPTIMUM | 59871412-fa34-471a-8d4d-839eb6e0e514 |
| ST21-shiqi-002 | shiqi | capacity_competition | EXECUTE | SUCCEEDED | PASS | CONDITIONAL_SUBSET_OPTIMUM | 9f67ca2f-6375-45a3-b8db-989b389b41d0 |
| ST21-east_district-003 | east_district | facility_disruption | EXECUTE | SUCCEEDED | PASS | NOT_RUN | 2282258a-6847-45d6-bf2f-c998895f802f |
| ST21-shiqi-004 | shiqi | multi_stage_recovery | EXECUTE | SUCCEEDED | PASS | CONDITIONAL_SUBSET_OPTIMUM | 0379da34-322e-4f7c-9a7a-366e0ddaf2d4 |
| ST21-east_district-011 | east_district | clarification_and_safe_stop | SAFE_STOP | SUCCEEDED | UNSCORABLE | NOT_RUN | 2b5eac93-fdae-4566-8492-995123c82d37 |
| ST21-east_district-029 | east_district | clarification_and_safe_stop | CLARIFY | FAILED | UNSCORABLE | NOT_RUN | NONE |
| ST21-shiqi-016 | shiqi | multi_stage_recovery | REJECT | FAILED | PASS | NOT_RUN | NONE |

## Measured difficulty and execution evidence

| scenario | solver | baseline served | optimized/oracle served | greedy served | runtime ms | measured difficulty |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| ST21-shiqi-000 | OPTIMAL | 7274.593472765874 | N/A | N/A | 716.62 | MEASURED_EXECUTION_ONLY |
| ST21-east_district-001 | OPTIMAL | 10257.165950297309 | 10617.165950198309 | 10617.165950297309 | 11586.573 | MEASURED_EXECUTION_ONLY |
| ST21-shiqi-002 | OPTIMAL | 16355.087031491968 | 16715.087031392977 | 16715.087031490984 | 4959.327 | MEASURED_EXECUTION_ONLY |
| ST21-east_district-003 | OPTIMAL | 11506.999999999016 | N/A | N/A | 2838.547 | MEASURED_EXECUTION_ONLY |
| ST21-shiqi-004 | OPTIMAL | 8548.355784415198 | 8908.355784316198 | 8908.355784415198 | 2987.2200000000003 | MEASURED_EXECUTION_ONLY |
| ST21-east_district-011 | OPTIMAL | 6490.739257357509 | N/A | N/A | 2309.397 | NOT_MEASURED |
| ST21-east_district-029 | NOT_RUN | N/A | N/A | N/A | N/A | NOT_MEASURED |
| ST21-shiqi-016 | NOT_RUN | N/A | N/A | N/A | N/A | MEASURED_EXECUTION_ONLY |

### Safety and multi-round evidence

- `ST21-shiqi-004` multi-round: runtime parent=`ST21-shiqi-004-parent`, child parent reference=`ST21-shiqi-004-parent`, state_ok=`True`.
- `ST21-east_district-011` SAFE_STOP probe: baseline served 6490.739/502793.117; capacity-unbounded upper bound 39327.723; supported=`False`. No Agent score was awarded.
- `ST21-shiqi-016` REJECT evidence: observed error=`CANDIDATE_INVALID`, HTTP status=`None`, trace=`None`.

## Scoring contract

VETS 是通过 GT 硬断言后的二元任务成功结果；U/W/P/E 仅是独立诊断维度（Utility、Workflow、Policy/Safety、Evidence），其诊断均值不等于端到端成功率。Planning Quality 单独记录服务人数、预算和 Oracle gap；Evidence 单独记录 Trace/Verifier/来源；Cost 单独记录运行时间、Trace 工具调用和 LLM token。正确 REJECT 必须匹配错误码、阶段和未启动求解器；CLARIFY 与 SAFE_STOP 在没有真实 Agent 输出时保持 UNSCORABLE。

## Limitations and blockers

- All real GIS runs remain `DEMO_ONLY`; no result is `VERIFIED`.
- No LLM/Agent baseline was run, so this is not yet a formal Flat Tool Agent vs Fixed Workflow vs Composite Skill + Harness comparison.
- Any East District HTTP route integration must be implemented and tested by B/C; this evaluation does not change B/C configuration.
