# Module C｜MCP 工具契约（V0）

服务名：`ShelterPlan` · 协议：MCP（stdio / Streamable HTTP）· SDK：`mcp 1.27.0`（FastMCP）

**默认只暴露 4 个工具。** Module B 的 12 个原子 Skill 是内部计算阶段，不是 Agent 决策，不暴露。

```
python -m agent.mcp_server --transport stdio
python -m agent.mcp_server --transport http --host 127.0.0.1 --port 8010   # /mcp
python -m agent.mcp_server --list-tools                                    # 打印本契约
```

---

## 1. `get_capabilities`

**输入**：无

**输出**

```jsonc
{
  "ok": true,
  "capabilities": {
    "skill_graph_version": "module-b-skillgraph-0.1.0",
    "task_types": ["assess","compare","diagnose_intervene","explain_result",
                   "optimize","sensitivity","stress_test"],
    "skill_count": 12,
    "composite_count": 5,
    "composites": [{"composite_id": "C01", "name": "AssessAndExplain"}, "..."],
    "solver": {"assignment": "SciPy HiGHS LP", "budget": "SciPy HiGHS mixed-integer optimization",
               "max_options": 64, "time_limit_seconds": 10.0, "objective_order": ["..."]},
    "data_modes": ["demo", "verified"],
    "data_version": "zhongshan_pilot_v0.1",
    "data_status": "DEMO_ONLY",
    "admission": {
      "demo_allowed": true,
      "verified_mode_allowed": false,
      "note": "Module A source is DEMO_ONLY. ... never presented as field-verified emergency guidance."
    },
    "tool_contract": {"exposed_tools": ["get_capabilities","submit_task","get_job","get_result"],
                      "atomic_skills_exposed": false,
                      "result_payload": "projected summary <= 20480 bytes"}
  }
}
```

用途：让 Agent 在规划前知道任务类型、求解边界与**数据准入状态**。`data_status` 必须原样出现在后续解释中。

---

## 2. `submit_task`

