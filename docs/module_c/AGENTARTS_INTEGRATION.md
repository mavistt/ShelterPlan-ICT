# Module C｜AgentArts 接入说明（下一轮）

本轮不访问 AgentArts、不部署云端。本文给出接入时**只需要做**的事，以及**不要重复做**的事。

---

## 1. 组件归属

| 组件 | 位置 | 理由 |
|---|---|---|
| Module B（求解 + Verifier + 结果存储） | 本地 / 云主机 Python 服务 | SciPy/OR-Tools 计算密集，AgentArts 无法承载 |
| Module C MCP Server | 独立进程，暴露 HTTPS | AgentArts 只能访问公网 HTTPS，不能访问 localhost |
| 会话状态、Guard、重试、Trace、结果投影 | **C 本地**（已实现） | 这些是确定性约束，不能交给 Prompt |
| 意图理解、槽位抽取、追问、解释生成 | **AgentArts 工作流** | LLM 的正当职责 |
| 批量评测与 V0/V1 对照 | C 本地 | 需要确定性判定与长期存储 |

**单一真值**：所有规划数字只能来自 MCP 返回的 `summary.metrics` / `summary.detail`。

---

## 2. HTTPS 暴露（V1-1）

```
AgentArts  ──HTTPS──▶  反向代理（Nginx/Caddy）  ──HTTP──▶  agent.mcp_server --transport http :8010
                                                              └──HTTP──▶  backend.app:8000
```

要求：

1. TLS 证书有效（AgentArts 侧校验证书链）。
2. **鉴权**：至少 Bearer Token；MCP Server 前置代理校验，不裸奔。
3. 只放行 `/mcp`；不暴露 `/docs`、`/planning/*`。
4. `SHELTERPLAN_B_BASE_URL` 指向内网 B 服务；B 不直接暴露公网。
5. 建议加请求体大小与速率限制（B 单次结果 5–11 MB，MCP 投影后 <20 KB）。

验证：

```bash
curl -s https://<host>/mcp -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## 3. 工作流节点设计

```
[Start]
  → LLM：识别任务类型（7 选 1）
  → LLM：抽取槽位（ratio / threshold / budget / disabled / options / 多轮指代）
  → Condition：槽位齐全？
       否 → LLM：只追问缺的那一个 → 回槽位
       是 → Code：调用 C 的会话状态（回填未修改参数）★
  → Condition：data_status == DEMO_ONLY？
       是 → 打标 DEMO_ONLY（后续解释强制附带）
  → MCP：submit_task
  → Condition：task.ok？
       否 → Code：按 error.code 分支
              GUARD_MISSING_PARAM   → 追问
              GUARD_NOT_VERIFIED    → 说明数据准入
              JOB_TIMEOUT           → 同键重试 1 次
              JOB_STATE_LOST        → 提示重新发起
              B_*                   → 解释业务原因，不编方案
       是 → LLM：基于 summary 生成解释
  → Code：claims 断言（数值 + DEMO_ONLY 表述）★
       不一致 → LLM 重写一次 → 仍不一致 → 降级为"只返回结构化结果"
  → 输出 + 写 Trace（join result_id / execution_trace_id）
```

★ 这两步**必须**调用本地 C 的能力，而不是在 AgentArts 里重写：

- 参数回填：`Session.plan_turn()` 的语义（只覆盖命名参数）。
- 断言：`agent.harness.guards.assert_claims_consistent()` / `assert_demo_disclosure()`。

如果 AgentArts 不能调用本地 HTTP，则把这两个能力也包成 MCP 工具（`prepare_turn`、`check_claims`），**不要在 Code 节点里重新实现一套**。

---

## 4. 适合 Code / Condition 节点的逻辑

| 逻辑 | 放在哪 | 说明 |
|---|---|---|
| 槽位归一化（30% → 0.3）、单位校验 | Code | 纯函数，无副作用 |
| 参数回填（继承上一轮） | Code 调 C | 必须以 C 的会话状态为准 |
| `data_status` / `verification_status` / `optimality_proven` 分支 | Condition | 三个布尔决定话术 |
| 重试计数与停止条件 | Condition + Code | 最多 1 次，失败即停 |
| 数值断言 | Code 调 C | 不允许 LLM 自评 |
| 业务数值计算 | **禁止** | 必须回到 B |

---

## 5. Trace 对齐

C 的 Trace（JSONL）已包含：`task_id`、`session_id`、`turn`、`scenario_id`、`parent_scenario_id`、`data_version`、`tool_name`、`args_digest`、`job_id`、`result_id`、`execution_trace_id`、`started_at/finished_at`、`duration_ms`、`attempt/attempts/retries`、`status`、`error_type`。

AgentArts 侧只需补：`agentarts_trace_id`、`model`、`prompt_version`、`tokens_in/out`，用 `result_id` 或 `execution_trace_id` join。

**不得**用 C 的 V0 Trace 冒充 AgentArts Trace。

---

## 6. V0 / V1 公平对照

| 维度 | V0（本轮） | V1 |
|---|---|---|
| 模型 | 无 | 固定模型 + 固定 prompt 版本 |
| 任务 | 25 条 P0（冻结） | 同一 25 条 |
| 数据 | `zhongshan_pilot_v0.1` / `DEMO_ONLY` | 同一 |
| 判定 | 期望工具序列 + 数值 + 错误码 | 同 V0 + 解释一致性 |
| 指标 | 完成率、工具序列、继承率、错误识别、摘要大小、p50/p95 | 加 token 成本、重写率、越权表述率 |

对照结论只能写成："在同一 25 条用例、同一数据版本下，V1 的工具序列正确率为 X%，V0 为 100%"——V0 是工程基线，不是"智能体"。

---

## 7. 上云前检查清单

- [ ] HTTPS 可达，证书有效，Bearer 鉴权生效
- [ ] `tools/list` 恰好 4 个工具，无 12 个原子技能
- [ ] `get_capabilities` 返回 `data_status=DEMO_ONLY`
- [ ] `submit_task` 返回 `summary_bytes < 20480`
- [ ] 缺预算时返回 `GUARD_MISSING_PARAM` 且 `tool_calls=[]`
- [ ] 一条完整 Trace 能从 `agentarts_trace_id` join 到 `result_id`
- [ ] 断网 / 超时 / `FAILED` 各有一条真实失败 Trace
- [ ] 解释中每条数字都能在 `summary` 中找到
