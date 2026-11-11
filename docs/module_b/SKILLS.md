# Skill Registry

All skills are registered Python implementations. `get_capabilities` returns identifiers, versions, dependencies, execution policy and task templates.

| ID | Skill | Inputs and preconditions | Output / verification |
|---|---|---|---|
| S01 | DataEligibility | Source status and requested mode | Permit demo or block real planning |
| S02 | ScenarioNormalize | Explicit ratio, threshold, data version | Valid immutable scenario |
| S03 | ReachabilityGraph | A OD rows; positive time threshold | Sparse eligible-edge relation |
| S04 | CapacityAssignment | Eligible graph, population ratio, capacities | Max-service LP, then minimum-time LP |
| S05 | BottleneckDiagnosis | Assignment deficits and OD reasons | No-connection vs capacity competition evidence |
| S06 | ExpansionCandidateAudit | Explicit options with costs and stacking policy | Eligible option set or structured error |
| S07 | BudgetedImprovement | Audited options, confirmed budget | HiGHS MIP; service, walk time, cost and option-count objectives |
| S08 | ServiceTimeTradeoff | Primary-optimal assignment | Weighted walking-time result |
| S09 | DisruptionStressTest | Base scenario, disabled shelters, options | Fixed-plan stress and adaptive replan side-by-side |
| S10 | ParameterSensitivity | One allowlisted parameter, at most 20 values | Per-value metrics |
| S11 | ScenarioContrast | Same source/version and explicit scenario changes | Comparable scenario summaries, no forced winner |
| S12 | EvidencePackaging | Result, certificate, versions and trace | Structured evidence references |

Composite templates:

- C01 `AssessAndExplain`: S01 → S02 → S03 → S04 → S05 → S12.
- C02 `OptimizeUnderBudget`: S01 → S02 → S03 → S04 → S06 → S07 → S08 → S12.
- C03 `DiagnoseThenIntervene`: S01 → S02 → S03 → S04 → S05, then branch on `eligible_options_present`. If true, run S06 → S07 → S12; if false, skip the intervention nodes and go directly to S12 with `NO_ELIGIBLE_EXPANSION_OPTIONS`.
- C04 `ResilienceWhatIf`: S01 → S02 → S03 → S04 → S09 → S12.
- C05 `CompareAndRecommend`: S01 → S02 → S03 → S04 → S11 → S12.

Sensitivity uses the S01/S02/S03/S04/S10/S12 route. Every composite emits explicit dependencies and a validated DAG. The C03 condition is a fixed registered branch; arbitrary branch expressions are rejected. LLM proposal is an input to validation, never executable code.
