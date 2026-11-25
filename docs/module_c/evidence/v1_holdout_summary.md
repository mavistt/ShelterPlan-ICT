# V1 run v1-1794428725

## baseline

| metric | value |
|---|---:|
| `case_count` | 14 |
| `case_pass_count` | 3 |
| `case_pass_rate` | 0.2143 |
| `task_success_rate` | 0.0 |
| `task_case_count` | 9 |
| `param_correctness_rate` | 0.0 |
| `param_case_count` | 4 |
| `numeric_consistency_rate` | 0.1818 |
| `numeric_case_count` | 11 |
| `guard_refusal_rate` | 0.3333 |
| `guard_case_count` | 3 |
| `ambiguous_resolution_rate` | 1.0 |
| `demo_compliance_rate` | 0.1818 |
| `failure_taxonomy` | {'demo_violation': 3, 'numeric_violation': 5, 'wrong_tool_or_task': 7, 'other': 2, 'none': 3, 'guessed_instead_of_ask': 2} |
| `llm_calls_per_case` | 3.571 |
| `tool_calls_per_case` | 1.714 |
| `tool_failures_total` | 4 |
| `prompt_tokens_total` | 57358 |
| `completion_tokens_total` | 14819 |
| `tokens_per_case` | 5155.5 |
| `llm_latency_p50_ms` | 25194.3 |
| `llm_latency_p95_ms` | 45262.5 |
| `case_wall_p50_ms` | 25292.3 |
| `case_wall_p95_ms` | 46244.6 |
| `rewrite_used_count` | 0 |
| `loop_limit_count` | 0 |

## harness

| metric | value |
|---|---:|
| `case_count` | 14 |
| `case_pass_count` | 6 |
| `case_pass_rate` | 0.4286 |
| `task_success_rate` | 0.2222 |
| `task_case_count` | 9 |
| `param_correctness_rate` | 0.0 |
| `param_case_count` | 4 |
| `numeric_consistency_rate` | 0.3636 |
| `numeric_case_count` | 11 |
| `guard_refusal_rate` | 0.6667 |
| `guard_case_count` | 3 |
| `ambiguous_resolution_rate` | 1.0 |
| `demo_compliance_rate` | 0.3636 |
| `failure_taxonomy` | {'none': 6, 'other': 3, 'wrong_tool_or_task': 6, 'demo_violation': 1, 'numeric_violation': 1, 'guessed_instead_of_ask': 1} |
| `llm_calls_per_case` | 2.571 |
| `tool_calls_per_case` | 1.286 |
| `tool_failures_total` | 3 |
| `prompt_tokens_total` | 53293 |
| `completion_tokens_total` | 19228 |
| `tokens_per_case` | 5180.1 |
| `llm_latency_p50_ms` | 27932.7 |
| `llm_latency_p95_ms` | 65546.0 |
| `case_wall_p50_ms` | 33637.5 |
| `case_wall_p95_ms` | 91247.9 |
| `rewrite_used_count` | 5 |
| `loop_limit_count` | 0 |

## failed cases

- **baseline/NL-H-01** (assess): demo: missing DEMO_ONLY disclosure or used forbidden wording
- **baseline/NL-H-02** (multi_turn_budget): task_types: missing ['optimize']; got []; numeric: answer cites numbers not present in tool output
- **harness/NL-H-02** (multi_turn_budget): task_types: missing ['optimize']; got []; budget_units: expected 3 got None; population_demand_ratio: expected 0.2 got None; max_walk_time_min: expected 15.0 got None
- **baseline/NL-H-03** (multi_turn_params): task_types: missing ['assess']; got []; population_demand_ratio: expected 0.4 got 0.2; numeric: answer cites numbers not present in tool output
- **harness/NL-H-03** (multi_turn_params): population_demand_ratio: expected 0.4 got 0.2
- **baseline/NL-H-04** (multi_turn_disable): task_types: missing ['assess']; got ['stress_test']; budget_units: expected 2 got None; demo: missing DEMO_ONLY disclosure or used forbidden wording
- **harness/NL-H-04** (multi_turn_disable): task_types: missing ['assess']; got ['stress_test']; demo: missing DEMO_ONLY disclosure or used forbidden wording
- **baseline/NL-H-05** (diagnose): task_types: missing ['diagnose_intervene']; got []; numeric: answer cites numbers not present in tool output
- **harness/NL-H-05** (diagnose): task_types: missing ['diagnose_intervene']; got []; numeric: answer cites numbers not present in tool output
- **baseline/NL-H-06** (compare): demo: missing DEMO_ONLY disclosure or used forbidden wording
- **baseline/NL-H-07** (sensitivity): task_types: missing ['sensitivity']; got []; numeric: answer cites numbers not present in tool output
- **harness/NL-H-07** (sensitivity): task_types: missing ['sensitivity']; got []
- **baseline/NL-H-09** (guard_negative_budget): ask_or_refuse: final answer neither asks nor explains the refusal
- **baseline/NL-H-10** (guard_invalid_ratio): ask_or_refuse: final answer neither asks nor explains the refusal
- **harness/NL-H-10** (guard_invalid_ratio): ask_or_refuse: final answer neither asks nor explains the refusal
- **baseline/NL-H-13** (multi_turn_undo): task_types: missing ['assess']; got []; numeric: answer cites numbers not present in tool output
- **harness/NL-H-13** (multi_turn_undo): task_types: missing ['assess']; got []; population_demand_ratio: expected 0.2 got None
- **baseline/NL-H-14** (explain): task_types: missing ['assess', 'explain_result']; got []
- **harness/NL-H-14** (explain): task_types: missing ['assess', 'explain_result']; got []