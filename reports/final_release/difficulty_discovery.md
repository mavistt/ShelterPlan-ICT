# A2 Difficulty Discovery

This is an A-only structural/reachability pass over the frozen Stage 2.1 pool.
It does not run B, C, LLM, or an Agent and therefore does not report Agent scores.

- Scenarios: `24`
- Measured decision labels: `{"high": 20, "medium": 4}`
- Expected behaviors: `{"CLARIFY": 1, "EXECUTE": 13, "REJECT": 9, "SAFE_STOP": 1}`
- Oracle statuses: `{"NOT_PROVEN": 24}`
- Structurally repetitive scenarios: `18`

## Evidence boundary

The measured labels are based on option/budget/exclusivity structure and the A v2 travel matrix. Real GIS cases remain `NOT_PROVEN` for optimality; hypothetical investment costs/capacities remain `DEMO_ONLY`.

## Recovery warning

The following cases require a runtime parent/child trace before their recovery difficulty can be called measured:
- `ST21-shiqi-004`
- `ST21-shiqi-010`
- `ST21-shiqi-016`

## Scenario evidence

| scenario | decision | workflow | options | budget competition | overlap max | warnings |
|---|---|---|---:|---|---:|---|
| `ST21-shiqi-000` | `medium` | `single_stage` | `2` | `False` | `0.0` | - |
| `ST21-east_district-001` | `high` | `single_stage` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-002` | `high` | `single_stage` | `4` | `True` | `0.0` | - |
| `ST21-east_district-003` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-004` | `high` | `multi_stage` | `4` | `True` | `0.0` | MULTI_STAGE_STRUCTURE_ONLY_NO_DAMAGE_OR_DISABLED_INPUT |
| `ST21-east_district-005` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-006` | `medium` | `conditional_branch` | `2` | `False` | `0.0` | - |
| `ST21-east_district-009` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-010` | `high` | `multi_stage` | `4` | `True` | `0.0` | MULTI_STAGE_STRUCTURE_ONLY_NO_DAMAGE_OR_DISABLED_INPUT |
| `ST21-east_district-011` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-020` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
| `ST21-east_district-029` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
| `ST21-east_district-031` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
| `ST21-east_district-007` | `high` | `single_stage` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-008` | `high` | `single_stage` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-012` | `medium` | `single_stage` | `2` | `False` | `0.0` | - |
| `ST21-east_district-013` | `high` | `single_stage` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-014` | `high` | `single_stage` | `4` | `True` | `0.0` | - |
| `ST21-east_district-015` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-016` | `high` | `multi_stage` | `4` | `True` | `0.0` | MULTI_STAGE_STRUCTURE_ONLY_NO_DAMAGE_OR_DISABLED_INPUT |
| `ST21-east_district-017` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
| `ST21-shiqi-018` | `medium` | `single_stage` | `2` | `False` | `0.0` | - |
| `ST21-east_district-019` | `high` | `single_stage` | `4` | `True` | `0.0` | - |
| `ST21-east_district-021` | `high` | `conditional_branch` | `4` | `True` | `0.0` | - |