**输入**（JSON Schema 的 required 仅 `task_type`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_type` | string | ✅ | `assess` / `optimize` / `diagnose_intervene` / `stress_test` / `compare` / `sensitivity` / `explain_result` |
| `session_id` | string | | 会话标识，默认 `default`。**同一会话复用**才能继承参数 |
| `changes` | object | | **只放用户本轮明确修改的字段**：`population_demand_ratio`(0–1)、`max_walk_time_min`(>0)、`budget_units`(≥0)、`disabled_shelter_ids`(string[]) |
| `parameters` | object | | 任务专属：`compare` → `{"scenarios":[{...},{...}]}`（2–12 个）；`sensitivity` → `{"parameter":..., "values":[...]}`（1–20 个） |
| `improvement_options` | array | | 扩容候选（≤12）。字段见下 |
| `result_id` | string | | 仅 `explain_result` 必填，且必须是本会话产生过的结果 |
| `claim` | string | | 传 `"optimal"` 表示回答将声称"已证明最优"；**会被 `optimality_proven` 强制校验** |

`improvement_options[]` 单项：

```jsonc
{
  "option_id": "CAP_ZS_FED820D94E_300",
  "shelter_id": "zs_fed820d94e",
  "action": "hypothetical_capacity_increase",
  "delta_capacity_persons": 300,     // >= 0
  "cost_units": 1,                    // >= 0，抽象成本单位，非工程造价
  "eligible": true,
  "source_status": "hypothetical_assumption",
  "hypothetical": true,               // hypothetical 与 human_confirmed 不能同时为 false
  "human_confirmed": false,
  "combination_policy": "standalone"  // standalone | stackable | exclusive_group
  // exclusive_group 时还需 "exclusive_group": "<组名>"
}
```

**输出（成功）**

```jsonc
{
  "ok": true,
  "task": {
    "ok": true,
    "task_type": "assess",
    "task_id": "c-<uuid>",
    "session_id": "default",
    "scenario": {                                  // C 的权威情景快照
      "scenario_id": "c-default-t02-1a2b3c4d",
      "parent_scenario_id": "c-default-t01-9f8e7d6c",
      "turn": 2, "changes": {"max_walk_time_min": 20},
      "params": {"population_demand_ratio": 0.35, "max_walk_time_min": 20,
                 "budget_units": null, "disabled_shelter_ids": [],
                 "data_version": "zhongshan_pilot_v0.1", "analysis_mode": "demo"},
      "result_id": "...", "created_at": "..."
    },
    "job_id": "...", "result_id": "...", "execution_trace_id": "...",
    "data_version": "zhongshan_pilot_v0.1", "data_status": "DEMO_ONLY",
    "solver_status": "OPTIMAL", "optimality_proven": true,
    "verification_status": "PASS_DEMO",
    "warnings": ["DEMO_ONLY: Module A handoff has not passed real-pilot acceptance; ..."],
    "summary": {                                   // ≤ 20 KB 的投影
      "result_id": "...", "task_id": "...", "task_type": "assess",
      "scenario_id": "...", "parent_scenario_id": "...", "scenario_changes": {},
      "data_version": "zhongshan_pilot_v0.1", "data_status": "DEMO_ONLY",
      "analysis_mode": "demo", "solver_status": "OPTIMAL", "optimality_proven": true,
      "verification_status": "PASS_DEMO",
      "metrics": {"served_demand": 6456.103, "total_demand": 110205.741,
                  "weighted_walk_time_min": 9.0376, "optimized_served_demand": null,
                  "diagnosed_unserved_nodes": 7634},
      "warnings": ["..."],
      "evidence": {"assignments": 354, "deficits": 7965, "diagnosis_rows": 7634,
                   "certificate_checks": 8346, "trace_rows": 6},
      "provenance": {"skill_graph_version": "...", "execution_trace_id": "...",
                     "scenario_hash": "...", "solver_version": "SciPy 1.13.1 HiGHS",
                     "source_hash": "..."},
      "disclosure": {"demo_only": true, "verified_for_real_planning": false,
                     "must_state": "DEMO_ONLY — 未经真实现场核验，不得作为应急执行建议"},
      "detail": { "...": "任务相关，见下" },
      "summary_bytes": 1559
    },
    "attempts": 1, "retries": 0, "duration_ms": 2712.4,
    "summary_bytes": 1559,
    "tool_calls": [{"tool_name": "submit_task", "...": "..."}]
  }
}
```

`detail` 按任务类型：

| task_type | `detail` 键 |
|---|---|
| `assess` / `explain_result` | `diagnosis{unserved_node_count, by_cause, unserved_persons_by_cause}` |
| `optimize` | `optimization{selected_option_ids, total_cost_units, budget_units, served_demand, weighted_walk_time_min, mip_stage_count, all_stages_optimal}` |
| `diagnose_intervene` | `diagnosis{...}` + `intervention{status, reason_code, message}` |
| `stress_test` | `stress{stressed_scenario_id, disabled_shelter_ids, fixed_plan{...}, adaptive_replan{...}, comparison_semantics}` |
| `compare` | `comparison{scenario_count, scenarios[], unique_recommendation:null}` |
| `sensitivity` | `sensitivity{parameter, point_count, points[]}` |

> ⚠️ **语义坑**：`optimize` 的根级 `metrics.served_demand` 是**优化前基线**；优化后的总量在 `detail.optimization.served_demand`。解释时必须区分。

**输出（失败）**

```jsonc
{
  "ok": true,                        // 工具调用本身成功
  "task": {
    "ok": false,
    "error": {
      "code": "GUARD_MISSING_PARAM",
      "message": "A confirmed budget is required before optimizing improvements.",
      "details": {"field": "budget_units"},
      "retryable": false,
      "user_action": "Ask the user for the missing value; never infer a real budget or ratio."
    },
    "tool_calls": [],                 // 预检失败时没有任何远程调用
    "duration_ms": 0.4
  }
}
```

C 的错误码：

| code | 含义 | 可重试 |
|---|---|---|
| `GUARD_MISSING_PARAM` | 缺预算/阈值/比例/关停名单/result_id | 否（先问用户） |
| `GUARD_INVALID_PARAM` | 越界、类型错、字段不支持、compare 数量不对 | 否 |
| `GUARD_VERSION_MISMATCH` | 会话数据版本与 B 源不一致 | 否 |
| `GUARD_NOT_VERIFIED_SOURCE` | DEMO_ONLY 上请求 verified | 否 |
| `GUARD_UNKNOWN_RESULT` | 引用了本会话没产生过的 result_id | 否 |
| `GUARD_INVALID_OPTION` | 候选重复/负成本/资格冲突 | 否 |
| `GUARD_CLAIM_VIOLATION` | 声称最优但未证明 / 数字与结果不符 / DEMO_ONLY 说成已核验 | 否 |
| `B_SCENARIO_INVALID` | B 拒绝情景（含 `VERSION_MISMATCH`、未知场所） | 否 |
| `B_CANDIDATE_INVALID` | B 拒绝候选集 | 否 |
| `B_SOLVER_TIMEOUT` | 无可行解产出 | 是（同键一次） |
| `B_INTERNAL_ERROR` | B 未捕获异常 | 否 |
| `TRANSPORT_ERROR` | 连接失败（已内部重试） | 是 |
| `JOB_TIMEOUT` | 超过 `job_timeout_s` | 是（同键） |
| `JOB_STATE_LOST` | B 重启丢作业表（轮询 404） | 是（需新逻辑任务） |
| `RESULT_NOT_FOUND` / `RESULT_MISMATCH` | 结果缺失/不属于该情景 | 否 |
| `INTERNAL_ERROR` | Harness 未捕获异常 | 否 |

---

## 3. `get_job`

**输入**：`{"job_id": "..."}`

**输出**：`{"ok": true, "job": {<Module B job 原样>}}`

主要用于观测与审计。`submit_task` 已经同步等待完成；该工具在 V1 异步化时才成为主路径。

---

## 4. `get_result`

**输入**：`{"result_id": "...", "session_id": "default"}`

**输出**：`{"ok": true, "result": <同 submit_task 的 summary 结构>}`

仅返回投影（≤20 KB）。**绝不返回 5–11 MB 原始文档。**

---

## 5. `handle_request`（V2 结构化入口，默认不注册）

**开关**：`SHELTERPLAN_MCP_STRUCTURED=1`

**定位**：给"不想自己选工具"的客户端一个自然语言入口。它内部执行
`意图抽取 → 固定路由 → Module B → 确定性作答`，**不暴露 12 个原子技能**，
也不把 B 的嵌套参数结构交给模型。

**输入**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | string | 是 | 用户原话（中文） |
| `session_id` | string | 否 | 会话 ID；复用它才能继承上一轮参数 |

**输出**

```jsonc
{
  "ok": true,
  "answer": "【结论】…【数据声明】DEMO_ONLY — …",
  "intent": "modify_scenario",
  "task_type": "optimize",
  "action": "task",
  "clarification_fields": [],
  "result_id": "5a9a0087-…",
  "llm_calls": 1,
  "error": null
}
```

`action=clarify` 时 `answer` 是一句澄清问题，`clarification_fields` 给出缺失字段，
`result_id=null`（未执行任何任务）。同一个 `session_id` 的 `TurnMemory`
（上一轮任务、候选措施、上一条 result_id）在进程内保留。

---

## 6. `get_evidence`（默认不注册）

设置 `SHELTERPLAN_MCP_EXPOSE_EVIDENCE=1` 后才会出现在 `tools/list` 中。用于审计，不给模型：

```
get_evidence(result_id, section, offset=0, limit=50)
```

`section` ∈ `assignments` | `deficits` | `shelter_loads` | `diagnosis` | `checks` | `optimization_checks` | `trace` | `compare_rows` | `sensitivity_points`

`limit` 上限 200；返回 `{section, offset, limit, total, has_more, items}`。

---

## 7. 调用顺序（Harness 强约束）

```
get_capabilities
   → submit_task（内部：submit → poll job → fetch result → postflight → project）
   → （如需审计）get_evidence
```

禁止：未 `submit_task` 直接 `get_result`（需已知 result_id）；对 `FEASIBLE` 结果使用"已证明最优"；把 `PASS_DEMO` 说成现场核验。
