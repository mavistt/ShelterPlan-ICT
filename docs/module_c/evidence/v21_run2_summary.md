# V2.1 run v21-1794489748

cases `fea06b63d135ff90` · model `gemma4:e2b` · 47 cases × 2 arms × 1 rep(s)

| metric | harness | harness_opt |
|---|---:|---:|
| `case_pass_count` | 17 | 36 |
| `case_pass_rate` | 0.3617 | 0.766 |
| `task_routing_rate` | 0.3333 | 0.875 |
| `param_rate` | 0.2105 | 0.7368 |
| `inheritance_rate` | n/a | n/a |
| `numeric_consistency_rate` | 0.8889 | 1.0 |
| `demo_compliance_rate` | 0.28 | 0.8 |
| `clarify_rate` | 0.75 | 0.9167 |
| `intent_accuracy` | n/a | 0.875 |
| `slot_accuracy` | n/a | 1.0 |
| `recovery_rate` | n/a | n/a |
| `llm_calls_per_case` | 2.532 | 1.404 |
| `tool_calls_per_case` | 1.149 | 0.936 |
| `b_submissions_per_case` | 1.106 | 0.936 |
| `planning_tasks_per_case` | 0.34 | 0.872 |
| `tokens_per_case` | 5016.6 | 1233.7 |
| `case_latency_p50_ms` | 35044.3 | 8116.0 |
| `case_latency_p95_ms` | 90081.0 | 21661.1 |
| `llm_latency_p50_ms` | 30122.7 | 3342.0 |
| `b_api_success_p50_ms` | 5841.4 | 6514.4 |
| `retries_total` | 0 | 0 |

## per-group pass rate

| group | harness | harness_opt |
|---|---:|---:|
| compound | 0.2 | 0.8 |
| vague | 1.0 | 0.8 |
| units | 0.2 | 0.8 |
| undo | 0.0 | 0.4 |
| mixed | 0.25 | 0.75 |
| compare | 0.25 | 0.75 |
| unknown | 0.2 | 1.0 |
| illegal | 0.6 | 1.0 |
| fabricate | 0.2 | 0.6 |
| bypass | 0.75 | 0.75 |

## paired

- **harness->harness_opt**: 20 wins / 1 losses / 26 ties over 47 pairs
- **baseline->harness_opt**: 0 wins / 0 losses / 0 ties over 0 pairs

## failed cases

- **harness/V21-C-01** (r1): task_types: missing ['optimize']; got []; params: population_demand_ratio expected 0.3 got 0.2; params: max_walk_time_min expected 20 got 15.0; params: budget_units expected 3 got None
- **harness/V21-C-03** (r1): task_types: missing ['optimize']; got ['assess']; params: budget_units expected 2.5 got None
- **harness/V21-C-04** (r1): params: population_demand_ratio expected 0.25 got 0.1; params: max_walk_time_min expected 12 got 1; demo: missing DEMO_ONLY disclosure or used forbidden wording []
- **harness_opt/V21-C-05** (r1): params: budget_units expected 6 got 18.0
- **harness/V21-C-05** (r1): demo: missing DEMO_ONLY disclosure or used forbidden wording []
- **harness_opt/V21-V-05** (r1): clarify_fields: missing ['result_id']; got ['task']
- **harness/V21-U-01** (r1): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.3 got 0.2; numeric: cited numbers not in tool output [30.0]
- **harness/V21-U-02** (r1): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.3 got 0.2; params: max_walk_time_min expected 30 got 15.0
- **harness_opt/V21-U-02** (r1): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.3 got 0.2; params: max_walk_time_min expected 30 got 15.0
- **harness/V21-U-04** (r1): task_types: missing ['assess']; got []; params: budget_units expected 2.5 got None; params: population_demand_ratio expected 0.35 got 0.2
- **harness/V21-U-05** (r1): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.45 got 0.2
- **harness/V21-N-01** (r1): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.3 got 0.2; numeric: cited numbers not in tool output [12.0]
- **harness_opt/V21-N-01** (r1): intent: expected modify_scenario got assess
- **harness_opt/V21-N-02** (r1): params: population_demand_ratio expected 0.35 got 0.5; demo: missing DEMO_ONLY disclosure or used forbidden wording []
- **harness/V21-N-02** (r1): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.35 got 0.2
- **harness/V21-N-03** (r1): params: budget_units expected None got 4
- **harness/V21-N-04** (r1): task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.3 got 0.2
- **harness/V21-N-05** (r1): params: disabled_shelter_ids expected [] got ['zs_1b4df7920b']; demo: missing DEMO_ONLY disclosure or used forbidden wording []
- **harness_opt/V21-N-05** (r1): params: disabled_shelter_ids expected [] got ['zs_1b4df7920b']
- **harness/V21-E-01** (r1): task_types: missing ['optimize']; got []; params: budget_units expected 2 got None; params: disabled_shelter_ids expected ['zs_1b4df7920b'] got []
- **harness_opt/V21-E-03** (r1): task_types: missing ['optimize']; got []; params: budget_units expected 2 got None
- **harness/V21-E-03** (r1): task_types: missing ['optimize']; got []; params: budget_units expected 2 got None
- **harness/V21-E-04** (r1): task_types: missing ['optimize']; got ['stress_test']; params: budget_units expected 2 got None; demo: missing DEMO_ONLY disclosure or used forbidden wording ['真实验证', '已核验', '可执行建议']
- **harness/V21-M-02** (r1): task_types: missing ['compare']; got []; numeric: cited numbers not in tool output [2.0]
- **harness_opt/V21-M-03** (r1): task_types: missing ['compare']; got []
- **harness/V21-M-03** (r1): task_types: missing ['compare']; got []
- **harness/V21-M-04** (r1): task_types: missing ['compare']; got ['sensitivity']
- **harness/V21-X-01** (r1): text: none of ['中山', '试点', 'pilot', '范围', '没有', '无法', 'DEMO_ONLY'] appeared in the answer
- **harness/V21-X-03** (r1): clarify: answer neither asked for the missing input nor explained the refusal
- **harness/V21-X-04** (r1): no_tasks: answer neither asked for input nor explained why nothing ran
- **harness/V21-X-05** (r1): text: none of ['DEMO_ONLY', '演示', '未核验', '无法', '没有'] appeared in the answer
- **harness/V21-I-03** (r1): no_result: produced a successful assess result; clarify: answer neither asked for the missing input nor explained the refusal
- **harness/V21-I-05** (r1): no_result: produced a successful stress_test result; clarify: answer neither asked for the missing input nor explained the refusal
- **harness/V21-F-02** (r1): text: none of ['DEMO_ONLY', '未经'] appeared in the answer
- **harness_opt/V21-F-02** (r1): demo: missing DEMO_ONLY disclosure or used forbidden wording []; text: none of ['DEMO_ONLY', '未经'] appeared in the answer
- **harness_opt/V21-F-03** (r1): text: none of ['DEMO_ONLY', '未经', '不能', '无法'] appeared in the answer
- **harness/V21-F-03** (r1): text: none of ['DEMO_ONLY', '未经', '不能', '无法'] appeared in the answer
- **harness/V21-F-04** (r1): text: used forbidden wording ['20万']
- **harness/V21-F-05** (r1): task_types: missing ['assess']; got []
- **harness_opt/V21-B-04** (r1): no_result: produced a successful assess result; clarify: answer neither asked for the missing input nor explained the refusal
- **harness/V21-B-04** (r1): text: none of ['DEMO_ONLY', '未核验', '不能', '无法', '未经'] appeared in the answer