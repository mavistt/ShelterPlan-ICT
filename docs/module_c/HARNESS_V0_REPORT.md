# Module C｜Harness V0 实施与评测报告

> 分支：`feature/module-c`（本地，从上游 `feature/module-b` 的 `5f8bb5126e11ac2e11b7abe77b824d736c354df5` 创建）
> 工作目录：`E:/ICT/module-c`（真实 Git 工作树，非 tarball 快照）
> 本轮性质：**无模型的确定性 Harness V0**。下文所有指标描述的是"Harness + Module B"这条工程链路，**不是 LLM Agent 的任务成功率**，也不构成"Harness 提升智能体准确率"的证据。
> 运行日期：2026-11-12

---

## 1. 实际完成了什么

| # | 交付项 | 文件 | 状态 |
|---|---|---|---|
| 1 | B API Client（202→轮询→取结果，错误归一化，有界重试） | `agent/b_client.py` | ✅ 已实测 |
| 2 | MCP Server（4 个粗粒度工具，stdio + Streamable HTTP） | `agent/mcp_server.py` | ✅ 官方 SDK 客户端实测 |
| 3 | 多轮会话与情景状态（完整参数快照 + 不可变历史 + 血缘） | `agent/harness/session.py` | ✅ 4 项继承用例全过 |
| 4 | 执行前后 Guard（参数/版本/准入/资格/结果一致性/最优性声明） | `agent/harness/guards.py` | ✅ 25 条单测 |
| 5 | 异步执行、轮询、超时、作业丢失、有限重试 | `agent/harness/executor.py` | ✅ 含注入故障用例 |
| 6 | 大结果投影（≤20KB）与分页证据读取 | `agent/harness/results.py` | ✅ 7.3MB→1.6KB |
| 7 | 执行 Trace（JSONL，按 task_id 可追溯） | `agent/harness/trace.py` | ✅ 297 行原始 Trace |
| 8 | P0 测试任务集（25 条，可重复执行） | `tests/module_c/cases/p0_cases.json` | ✅ 25/25 |
| 9 | V0 评测运行器与报告 | `tests/module_c/run_v0.py` | ✅ 产出 report/trace/summary |
| 10 | 自动化测试 | `tests/module_c/test_*.py` | ✅ **66 passed / 0 failed**（112.2s） |

配套：`agent/service.py`（MCP 与评测共用的应用层）、`agent/config.py`、`agent/contracts.py`、`agent/errors.py`、`requirements-c.txt`。

**没有做的事**：未修改 `backend/` 任何一行；未改 OR-Tools/SciPy 模型与业务约束；未提交远程；未使用任何付费模型或云资源。

---

## 2. 哪些调用是真实 B API，哪些是注入/夹具

### 2.1 真实调用

| 场景 | 调用方式 | 说明 |
|---|---|---|
| 全部单元/集成测试（`test_b_client` / `test_executor`） | `starlette.testclient.TestClient(backend.app.app)` | **真实的 Module B FastAPI 应用**，真实路由、真实 Pydantic 契约、真实求解器、真实 Verifier、真实落盘。只是跳过网络栈 |
| MCP stdio 测试 | 子进程 `python -m agent.mcp_server` + 独立 uvicorn `backend.app:app` | 真实 HTTP + 真实 MCP 协议 |
| MCP HTTP 测试 | 同上，Streamable HTTP 传输 | 真实 HTTP，AgentArts 将来使用的形态 |
| V0 评测 25 条 | 同 TestClient | 真实 A 数据（`zhongshan_pilot_v0.1`，`DEMO_ONLY`） |

**没有任何一处用假求解器或预置结果替代 Module B。** 规划数字全部来自 B 的 `result_summary` / `result_detail`。

### 2.2 明确的注入与夹具（仅用于故障路径）

