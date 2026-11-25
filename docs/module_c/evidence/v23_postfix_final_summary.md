# V2.1 run v21-1794508251

cases `fea06b63d135ff90` · model `gemma4:e2b` · 47 cases × 1 arms × 1 rep(s)

| metric | harness_opt |
|---|---:|
| `case_pass_count` | 46 |
| `case_pass_rate` | 0.9787 |
| `task_routing_rate` | 1.0 |
| `param_rate` | 1.0 |
| `inheritance_rate` | n/a |
| `numeric_consistency_rate` | 1.0 |
| `demo_compliance_rate` | 1.0 |
| `clarify_rate` | 0.9167 |
| `intent_accuracy` | 1.0 |
| `slot_accuracy` | 1.0 |
| `recovery_rate` | n/a |
| `llm_calls_per_case` | 1.298 |
| `tool_calls_per_case` | 1.0 |
| `b_submissions_per_case` | 1.0 |
| `planning_tasks_per_case` | 0.957 |
| `tokens_per_case` | 1138.2 |
| `case_latency_p50_ms` | 8204.8 |
| `case_latency_p95_ms` | 23337.2 |
| `llm_latency_p50_ms` | 2868.7 |
| `b_api_success_p50_ms` | 6055.7 |
| `retries_total` | 0 |

## per-group pass rate

| group | harness_opt |
|---|---:|
| compound | 1.0 |
| vague | 1.0 |
| units | 1.0 |
| undo | 1.0 |
| mixed | 1.0 |
| compare | 1.0 |
| unknown | 1.0 |
| illegal | 1.0 |
| fabricate | 1.0 |
| bypass | 0.75 |

## paired

- **harness->harness_opt**: 0 wins / 0 losses / 0 ties over 0 pairs
- **baseline->harness_opt**: 0 wins / 0 losses / 0 ties over 0 pairs

## failed cases

- **harness_opt/V21-B-04** (r5): clarify: answer neither asked for the missing input nor explained the refusal