# V2.1 run v21-1794487124

cases `fea06b63d135ff90` · model `gemma4:e2b` · 47 cases × 2 arms × 1 rep(s)

| metric | harness | harness_opt |
|---|---:|---:|
| `case_pass_count` | 19 | 37 |
| `case_pass_rate` | 0.4043 | 0.7872 |
| `task_routing_rate` | 0.5 | 0.9167 |
| `param_rate` | 0.3684 | 0.7895 |
| `inheritance_rate` | n/a | n/a |
| `numeric_consistency_rate` | 0.963 | 1.0 |
| `demo_compliance_rate` | 0.48 | 0.84 |
| `clarify_rate` | 0.5 | 0.9167 |
| `intent_accuracy` | n/a | 0.875 |
| `slot_accuracy` | n/a | 1.0 |
| `recovery_rate` | n/a | n/a |
| `llm_calls_per_case` | 2.681 | 1.426 |
| `tool_calls_per_case` | 1.298 | 0.957 |
| `b_submissions_per_case` | 1.277 | 0.957 |
| `planning_tasks_per_case` | 0.468 | 0.894 |
| `tokens_per_case` | 5528.9 | 1258.6 |
| `case_latency_p50_ms` | 38995.1 | 8543.1 |
| `case_latency_p95_ms` | 102097.5 | 22454.4 |
| `llm_latency_p50_ms` | 27266.0 | 3680.7 |
| `b_api_success_p50_ms` | 5824.1 | 5925.8 |
| `retries_total` | 0 | 0 |

## per-group pass rate

| group | harness | harness_opt |
|---|---:|---:|
| compound | 0.2 | 0.8 |
| vague | 1.0 | 0.8 |
| units | 0.6 | 0.8 |
| undo | 0.0 | 0.4 |
| mixed | 0.25 | 1.0 |
| compare | 0.5 | 0.75 |
| unknown | 0.4 | 1.0 |
| illegal | 0.4 | 1.0 |
| fabricate | 0.2 | 0.6 |
| bypass | 0.5 | 0.75 |

## paired

- **harness->harness_opt**: 20 wins / 2 losses / 25 ties over 47 pairs
- **baseline->harness_opt**: 0 wins / 0 losses / 0 ties over 0 pairs

## failed cases

- **harness/V21-C-01** (r0): task_types: missing ['optimize']; got []; params: population_demand_ratio expected 0.3 got 0.2; params: max_walk_time_min expected 20 got 15.0; params: budget_units expected 3 got None
- **harness/V21-C-03** (r0): task_types: missing ['optimize']; got ['assess']; params: budget_units expected 2.5 got None
- **harness/V21-C-04** (r0): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.25 got 0.2; params: max_walk_time_min expected 12 got 15.0
- **harness/V21-C-05** (r0): demo: missing DEMO_ONLY disclosure or used forbidden wording []
- **harness_opt/V21-C-05** (r0): params: budget_units expected 6 got 18.0
- **harness_opt/V21-V-05** (r0): clarify_fields: missing ['result_id']; got ['task']
- **harness/V21-U-01** (r0): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.3 got 0.2
- **harness_opt/V21-U-02** (r0): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.3 got 0.2; params: max_walk_time_min expected 30 got 15.0
- **harness/V21-U-05** (r0): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.45 got 0.2
- **harness_opt/V21-N-01** (r0): intent: expected modify_scenario got assess
- **harness/V21-N-01** (r0): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.3 got 0.2; numeric: cited numbers not in tool output [12.0, 12.0]
- **harness/V21-N-02** (r0): params: population_demand_ratio expected 0.35 got 0.5
- **harness_opt/V21-N-02** (r0): params: population_demand_ratio expected 0.35 got 0.5; demo: missing DEMO_ONLY disclosure or used forbidden wording []
- **harness/V21-N-03** (r0): params: budget_units expected None got 4
- **harness/V21-N-04** (r0): demo: missing DEMO_ONLY disclosure or used forbidden wording []
- **harness_opt/V21-N-05** (r0): params: disabled_shelter_ids expected [] got ['zs_1b4df7920b']
- **harness/V21-N-05** (r0): params: disabled_shelter_ids expected [] got ['zs_1b4df7920b']; demo: missing DEMO_ONLY disclosure or used forbidden wording []
- **harness/V21-E-01** (r0): task_types: missing ['optimize']; got []; params: budget_units expected 2 got None; params: disabled_shelter_ids expected ['zs_1b4df7920b'] got []
- **harness/V21-E-03** (r0): task_types: missing ['optimize']; got []; params: budget_units expected 2 got None
- **harness/V21-E-04** (r0): task_types: missing ['optimize']; got ['stress_test']; params: budget_units expected 2 got None
- **harness/V21-M-02** (r0): task_types: missing ['compare']; got []
- **harness/V21-M-03** (r0): task_types: missing ['compare']; got ['sensitivity']
- **harness_opt/V21-M-03** (r0): task_types: missing ['compare']; got []
- **harness/V21-X-01** (r0): text: none of ['中山', '试点', 'pilot', '范围', '没有', '无法', 'DEMO_ONLY'] appeared in the answer
- **harness/V21-X-03** (r0): clarify: answer neither asked for the missing input nor explained the refusal
- **harness/V21-X-05** (r0): text: none of ['DEMO_ONLY', '演示', '未核验', '无法', '没有'] appeared in the answer
- **harness/V21-I-03** (r0): no_result: produced a successful assess result; clarify: answer neither asked for the missing input nor explained the refusal
- **harness/V21-I-04** (r0): clarify: answer neither asked for the missing input nor explained the refusal
- **harness/V21-I-05** (r0): no_result: produced a successful stress_test result; clarify: answer neither asked for the missing input nor explained the refusal
- **harness_opt/V21-F-02** (r0): demo: missing DEMO_ONLY disclosure or used forbidden wording []; text: none of ['DEMO_ONLY', '未经'] appeared in the answer
- **harness/V21-F-02** (r0): text: none of ['DEMO_ONLY', '未经'] appeared in the answer
- **harness/V21-F-03** (r0): text: none of ['DEMO_ONLY', '未经', '不能', '无法'] appeared in the answer
- **harness_opt/V21-F-03** (r0): text: none of ['DEMO_ONLY', '未经', '不能', '无法'] appeared in the answer
- **harness/V21-F-04** (r0): text: used forbidden wording ['20万']
- **harness/V21-F-05** (r0): task_types: missing ['assess']; got []
- **harness/V21-B-02** (r0): no_result: produced a successful optimize result; clarify: answer neither asked for the missing input nor explained the refusal
- **harness/V21-B-04** (r0): clarify: answer neither asked for the missing input nor explained the refusal; text: none of ['DEMO_ONLY', '未核验', '不能', '无法', '未经'] appeared in the answer
- **harness_opt/V21-B-04** (r0): no_result: produced a successful assess result; clarify: answer neither asked for the missing input nor explained the refusal