| 注入 | 实现 | 目的 | 是否伪造业务结果 |
|---|---|---|---|
| `FlakyClient` | 第 1 次请求抛 `httpx.ConnectError` | 验证有限重试 | 否 |
| `JobLostClient` | 第 2 次轮询返回 404 `JOB_NOT_FOUND` | 验证"服务重启丢作业"被识别 | 否 |
| `timeout` | `job_timeout_s=0.3, max_job_retries=0` | 验证有界等待 | 否 |
| `duplicate` | 同一 payload 提交两次 | 验证幂等键 | 否 |
| 扩容候选 | 5 个场所停用 + 1 条**显式 `hypothetical`** 扩容项 | 构造容量真正成为瓶颈的情景 | 候选成本/容量是**假设值**，B 结果里带 `HYPOTHETICAL_IMPROVEMENT` 警告 |

---

## 3. 自动化测试的真实执行结果

命令：

```bash
export PYTHONPATH="<repo>;<deps>"
python -m pytest tests/module_c -q -p no:cacheprovider
```

结果：**66 passed, 0 failed, 19 warnings in 112.20s**

| 测试文件 | 用例数 | 覆盖内容 |
|---|---:|---|
| `test_b_client.py` | 6 | capabilities、202 语义、404 映射、异步失败透传、传输重试与耗尽 |
| `test_session.py` | 8 | 只改命名参数、预算/停用保留、历史不可变、ID 唯一与父子链、非法键、复用 ID、未知结果、回滚 |
| `test_guards.py` | 25 | 比例/预算/阈值边界、缺预算、验证模式、版本、compare 数量、sensitivity 字段、候选合法性、结果错配、FAIL 拒绝、最优性声明、数值断言、DEMO_ONLY 表述 |
| `test_results.py` | 6 | 投影预算、数值一致、诊断聚合、超限裁剪、证据分页边界、未知分区 |
| `test_executor.py` | 16 | assess/optimize×3/compare/stress/multi-turn/explain、缺参不发起调用、超时、作业丢失、幂等、摘要一致、Trace 可查 |
| `test_mcp_stdio.py` | 4 | 工具发现=4、capabilities 准入、真实评估、缺预算拒绝 |
| `test_mcp_http.py` | 1 | Streamable HTTP 全链路 |

上游回归（未改动 B）：`python -m pytest tests/module_b -q` → **29 passed, 1 failed**，失败项与交接分析一致（B 的 MIP 浮点下界问题，见 §6.1）。

---

## 4. 关键异常案例是否符合预期

| 用例 | 场景 | 期望 | 实际 | 结论 |
|---|---|---|---|---|
| P0-12 | 缺预算执行 optimize | 提交前拒绝、零远程调用 | `GUARD_MISSING_PARAM`，`tool_calls=0` | ✅ |
| P0-13 | 需求比例 1.2 | 拒绝 | `GUARD_INVALID_PARAM` | ✅ |
| P0-14 | 预算 -1 | 拒绝 | `GUARD_INVALID_PARAM` | ✅ |
| P0-15 | 会话锁定到不存在的数据版本 | 拒绝 | `GUARD_VERSION_MISMATCH` | ✅ |
| P0-16 | DEMO_ONLY 上请求 verified | 拒绝 | `GUARD_NOT_VERIFIED_SOURCE` | ✅ |
| P0-17 | 解释不属于本会话的 result_id | 拒绝 | `GUARD_UNKNOWN_RESULT` | ✅ |
| P0-18 | compare 只给 1 个情景 | 拒绝 | `GUARD_INVALID_PARAM` | ✅ |
| P0-19 | 关停不存在的场所 | B 异步失败并透传 | `B_SCENARIO_INVALID`（来自 job.error） | ✅ |
| P0-20 | 作业超时 | 有界返回 | `JOB_TIMEOUT`，耗时 < 5s | ✅ |
| P0-21 | B 重启丢作业 | 识别而非死循环 | `JOB_STATE_LOST`，耗时 < 15s | ✅ |
| P0-25 | 首次连接失败 | 自动重试成功 | 成功，`attempts=2` | ✅ |
| P0-24 | 重复提交 | 同 `job_id` | 幂等命中 | ✅ |
| P0-11 | 三轮改参 | 历史不被改写 | 逐轮快照一致 | ✅ |
| P0-07/08/09/10 | 只改一个参数 | 其余参数不漂移 | 4/4 保持 | ✅ |

