# Module C｜Harness V0 → V2

Agent 执行层：把 Module B 的规划服务包装成 **4 个粗粒度 MCP 工具**，并在外面加一层确定性的
会话状态、准入守卫、结果投影与执行 Trace。

V2 在此之上增加了**结构化意图 + 受控路由**（`intent/`、`workflow/`）与**确定性答案绑定**
（`structured_runner.py`）。V0（无模型）、V1（工具调用 Agent）保持不变，作为消融的 A/B 臂。

> 数据状态为 `DEMO_ONLY`，任何输出都不得表述为现场核验的应急建议。

---

## 目录

```
agent/
├── config.py            # 超时/重试/摘要预算/数据版本
├── errors.py            # C 错误码（GUARD_* / TOOL_ARGUMENT_* / B_* / JOB_* / TRANSPORT_*）
├── contracts.py         # ScenarioParams / ScenarioState / TaskOutcome
├── b_client.py          # HTTP 客户端：202→轮询→结果，错误归一化
├── service.py           # MCP 与评测共用的应用层
├── mcp_server.py        # 4 个 MCP 工具（stdio / Streamable HTTP）
├── llm_adapter.py       # OpenAI-compatible 适配器（V1）
├── agent_loop.py        # 工具调用 Agent 循环（V1，A/B 臂）
├── agent_tools.py       # 两臂的工具注册表
├── numeric_audit.py     # 数值引用审计
├── structured_runner.py # C/D 臂：意图→路由→执行→作答
├── intent/
│   ├── schema.py        # 意图信封、别名、JSON 修复
│   ├── extractor.py     # LLM 抽取 + 确定性修复 + 规则兜底
│   └── normalizer.py    # set/delta/scale/restore 作用于完整快照
├── workflow/
│   ├── router.py        # 意图 → 可执行计划（不重写 B 的技能图）
│   ├── controller.py    # 提交、错误分类、有限重试、数值绑定
│   └── answer.py        # 确定性作答（数字、DEMO_ONLY、最优性语义）
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
python -m pytest tests/module_c -q            # 全部离线测试
python -m tests.module_c.run_v0 --output outputs/module_c/v0          # 确定性 V0（25 条）
python -m tests.module_c.run_v1 --split holdout --output outputs/module_c/v1  # V1 双臂（历史）
python -m tests.module_c.run_v2 --arms all --reps 1 --output outputs/module_c/v2  # 四臂消融
```

V0 指标（25 条用例）：完成率 1.0、参数继承率 1.0、错误识别率 1.0、摘要一致率 1.0、
p50 ≈ 4.2 s / p95 ≈ 6.3 s、摘要最大 1,871 B。

详见 [`HARNESS_V0_REPORT.md`](../../docs/module_c/HARNESS_V0_REPORT.md)、
[`HARNESS_V1_REPORT.md`](../../docs/module_c/HARNESS_V1_REPORT.md)、
[`HARNESS_V2_REPORT.md`](../../docs/module_c/HARNESS_V2_REPORT.md)。

---

## 五条硬规则

1. **数字只能来自 B**。C 不计算覆盖率、缺口或成本；D 臂只是把 B 的数字格式化。
2. **每轮发送完整参数快照**。B 的 `parent_scenario_id` 只记血缘，不继承参数。
3. **模型不产出参数快照，只产出操作**（`set/delta/scale/restore`），由 C 作用在快照上。
4. **模型没提到的场所 ID 永远不进 B**（停用与扩容候选都做确定性过滤）。
5. **`PASS_DEMO` 不等于现场核验**。`DEMO_ONLY` 由程序化声明保证，不靠模型记得写。

---

## 相关文档

- [工具契约](../../docs/module_c/MCP_TOOL_CONTRACT.md)
- [AgentArts 接入](../../docs/module_c/AGENTARTS_INTEGRATION.md)
- [B 上游问题单](../../docs/module_c/upstream/MODULE_B_FLOAT_TOLERANCE_ISSUE.md)
