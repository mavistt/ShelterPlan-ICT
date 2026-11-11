# V1 run v1-1794427837

## baseline

| metric | value |
|---|---:|
| `case_count` | 10 |
| `case_pass_count` | 3 |
| `case_pass_rate` | 0.3 |
| `task_success_rate` | 0.0 |
| `task_case_count` | 7 |
| `param_correctness_rate` | 0.0 |
| `param_case_count` | 4 |
| `numeric_consistency_rate` | 0.125 |
| `numeric_case_count` | 8 |
| `guard_refusal_rate` | 1.0 |
| `guard_case_count` | 2 |
| `ambiguous_resolution_rate` | None |
| `demo_compliance_rate` | 0.125 |
| `failure_taxonomy` | {'wrong_tool_or_task': 4, 'demo_violation': 4, 'other': 3, 'numeric_violation': 1, 'none': 3} |
| `llm_calls_per_case` | 3.5 |
| `tool_calls_per_case` | 1.8 |
| `tool_failures_total` | 1 |
| `prompt_tokens_total` | 37566 |
| `completion_tokens_total` | 11798 |
| `tokens_per_case` | 4936.4 |
| `llm_latency_p50_ms` | 25231.1 |
| `llm_latency_p95_ms` | 66300.3 |
| `case_wall_p50_ms` | 26025.4 |
| `case_wall_p95_ms` | 67240.3 |
| `rewrite_used_count` | 0 |
| `loop_limit_count` | 0 |

## harness

| metric | value |
|---|---:|
| `case_count` | 10 |
| `case_pass_count` | 7 |
| `case_pass_rate` | 0.7 |
| `task_success_rate` | 0.5714 |
| `task_case_count` | 7 |
| `param_correctness_rate` | 0.5 |
| `param_case_count` | 4 |
| `numeric_consistency_rate` | 0.625 |
| `numeric_case_count` | 8 |
| `guard_refusal_rate` | 1.0 |
| `guard_case_count` | 2 |
| `ambiguous_resolution_rate` | None |
| `demo_compliance_rate` | 0.625 |
| `failure_taxonomy` | {'none': 7, 'other': 2, 'wrong_tool_or_task': 2, 'numeric_violation': 1, 'demo_violation': 1} |
| `llm_calls_per_case` | 2.4 |
| `tool_calls_per_case` | 1.3 |
| `tool_failures_total` | 0 |
| `prompt_tokens_total` | 40049 |
| `completion_tokens_total` | 10888 |
| `tokens_per_case` | 5093.7 |
| `llm_latency_p50_ms` | 24638.8 |
| `llm_latency_p95_ms` | 46585.5 |
| `case_wall_p50_ms` | 30081.5 |
| `case_wall_p95_ms` | 67948.1 |
| `rewrite_used_count` | 4 |
| `loop_limit_count` | 0 |

## failed cases

- **baseline/NL-D-01** (assess): task_types: missing ['assess']; got []
- **baseline/NL-D-02** (multi_turn): max_walk_time_min: expected 20 got 15.0; demo: missing DEMO_ONLY disclosure or used forbidden wording
- **baseline/NL-D-03** (optimize): task_types: missing ['optimize']; got []; disabled_shelter_ids: expected ['zs_1b4df7920b', 'zs_60213118ec', 'zs_e016bde4b3', 'zs_737caba8e8', 'zs_6358642b6f'] got []; numeric: answer cites numbers not
- **harness/NL-D-03** (optimize): task_types: missing ['optimize']; got ['stress_test']; budget_units: expected 1 got None
- **baseline/NL-D-04** (compare): task_types: missing ['compare']; got []
- **baseline/NL-D-06** (param_ratio): task_types: missing ['assess']; got ['compare']; population_demand_ratio: expected 0.3 got 0.2; demo: missing DEMO_ONLY disclosure or used forbidden wording
- **harness/NL-D-06** (param_ratio): task_types: missing ['assess']; got []; population_demand_ratio: expected 0.3 got None; numeric: answer cites numbers not present in tool output
- **baseline/NL-D-07** (stress): demo: missing DEMO_ONLY disclosure or used forbidden wording
- **baseline/NL-D-09** (demo_gate): demo: missing DEMO_ONLY disclosure or used forbidden wording
- **harness/NL-D-09** (demo_gate): demo: missing DEMO_ONLY disclosure or used forbidden wording