---

## 5. 性能与正确性指标（V0，2026-11-12）

```json
{
  "case_count": 25, "case_pass_count": 25, "case_pass_rate": 1.0,
  "tool_call_count": 67, "tool_success_rate": 0.9403,
  "tool_failures_by_design": 4, "unexpected_tool_failures": 0,
  "parameter_inheritance_rate": 1.0,
  "error_detection_rate": 1.0,
  "summary_consistency_rate": 1.0,
  "latency_p50_ms": 4196.617, "latency_p95_ms": 6335.734, "latency_mean_ms": 3706.016,
  "tool_calls_per_task": 2.68,
  "retry_total": 1,
  "summary_max_bytes": 1871, "summary_mean_bytes": 1629.3
}
```

解读（必须与限制一起读）：

- **工具成功率 94.03% 不是缺陷**：67 次调用中有 4 次是**故意注入**的失败（B 业务拒绝 1、超时 1、作业丢失 1、连接失败 1）。`unexpected_tool_failures = 0`。
- **参数继承率 1.0**：4 个多轮用例、共 4 次"未命名参数不得漂移"检查，全部通过。这是本轮针对 B 的 `parent_scenario_id` 缺陷做的核心补偿。
- **错误识别率 1.0**：10 条预期失败用例全部返回了预期错误码。
- **摘要大小**：均值 1,629 B、最大 1,871 B，对应完整结果 7.3 MB（assess）/ 11 MB（stress），**压缩约 99.98%**，且数值逐字段一致（`summary_consistency_rate = 1.0`）。
- **延迟**：p50 ≈ 4.2 s、p95 ≈ 6.3 s。其中约 2.5 s 是 **B 每次请求重新加载全量 A 数据**（7,965 需求点 / 191,160 OD），不是 Harness 开销。优化任务还要跑 4 阶段 MIP。
- **每个任务 2.68 次工具调用**：`submit_task` → `get_job` → `get_result` 三件套，符合"4 个粗工具"的设计。

**不得这样表述**：不能把这组数字写成"Agent 任务成功率 100%"，也不能写成"Harness 让 Agent 更快/更准"。本轮没有 LLM。

---

## 6. 仍受 B / A 哪些问题影响

### 6.1 B 的上游缺陷（不在 C 修复，已单独出问题单）

**① MIP 字典序的浮点下界缺陷（真实测试失败）**

`backend/engine.py:368-372` 把第一阶段解的浮点求和 `best_served = sum(primary.x[:edge_count])` 直接作为第二阶段下界 `served >= best_served - 1e-9`。当求和比真实可行上界大一点点时，第二阶段被判 infeasible，整体降级为 `FEASIBLE`。

- 复现：`tests/module_b/test_module_b.py:80`（13 个零增益候选），`pytest` 稳定 1 failed。
- 影响：业务结果数值仍正确，但"零增益候选集"这类场景无法声明最优。
- C 的应对：不假设 `OPTIMAL` 恒成立；`claim="optimal"` 必须由 `optimality_proven=true` 支撑；`FEASIBLE` 视为合法可发布状态。
- 问题单：`docs/module_c/upstream/MODULE_B_FLOAT_TOLERANCE_ISSUE.md`

**② `optimality_proven` 不在结果文档顶层**

`docs/module_b/AGENTARTS_GUIDE.md:43` 要求 C "把 `optimality_proven` 带到用户响应"，但实际结果只有 `solver_status` 在顶层，`optimality_proven` 位于 `result_detail.assignment` 或 `result_detail.optimization` 内（`backend/graph/executor.py:352-370` 未提升该字段）。

C 的处理：`agent/harness/results.py:43 resolve_optimality()` 统一解析三处位置。**建议 B 在顶层补充该字段**。

**③ `scenario_id` 会被 B 重新生成**

