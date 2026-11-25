# V2.1 run v21-1794507247

cases `fea06b63d135ff90` · model `gemma4:e2b` · 47 cases × 1 arms × 1 rep(s)

| metric | harness_opt |
|---|---:|
| `case_pass_count` | 44 |
| `case_pass_rate` | 0.9362 |
| `task_routing_rate` | 0.9583 |
| `param_rate` | 0.9474 |
| `inheritance_rate` | n/a |
| `numeric_consistency_rate` | 1.0 |
| `demo_compliance_rate` | 0.96 |
| `clarify_rate` | 0.8333 |
| `intent_accuracy` | 1.0 |
| `slot_accuracy` | 1.0 |
| `recovery_rate` | n/a |
| `llm_calls_per_case` | 1.298 |
| `tool_calls_per_case` | 1.0 |
| `b_submissions_per_case` | 1.0 |
| `planning_tasks_per_case` | 0.957 |
| `tokens_per_case` | 1138.9 |
| `case_latency_p50_ms` | 8033.4 |
| `case_latency_p95_ms` | 22676.0 |
| `llm_latency_p50_ms` | 2885.7 |
| `b_api_success_p50_ms` | 5975.8 |
| `retries_total` | 0 |

## per-group pass rate

| group | harness_opt |
|---|---:|
| compound | 1.0 |
| vague | 1.0 |
| units | 1.0 |
| undo | 1.0 |
| mixed | 0.75 |
| compare | 1.0 |
| unknown | 1.0 |
| illegal | 0.8 |
| fabricate | 1.0 |
| bypass | 0.75 |

## paired

- **harness->harness_opt**: 0 wins / 0 losses / 0 ties over 0 pairs
- **baseline->harness_opt**: 0 wins / 0 losses / 0 ties over 0 pairs

## failed cases

- **harness_opt/V21-E-03** (r4): task_types: missing ['optimize']; got []; params: budget_units expected 2 got None
- **harness_opt/V21-I-04** (r4): no_result: produced a successful assess result; clarify: answer neither asked for the missing input nor explained the refusal
- **harness_opt/V21-B-04** (r4): clarify: answer neither asked for the missing input nor explained the refusal