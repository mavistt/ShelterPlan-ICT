# Module C｜Harness V0

Agent 执行层：把 Module B 的规划服务包装成 **4 个粗粒度 MCP 工具**，并在外面加一层确定性的会话状态、准入守卫、结果投影与执行 Trace。

> 当前是**无模型**的 V0：它证明"接口、状态、异常、证据"这条链路是对的，不是"智能体效果"。
> 数据状态为 `DEMO_ONLY`，任何输出都不得表述为现场核验的应急建议。

---

## 目录

```
agent/
├── config.py            # 超时/重试/摘要预算/数据版本
├── errors.py            # C 错误码（GUARD_* / B_* / JOB_* / TRANSPORT_*）
├── contracts.py         # ScenarioParams / ScenarioState / TaskOutcome
├── b_client.py          # HTTP 客户端：202→轮询→结果，错误归一化
├── service.py           # MCP 与评测共用的应用层
├── mcp_server.py        # 4 个 MCP 工具（stdio / Streamable HTTP）
└── harness/
    ├── session.py       # 完整参数快照 + 不可变历史 + 血缘
    ├── guards.py        # 执行前/后校验、数值断言、DEMO_ONLY 表述闸门
    ├── results.py       # 5–11 MB → ≤20 KB 投影；分页证据
    ├── executor.py      # 编排、轮询、重试、终止条件
    └── trace.py         # JSONL Trace（按 task_id 可追溯）
```

---

## 快速开始

```bash
pip install -r requirements-b.txt -r requirements-c.txt

# Module B（必须在仓库根目录）
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

# MCP（stdio）
python -m agent.mcp_server --transport stdio

# MCP（HTTP，路径 /mcp）
python -m agent.mcp_server --transport http --host 127.0.0.1 --port 8010

# 工具契约
python -m agent.mcp_server --list-tools
```

客户端侧环境变量：`SHELTERPLAN_B_BASE_URL`（默认 `http://127.0.0.1:8000`）。

---

## 测试与评测

```bash
python -m pytest tests/module_c -q            # 66 passed
python -m tests.module_c.run_v0 --output outputs/module_c/v0
```

V0 指标（25 条用例）：完成率 1.0、参数继承率 1.0、错误识别率 1.0、摘要一致率 1.0、
p50 ≈ 4.2 s / p95 ≈ 6.3 s、摘要最大 1,871 B。

详见 [`docs/module_c/HARNESS_V0_REPORT.md`](../../docs/module_c/HARNESS_V0_REPORT.md)。

---

## 三条硬规则

1. **数字只能来自 B**。C 不计算覆盖率、缺口或成本。
2. **每轮发送完整参数快照**。B 的 `parent_scenario_id` 只记血缘，不继承参数。
3. **`PASS_DEMO` 不等于现场核验**。`DEMO_ONLY` 必须出现在解释里。

---

## 相关文档

- [工具契约](../../docs/module_c/MCP_TOOL_CONTRACT.md)
- [AgentArts 接入](../../docs/module_c/AGENTARTS_INTEGRATION.md)
- [B 上游问题单](../../docs/module_c/upstream/MODULE_B_FLOAT_TOLERANCE_ISSUE.md)