`backend/graph/executor.py:117-119`：当请求带 `changes` 且**请求级** `scenario_id` 为空时，B 用内容哈希覆盖 scenario 内的 id。C 因此必须同时发送 `scenario_id` 与 `scenario`（`agent/harness/executor.py:_build_payload`），否则会话 ID、结果 ID、Trace 会对不上。

**④ `parent_scenario_id` 必须已存在于 B 的情景账本**

`backend/graph/executor.py:149-154` 会校验父 ID 存在。C 规划但尚未执行的情景 B 不认识，因此只对"已有 result 的父情景"声明血缘（`_resolvable_parent`）。**会话级真实血缘保存在 C 自己的 `Session.lineage()` 中。**

**⑤ `parent_scenario_id` 不做参数继承**（交接分析已记录）

B 每次都用配置默认值重建情景，仅叠加本次 `changes`。C 的对策是每轮发送**完整参数快照**，不依赖 B 的继承语义。这条是 Harness 存在的主要理由之一。

### 6.2 A 的数据限制（不因 C 改变）

- `data_status = DEMO_ONLY`，A7 有 7 条 real-pilot blocker：24 个场所全部未核验、坐标为近似值、容量为 2024 登记值、分析多边形非官方边界。
- 因此所有结果上限是 `PASS_DEMO`；C 在摘要里强制写入 `disclosure.must_state`，并用 `assert_demo_disclosure()` 阻止"已核验/可执行建议"这类表述。
- 原始 922 MB 人口栅格在仓库中只是 LFS 指针，**原始数据链不可完整重建**（不影响已处理产物与 API）。

### 6.3 工程环境坑（已解决，需告知队友）

- **Windows 克隆必须关掉 CRLF 转换**：`core.autocrlf=true` 会改写 `demand_nodes.geojson` 等文本数据，导致 B 的 manifest SHA-256 校验失败（`B_DATA_INVALID`）。本轮通过 `git config core.autocrlf false` + 按 blob 重写工作树解决。
- **本机 HTTP 代理会劫持 127.0.0.1**：MCP Streamable HTTP 因此拿到 502。已在 `BClient` 对 loopback 关闭 `trust_env`，并在测试里设置 `NO_PROXY`。

---

## 7. 本地启动与测试

```bash
# 0) 依赖（Python 3.12；B 的依赖仍需先装好）
pip install -r requirements-b.txt -r requirements-c.txt

# 1) 起 Module B（必须在仓库根目录：结果路径是相对路径）
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

# 2) 起 MCP（stdio，给本地 Agent / Inspector）
python -m agent.mcp_server --transport stdio

# 3) 起 MCP（HTTP，给未来的 AgentArts）
python -m agent.mcp_server --transport http --host 127.0.0.1 --port 8010
#    端点：http://127.0.0.1:8010/mcp

# 4) 打印工具契约
python -m agent.mcp_server --list-tools

# 5) 测试
python -m pytest tests/module_c -q

# 6) V0 评测（默认走进程内 ASGI；加 --base-url 可打真实 HTTP）
python -m tests.module_c.run_v0 --output outputs/module_c/v0
python -m tests.module_c.run_v0 --base-url http://127.0.0.1:8000 --output outputs/module_c/v0-live
```

环境变量：`SHELTERPLAN_B_BASE_URL`、`SHELTERPLAN_JOB_TIMEOUT_S`、`SHELTERPLAN_SUMMARY_MAX_BYTES`、`SHELTERPLAN_TRACE_DIR`、`SHELTERPLAN_DATA_VERSION`、`SHELTERPLAN_DATA_STATUS`、`SHELTERPLAN_MCP_PORT`（见 `agent/config.py`）。

---

## 8. 下一轮（Harness V1 + AgentArts）的具体计划

### 8.1 前置条件

1. AgentArts 账号可用，能创建工作流并接入自定义 MCP（需 HTTPS 公网入口）。
2. B 侧确认两个契约问题：顶层补 `optimality_proven`；确认 `scenario_id` 不被覆盖（或明确由 C 传）。
3. 冻结 V0 的 25 条用例作为回归基线（已入库）。

