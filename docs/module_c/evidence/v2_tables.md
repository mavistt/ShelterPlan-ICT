## 6.1 总体指标

> `n/a*`：baseline 走异步协议，B 的计算发生在两次工具调用之间，无法整段计时。

| 指标 | `baseline` | `harness` | `harness_struct` | `harness_opt` |
|---|---:|---:|---:|---:|
| 完整任务通过率 | 21.7% | 39.1% | 100.0% | 100.0% |
| 任务路由正确率 | 16.7% | 38.9% | 100.0% | 100.0% |
| 参数正确率 | 69.2% | 46.2% | 100.0% | 100.0% |
| 多轮继承正确率 | 62.5% | 50.0% | 100.0% | 100.0% |
| 数值解释一致率 | 100.0% | 88.9% | 100.0% | 100.0% |
| DEMO_ONLY 合规率 | 0.0% | 27.8% | 100.0% | 100.0% |
| 澄清/拒绝正确率 | 100.0% | 100.0% | 100.0% | 100.0% |
| 意图识别正确率 | n/a | n/a | 100.0% | 100.0% |
| 槽位提取正确率 | n/a | n/a | 100.0% | 100.0% |
| 错误恢复成功率 | 0.0% | 0.0% | 100.0% | 100.0% |
| LLM 调用/用例 | 4.435 | 3.565 | 3.087 | 1.522 |
| 业务任务/用例 | 0.261 | 0.565 | 1.217 | 1.217 |
| tokens/用例 | 6676.6 | 7679.4 | 3437.6 | 1368.7 |
| LLM 延迟 p50 (ms) | 31533.7 | 45686 | 36234.2 | 4356.8 |
| B API 延迟 p50 (ms，仅成功任务) | n/a* | 6257.8 | 7742.6 | 7565.4 |
| 端到端 p50 (ms) | 31549.9 | 53451.5 | 44050.8 | 11170.1 |
| 端到端 p95 (ms) | 71456.6 | 81406.5 | 84541.1 | 20842.4 |
| 重试总数 | 0 | 0 | 0 | 0 |

## 6.2 分组通过率

| 组 | `baseline` | `harness` | `harness_struct` | `harness_opt` |
|---|---:|---:|---:|---:|
| plan | 0% (0/6) | 50% (3/6) | 100% (6/6) | 100% (6/6) |
| multi | 0% (0/5) | 0% (0/5) | 100% (5/5) | 100% (5/5) |
| relative | 0% (0/4) | 25% (1/4) | 100% (4/4) | 100% (4/4) |
| clarify | 100% (5/5) | 100% (5/5) | 100% (5/5) | 100% (5/5) |
| recover | 0% (0/3) | 0% (0/3) | 100% (3/3) | 100% (3/3) |

## 6.3 逐条配对结果

| 用例 | 组 | baseline | harness | harness_struct | harness_opt | 说明 |
|---|---|---|---|---|---|---|
| V2-P-01 | plan | ❌ | ✅ | ✅ | ✅ | baseline: task_types: missing ['assess']; got [] |
| V2-P-02 | plan | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['optimize']; got [] / harness: task_types: missing ['optimize']; got []; params: budget_units expected 2 got None |
| V2-P-03 | plan | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['compare']; got [] / harness: demo: missing DEMO_ONLY disclosure or used forbidden wording [] |
| V2-P-04 | plan | ❌ | ✅ | ✅ | ✅ | baseline: demo: missing DEMO_ONLY disclosure or used forbidden wording [] |
| V2-P-05 | plan | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['diagnose_intervene']; got [] / harness: task_types: missing ['diagnose_intervene']; got [] |
| V2-P-06 | plan | ❌ | ✅ | ✅ | ✅ | baseline: demo: missing DEMO_ONLY disclosure or used forbidden wording [] |
| V2-M-01 | multi | ❌ | ❌ | ✅ | ✅ | baseline: params: population_demand_ratio expected 0.35 got 0.2; demo: missing DEMO_ONLY disclosure  / harness: task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.35 go |
| V2-M-02 | multi | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['optimize']; got [] / harness: demo: missing DEMO_ONLY disclosure or used forbidden wording [] |
| V2-M-03 | multi | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['assess']; got ['stress_test']; params: budget_units expected 2 got N / harness: task_types: missing ['assess']; got ['stress_test']; demo: missing DEMO_ONLY disclosure  |
| V2-M-04 | multi | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['assess']; got [] / harness: task_types: missing ['assess']; got [] |
| V2-M-05 | multi | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['assess']; got ['stress_test']; demo: missing DEMO_ONLY disclosure or / harness: task_types: missing ['assess']; got ['stress_test'] |
| V2-R-01 | relative | ❌ | ✅ | ✅ | ✅ | baseline: task_types: missing ['optimize']; got [] |
| V2-R-02 | relative | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['optimize']; got [] / harness: task_types: missing ['optimize']; got []; params: budget_units expected 2 got None |
| V2-R-03 | relative | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.35 got  / harness: task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.35 go |
| V2-R-04 | relative | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['optimize']; got [] / harness: params: budget_units expected 6 got 3; demo: missing DEMO_ONLY disclosure or used forbidde |
| V2-C-01 | clarify | ✅ | ✅ | ✅ | ✅ |  |
| V2-C-02 | clarify | ✅ | ✅ | ✅ | ✅ |  |
| V2-C-03 | clarify | ✅ | ✅ | ✅ | ✅ |  |
| V2-C-04 | clarify | ✅ | ✅ | ✅ | ✅ |  |
| V2-C-05 | clarify | ✅ | ✅ | ✅ | ✅ |  |
| V2-E-01 | recover | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['optimize']; got [] / harness: task_types: missing ['optimize']; got []; params: budget_units expected 2 got None |
| V2-E-02 | recover | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.4 got 0 / harness: task_types: missing ['assess']; got []; params: population_demand_ratio expected 0.4 got |
| V2-E-03 | recover | ❌ | ❌ | ✅ | ✅ | baseline: task_types: missing ['compare']; got [] / harness: task_types: missing ['compare']; got [] |

## 6.4 失败分类

| 失败类型 | baseline | harness | harness_struct | harness_opt |
|---|---:|---:|---:|---:|
| `demo_violation` | 5 | 4 | 0 | 0 |
| `none` | 5 | 9 | 23 | 23 |
| `numeric_violation` | 0 | 2 | 0 | 0 |
| `param_error` | 4 | 7 | 0 | 0 |
| `wrong_task` | 15 | 11 | 0 | 0 |

## 6.5 配对胜负

- **baseline->harness**：4 胜 / 0 负 / 19 平 （23 对）
- **harness->harness_struct**：14 胜 / 0 负 / 9 平 （23 对）
- **harness_struct->harness_opt**：0 胜 / 0 负 / 23 平 （23 对）
- **baseline->harness_opt**：18 胜 / 0 负 / 5 平 （23 对）
- **harness->harness_opt**：14 胜 / 0 负 / 9 平 （23 对）
