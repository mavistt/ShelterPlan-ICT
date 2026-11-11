# 上游问题单（Module B）｜MIP 字典序的浮点下界导致 `optimality_proven=false`

- 报告方：Module C（Harness V0）
- 上游分支 / 提交：`feature/module-b` @ `5f8bb5126e11ac2e11b7abe77b824d736c354df5`
- 复现环境：Python 3.12.4 / numpy 1.26.4 / scipy 1.13.1（与 `requirements-b.txt` 区间一致）
- 影响面：MIP（预算优化）路径；不影响 LP 分配路径
- C 侧是否已绕过：**没有修改任何数学模型**；C 只做了"不假设 OPTIMAL"的话术与状态处理

---

## 1. 复现

```bash
python -m pytest tests/module_b/test_module_b.py::SolverTests::test_budget_mip_accepts_more_than_the_old_enumeration_bound -q
```

结果：`1 failed`。

```
AssertionError: False is not true
  test_module_b.py:84  assertTrue(result["optimality_proven"])
```

独立复现（不依赖测试）：

```python
from backend.graph.executor import load_fixture
from backend.engine import optimize_budgeted
from backend.models import ImprovementOption

data, scenario, options = load_fixture("tests/module_b/fixtures/toy_case.json")
base = options[0].model_dump()
zeros = [ImprovementOption(**{**base, "option_id": f"ZERO_{i}",
                              "delta_capacity_persons": 0.0,
                              "cost_units": 1.0,
                              "combination_policy": "stackable"}) for i in range(13)]
result = optimize_budgeted(data, scenario, zeros, 10)
print(result["solver_status"], result["optimality_proven"])
for stage in result["mip_stages"]:
    print(stage["stage"], stage["status"], stage["message"])
```

实际输出：

```
FEASIBLE False
maximize_served_demand       status=0  "Optimal"
minimize_weighted_walk_time  status=4  "Primal infeasible or unbounded"
```

期望：`OPTIMAL / True`，`selected_option_ids == []`。

---

## 2. 根因

`backend/engine.py:368-372`：

```python
if all_optimal:
    best_served = float(np.sum(primary.x[:edge_count]))
    served_row = np.zeros(var_count); served_row[:edge_count] = 1.0
    secondary_objective = np.zeros(var_count); secondary_objective[:edge_count] = walk_times
    secondary = run_stage("minimize_weighted_walk_time", secondary_objective,
                          [(served_row, best_served - NUMERIC_TOLERANCE, np.inf)])
```

`NUMERIC_TOLERANCE = 1e-9` 被当作**绝对量级**使用，但 `best_served` 是数千人量级的浮点求和：

- 第一阶段 LP/MIP 允许极小的约束违反（HiGHS 默认 feasibility tolerance ≈ 1e-7 相对量级）；
- `np.sum` 之后 `best_served` 可能比真实可行上界大 1e-13 ~ 1e-9；
- 于是 `served >= best_served - 1e-9` 在真实可行域内**无解**，第二阶段返回 `status=4`；
- `all_optimal` 被置为 `False`，最终返回 `FEASIBLE`。

同一个 1e-9 常量在 LP 路径（`engine.py:117-133`）也用于第二阶段，但那里 `best_served = -primary.fun` 直接来自目标函数值，量级误差表现不同，因此测试未暴露。

---

## 3. 影响

| 影响 | 说明 |
|---|---|
| 业务数值 | **不受影响**：`served_demand`、选中项、成本仍正确 |
| 最优性声明 | 受影响：本可证明最优的场景被降级为 `FEASIBLE` |
| 下游话术 | 必须按 `optimality_proven=false` 处理，不能说"已证明最优" |
| 基准指标 | `verified_task_success_rate` 仍为 1.0，但 `all_stages_optimal` 可能为 false |

C 侧已做的防御：

- `agent/harness/results.py:43 resolve_optimality()` 统一解析三处嵌套位置；
- `agent/harness/guards.py` 的 `claim="optimal"` 必须由 `optimality_proven=true` 支撑；
- V0 用例 P0-23 只对 `OPTIMAL` 结果允许"已证明最优"。

---

## 4. 建议修法（供 B 负责人评估，C 未实施）

按优先级：

1. **相对容差**：把下界改成 `best_served * (1 - 1e-9) - 1e-6` 之类的相对+绝对混合容差，或直接用 `best_served - max(1e-6, abs(best_served) * 1e-9)`。
2. **用目标函数值而非求和**：第二阶段下界取 `-primary.fun`（求解器返回的目标值），而不是对 `x` 重新求和。
3. **可行性回退**：第二阶段 infeasible 时，回退为 `final = primary` 且保持 `optimality_proven=True`（因为第一阶段确实证明了最优），仅把"步行时间最小化"标记为 `NOT_APPLIED`。当前代码的回退保留了 `primary`，但把 `all_optimal` 置 false，语义上偏保守。

推荐 1 + 3：既修容差，又保证"第一阶段已证明最优"这一事实不被第二阶段的失败抹掉。

---

## 5. 回归建议

修完后请确保：

```bash
python -m pytest tests/module_b -q          # 期望 30 passed
python -m backend.benchmark --split all --output outputs/module_b/bench/all
```

并检查 `mip_stages` 中四个阶段均为 `status=0`，且 `all_stages_optimal=true`。

C 侧的 25 条 P0 用例不依赖该修复（已按 `FEASIBLE` 合法处理），修复后无需改动。