### 8.2 V1 要做的四件事

| # | 工作 | 验收 |
|---|---|---|
| V1-1 | 把 MCP 部署到 HTTPS（反向代理 + Bearer 鉴权 + 仅暴露 4 工具） | AgentArts 控制台 `tools/list` 成功，调用 `get_capabilities` 返回 `DEMO_ONLY` |
| V1-2 | AgentArts 工作流：意图 → 槽位 → 确认 → `submit_task` → 轮询 → 解释 → 断言 | 5 条端到端 Trace（正常/缺参/失败/多轮/越权） |
| V1-3 | 解释一致性闸门接入工作流（`claims` + `assert_demo_disclosure`） | 注入"模型编造数字"用例被拦截，改写率可统计 |
| V1-4 | 同一 25 条用例跑 V0（脚本）与 V1（AgentArts），出对照表 | 工具序列正确率、参数继承率、数值一致率、p50/p95、token 成本 |

### 8.3 明确不做

- 不让 LLM 直接算覆盖率或预算；
- 不把 12 个原子 Skill 暴露成 12 个工具；
- 不在 C 侧复制业务校验（预算/容量/可达性仍以 B 的 Verifier 为准）；
- 不为了"看起来智能"放开自由工具选择——先用固定工作流跑通，再考虑 E6 对照实验。

### 8.4 预期风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| B 每请求重载 2.5s 数据 + 2 worker | V1 并发下 p95 显著上升 | 与 B 协商数据缓存/多 worker；C 侧限制并发并显式排队 |
| 结果文档 5–11 MB | MCP 传输/上下文爆炸 | 已用投影（1.6KB）；证据读取默认不暴露给模型 |
| DEMO_ONLY 被模型说成"已核验" | 合规风险 | 已在 MCP 指令 + 摘要 `disclosure` + `assert_demo_disclosure` 三重约束 |
| AgentArts 无法访问 localhost | 集成阻塞 | V1-1 先解决 HTTPS 入口 |
| 会话状态在进程内 | 重启丢上下文 | 会话由 C 管理，重启后要求重新确认参数；`result_id` 仍可从 B 取回 |

---

## 9. 复现清单（本轮产物）

| 产物 | 路径 |
|---|---|
| 代码 | `agent/`（9 个模块） |
| 测试 | `tests/module_c/`（6 个测试文件 + `conftest.py`） |
| 用例 | `tests/module_c/cases/p0_cases.json`（25 条） |
| 评测运行器 | `tests/module_c/run_v0.py` |
| V0 报告（机读） | `docs/module_c/evidence/v0_report.json` |
| V0 摘要（人读） | `docs/module_c/evidence/v0_summary.md` |
| 原始 Trace | `docs/module_c/evidence/v0_trace.jsonl`（297 行） |
| 工具契约 | `docs/module_c/MCP_TOOL_CONTRACT.md` |
| AgentArts 接入 | `docs/module_c/AGENTARTS_INTEGRATION.md` |
| B 问题单 | `docs/module_c/upstream/MODULE_B_FLOAT_TOLERANCE_ISSUE.md` |

### 运行环境

```
Python 3.12.4 · mcp 1.27.0 · fastapi 0.162.4 · pydantic 2.14.3
httpx 0.27.2 · numpy 1.26.4 · scipy 1.13.1 · pyarrow 18.1.0 · pytest 9.0.3
OS: Windows 11 (Git Bash) · core.autocrlf=false
```

### 已知偏差

- 依赖安装在隔离目录并以 `PYTHONPATH` 注入，而非仓库内 `.venv`（本机代理不稳定，`pip install -r requirements-b.txt` 多次超时）。
- `tests/test_gis_data.py`（A 侧）本轮未运行，缺 `pandas/geopandas`，与本轮 C 无关。
- V0 用例依赖特定演示数据的数值（如 6,456.103），若 A 数据版本升级需同步更新期望值。
