# ShelterPlan-Eval Stage 2.1 Pilot Summary

- generator: stage2-pilot-2.1
- seed: 20261125
- candidates: 48
- structurally valid: 48
- selected: 24
- strict raw Manifest eligible: 0
- content-integrity eligible: 48
- executed: 0
- Oracle-proven: 0

The previous Stage 2 Pilot remains archived under benchmark/shelterplan_eval/pilot. This Stage 2.1 run writes to benchmark/shelterplan_eval/pilot_stage2_1.

All selected cases are DEMO_ONLY. measured_difficulty is NOT_MEASURED because no real B Solver Trace or independent Oracle was run. No case is EXECUTED or ORACLE_PROVEN.

## Status accounting

| status | count |
| --- | ---: |
| invalid_generated | 0 |
| valid_not_selected | 24 |
| selected | 24 |
| duplicate | 0 |
| manifest_blocked | 48 |

## Expected behaviors

| behavior | selected |
| --- | ---: |
| CLARIFY | 1 |
| EXECUTE | 13 |
| REJECT | 9 |
| SAFE_STOP | 1 |

## Regions

| region_id | raw Manifest | content integrity | data_version |
| --- | --- | --- | --- |
| east_district | False | True | shelterplan_module_a_v2.0-demo__east_district |
| shiqi | False | True | shelterplan_module_a_v2.0-demo__shiqi |
| xiaolan | False | True | shelterplan_module_a_v2.0-demo__xiaolan |

## Contract and release limits

- shelter_ids/base_shelter_ids describe the complete active regional GIS environment.
- The Module B maximum of 12 applies only to improvement_options.
- All generated projects are existing-shelter capacity_expansion options. temporary_site is not supported without a complete new-site coordinate and OD contract.
- CRLF-reconstructed Manifest matches are format evidence, not a VERIFIED release decision.
- rejected_log.json and unselected_log.json are retained separately.
