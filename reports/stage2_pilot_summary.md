# ShelterPlan-Eval Stage 2 Pilot Summary

- generator: stage2-pilot-1
- seed: 20261125
- candidates: 40
- structurally accepted: 32
- pilot accepted: 24
- strict Manifest eligible: 0
- rejected: 16
- deduplicated: 0

Pilot accepted scenarios are DEMO_ONLY structural candidates. Because the current A v2 Manifest has blocking mismatches, none is VERIFIED or eligible for a verified release.

## Region distribution

| region_id | accepted |
| --- | ---: |
| east_district | 12 |
| shiqi | 12 |

## Scenario family distribution

| family | accepted |
| --- | ---: |
| budget_tradeoff | 4 |
| capacity_competition | 4 |
| clarification_and_safe_stop | 4 |
| coverage_baseline | 4 |
| facility_disruption | 4 |
| multi_stage_recovery | 4 |

## Difficulty distribution

### decision_complexity

| level | count |
| --- | ---: |
| high | 12 |
| low | 4 |
| medium | 8 |

### workflow_complexity

| level | count |
| --- | ---: |
| conditional_branch | 8 |
| multi_stage | 8 |
| safe_stop | 4 |
| single_stage | 4 |

### interaction_complexity

| level | count |
| --- | ---: |
| complete_request | 12 |
| missing_parameter | 4 |
| parameter_edit | 8 |

## Manifest status

| region_id | manifest match | data_version |
| --- | --- | --- |
| east_district | False | shelterplan_module_a_v2.0-demo__east_district |
| shiqi | False | shelterplan_module_a_v2.0-demo__shiqi |
| xiaolan | False | shelterplan_module_a_v2.0-demo__xiaolan |

Reject details are retained in benchmark/shelterplan_eval/pilot/reject_log.json. The strict validator rejects Manifest errors; the pilot generator only labels structurally valid cases as DEMO_ONLY and carries the blocking warning forward.
