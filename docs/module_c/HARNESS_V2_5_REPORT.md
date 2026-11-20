# Module C｜V2.5 复杂组合任务、数学证据解释与工程加固报告

> **基线提交**：`05a8e27abac0e7835e090b4ccbb35149f725f219`（`feature/module-c`，干净工作树，274 项基线测试，`test_v21_cases.py` 双 SHA-256 冻结校验 8/8 通过）  
> **数据与边界**：仅修改 Module C 侧目录（`agent/`、`tests/module_c/`、`docs/module_c/`）；`backend/`、`data/`、`configs/`、`scripts/`、V2.1 冻结集与历史证据目录均未改动。所有数学结论严格受限于 `zhongshan_pilot_v0.1` (`DEMO_ONLY`) 及 B 引擎原生证据字段。

---

## 1. 第一步：接口与证据前置核查结论

在改动 C 侧代码前，先行核查 Module B（`backend/`）与数据配置真实接口，确认了以下五项硬边界：

1. **候选数量上限保持 `<= 12`**：尽管 `backend/engine.py:15` 定义了 `MAX_BUDGET_OPTIONS = 64`，但 HTTP API 契约 `backend/models.py:149` 将 `TaskRequest.improvement_options` 硬限制为 `max_length=12`。因此 C 侧抽取器与 Guard 保持 `<= 12` 限制，防止触发 422。
2. **`mip_stages` 缺少第一阶段数字目标值**：`backend/engine.py:352-356` 返回的 `mip_stages` 仅含 `stage`、`status`、`optimality_proven`、`message`、`mip_gap`、`mip_node_count`，**不含**第一阶段数值目标 `objective_value`。因此未经改造的 B 在线响应上，C 按三态判定规则将 `primary_stage_optimal` 严格记为 **`UNKNOWN`**（`stage1_objective_value_missing_in_b_evidence`），不把未核对数值的阶段状态包装成 `TRUE`。
3. **B 在线 API 不输出 Greedy 对照基线**：B 的 `GreedyExpansion`（`backend/evaluation/baselines.py:111-145`）每步调用 `solve_assignment` 评估真实网络增广，且仅存在离线评测模块中，在线 `execute_task()` 并不运行 Greedy。C 遵循“唯一计算源”原则，绝不自行按标称增量排序估算 Greedy；当 B 结果不含对照字段时，`greedy_comparison.status` 严格记为 **`unavailable`**。
4. **`REAL-000` / `REAL-081` 原始 JSON 尚未随仓提交**：团队报告中提及的 `REAL-000-TARGETED-TRAP` 与 `REAL-081` 原件位于实验工作区 `merge_data/v2_difficulty_discovery/`，当前仓库内无对应配置。因此本次在真实 `zhongshan_pilot_v0.1` 试点数据上构造了可复现的三候选背包竞争测试，明确命名为 **`C_CONSTRUCTED_3OPT_KNAPSACK`**，不冒称 `REAL-000`。
5. **B 已提供单调性检查原语**：`backend/verifier/counterfactual.py:5-31` 已实现带严格前置条件的 `verify_monotonicity()`，C 直接复用该原语并补全三态（`PASS` / `FAIL` / `INCONCLUSIVE`）前置核查。

---

## 2. 本轮改动内容与变更文件清单

### 2.1 分阶段实施内容
- **Phase 1（业务正确性与组合约束修复）**
  - 修复 `diagnose_intervene` 带候选时投影截断缺陷：`_detail_for()` 与 `_infer_task_type()` 在 `diagnose_intervene` 下同时保留 `diagnosis` 与 `optimization`（及 `intervention`），`router.py` 允许在有预算时复用会话 `last_options`，`answer.py` 同时输出瓶颈原因统计与预算内干预措施/成本/覆盖变化。
  - 升级 `explain_result`：在 `HarnessExecutor` 阶段从会话安全边界内读取被解释 `result_id` 的权威 `summary` 快照注入 `detail.explanation.target_summary`（受 20 KB 预算约束，超限时优先裁剪嵌套明细），`answer.py` 据此输出选中方案、成本预算、增量变化、第一阶段证明状态、Greedy 对照状态与 `HYPOTHETICAL` 声明。
  - 扩展组合策略抽取：在 `schema.py`、`extractor.py` 中支持 `option_id`（`项目A`/`方案1`/`OPT_*`）、`combination_policy`（`standalone` / `stackable` / `exclusive_group`）与 `exclusive_group`，改用按候选段切分的边界窗口提取各自成本与策略，并修正 `answer.py` 中 `选中措施：...。总成本...` 句界分割以防措施 ID 数字污染 `numeric_audit`。
  - 强化会话隔离与契约防护：`HarnessService.result()` 先查本会话已存摘要，若属于其他会话则抛 `GuardUnknownResult` 拒绝跨会话绑定，且重投影时保留当前会话完整 `scenario_params`；`contract.py` 新增 `hypothetical_assumption` 保护事实，阻止把 `HYPOTHETICAL` 假设成本改写为真实工程报价。

- **Phase 2（三态数学证据核验）**
  - 新增 `resolve_primary_stage_optimality()`：输出 `TRUE | FALSE | UNKNOWN` 三态。仅当 `mip_stages` 含 `maximize_served_demand`、`status == 0`、`optimality_proven is True` 且原生地记录了 stage-1 目标值并与 `final_served` 在 `max(1e-6, |v|*1e-9)` 容差内一致时才为 `TRUE`；阶段失败或数值不一致为 `FALSE`；缺少证据字段一律 `UNKNOWN`。
  - 实现证据驱动 `greedy_comparison`：仅当 B 结果显式携带 `greedy_comparison` / `greedy_baseline` 时记为 `available`，否则记为 `unavailable`。
  - 实现三态运行时单调性核验（`PASS | FAIL | INCONCLUSIVE`）：覆盖 `sensitivity`（`evaluate_series_monotonicity`）、`compare`（`evaluate_compare_monotonicity`）与父子跨轮同选项单参数变更（`evaluate_pair_monotonicity`），严格校验同数据版本、同区域、同任务类型、最优性证明及单轴可比性；如 B 未提供 `budget_units` 单点最优证明或 `compare` 同时修改多轴，均严谨判为 `INCONCLUSIVE`（不得视作 `PASS`）。

- **Phase 3（工程并发安全与可观测性加固）**
  - `SessionStore`（`agent/state_store.py`）：全部公共读写方法接入 `self._lock`，`_atomic_write` 使用 `pid + thread_ident + uuid` 临时文件名及 Windows `PermissionError` 短退避重试。
  - **并发支持边界**：明确标注当前 `SessionStore` **支持单进程多线程并发读写，不支持多进程共享同一文件目录并发写**（多进程横向扩展需替换为带事务/文件锁的外部存储）。
  - 全链路 Trace 与可观测性：`api.py` 与 `mcp_server.py` 将 `service.session_trace(session_id)` 注入 `StructuredRunner`，`run_turn(turn_id=...)` 打通 `turn_id -> intent/plan/answer -> tool_call -> execution_trace_id` 关联；`api.py` 补齐真实 `timings.queue_ms` 计算、owner-scoped FastAPI `lifespan` 优雅关闭；`service.py` 增加 `_result_cache` 64 MiB 字节容量 LRU 上限、`_capabilities` 30 秒 TTL 缓存及 B 离线 `b_service_status="degraded"` 降级返回；`structured_runner.py` 接入 `_state_lock`、`persist_snapshot()`、会话级 `session_defaults` 及历史数字滑动窗口裁剪。

### 2.2 变更文件清单（16 个 C 侧文件）

| 类别 | 文件路径 | 行尾 | 说明 |
|---|---|---|---|
| 修改 | `agent/harness/results.py` | CRLF | `diagnose_intervene`/`explain_result` 投影、三态 `resolve_primary_stage_optimality`、证据驱动 `greedy_comparison`、三态单调性核验 |
| 修改 | `agent/harness/executor.py` | CRLF | `explain_result` target_summary 注入、`options_digest` 记录、父子跨轮单调性检查 |
| 修改 | `agent/harness/session.py` | LF | `remember_result` 支持保存 `options_digest` |
| 修改 | `agent/harness/contract.py` | LF | 新增 `hypothetical_assumption` 受保护事实 |
| 修改 | `agent/intent/schema.py` | LF | `INTENT_SCHEMA_HINT` 增加 `option_id`/`combination_policy`/`exclusive_group` |
| 修改 | `agent/intent/extractor.py` | LF | `SYSTEM_PROMPT` 更新、分段窗口 `_rule_options`、LLM 缺失策略元数据补全 |
| 修改 | `agent/workflow/router.py` | LF | `diagnose_intervene` 在有预算时复用 `last_options` |
| 修改 | `agent/workflow/controller.py` | LF | 绑定 `primary_stage_optimal`、`greedy_comparison`、`monotonicity_check` |
| 修改 | `agent/workflow/answer.py` | LF | 完善 `diagnose_intervene`/`optimize`/`explain_result` 确定性模板与 `FEASIBLE`+阶段1注释、修复句界分隔 |
| 修改 | `agent/service.py` | CRLF | 跨会话 `result()` 隔离、`_infer_task_type` 顺序修复、64 MiB 缓存上限、`capabilities` 缓存与离线降级、`session_trace` |
| 修改 | `agent/state_store.py` | LF | 线程安全锁、线程唯一临时文件名与重试、单/多进程边界说明 |
| 修改 | `agent/structured_runner.py` | LF | `_state_lock` / `persist_snapshot()`、`turn_id` Trace 关联、`session_defaults`、滑动窗口裁剪 |
| 修改 | `agent/api.py` | LF | Trace 注入、`queue_ms`、`b_service_status` 透传、owner-scoped `lifespan` |
| 修改 | `agent/mcp_server.py` | LF | `_structured_runner` 注入 `session_trace` |
| 新增 | `tests/module_c/test_v25_scoped_improvements.py` | LF | 10 项 V2.5 专项测试（真实 A+B 引擎、并发读写、三态验证） |
| 新增 | `docs/module_c/HARNESS_V2_5_REPORT.md` | LF | 本报告 |

---

## 3. 实验与测试结果

### 3.1 分阶段与全量测试结果
| 阶段 | 命令范围 | 实测结果 |
|---|---|---|
| Phase 0 基线 | `pytest tests/module_c/test_v21_cases.py -q` & `--collect-only` | `8 passed`，基线收集 `274 tests` |
| Phase 1 业务修复 | `test_intent`, `test_results`, `test_workflow`, `test_executor`, `test_v22_behaviour`, `test_v23_semantics` | `113 passed, 1 warning` |
| Phase 2 数学证据 | `test_results`, `test_workflow`, `test_executor`, `test_session`, `test_v23_semantics` | `71 passed, 1 warning` |
| Phase 3 工程加固 | `test_api`, `test_api_http`, `test_mcp_http`, `test_mcp_stdio`, `test_mcp_structured`, `test_entry_points_agree`, `test_v24_fixes_and_regions` | `41 passed, 1 warning`（含双 uvicorn 真实进程 A+B+C HTTP 联调） |
| Phase 4 V2.5 专项 | `pytest tests/module_c/test_v25_scoped_improvements.py -v` | `10 passed in 97.71s` |
| Phase 4 全量回归 | `pytest tests/module_c -q` | **`284 passed, 3 warnings in 526.77s (0:08:46)`** |
| V2 历史复算 | `python -m tests.module_c.recompute_v2` | `OK: every stored number is reproducible from the raw records.` |
| 行尾与空白检查 | 行尾检查脚本 + `git diff --check` | `bad_eol: []`，CRLF/LF 100% 保持原制式 |

### 3.2 真实 A+B+C 组合优化关键实验指标（`C_CONSTRUCTED_3OPT_KNAPSACK`）
在真实 `zhongshan_pilot_v0.1` (`DEMO_ONLY`) 数据 + 真实 Module B 求解器 + `RuleLLM` 下，驱动完整 `/v1/sessions` -> `/turns` HTTP 对话流：
- **三候选背包反例（预算 20：`zs_6358642b6f` +60/成本11，`zs_e016bde4b3` +50/成本10，`zs_737caba8e8` +50/成本10）**：
  - 基线服务量：`6,456.103027` 人
  - B 离线 `GreedyExpansion` 对照：`OPT_A`，总成本 11，服务 `6,516.103027` 人
  - 经 C 自然语言下发至真实 B MIP：`OPT_B` + `OPT_C`，总成本 20，服务 `6,556.103027` 人（较基线 **+100.00 人**，较 Greedy 多服务 **+40.00 人**）
  - 第二轮自然语言追问 `“解释一下刚才的结果”`：正确绑定上一轮 `result_id`，输出 `OPT_B` / `OPT_C`、成本/增量、`主目标阶段最优核验=UNKNOWN`、`Greedy对照=unavailable`、`HYPOTHETICAL` 与 `DEMO_ONLY` 声明，并通过数值引用审计。
- **同场所可叠加（`stackable`，预算 2）**：两项同指向 `zs_6358642b6f`（+50 与 +60，成本各 1）成功同时入选，优化后服务 `6,566.103027` 人（+110.00 人）。
- **跨场所互斥组（`exclusive_group=G1`，预算 2）**：两项成本各 1（+50 与 +60），尽管预算足以全选，因互斥约束仅入选单项最优（+60.00 人，总服务 `6,516.103027` 人）。
- **单调性三态检查**：`max_walk_time_min=[10,15,20]` 敏感性分析与跨轮 `15->20 min` 评估均获 `PASS`；未记录单点 `optimality_proven` 的 `budget_units` 敏感性严守边界返回 `INCONCLUSIVE`；合成反向下降序列检出 `FAIL`。

---

## 4. 未运行项、局限性与后续消融建议

1. **当前未执行项**：
   - 未重新运行本地 Ollama 真实小模型（如 `gemma4:e2b`）在 V2.5 复杂组合用例上的在线 LLM 推理对照；本轮全量端到端测试使用的是 `RuleLLM` + 真实 B + 真实 A + 真实进程 HTTP/MCP。
   - 非作者盲测集（`v22_holdout.json`）及队友本地的 `REAL-000` / `REAL-081` 原件 JSON 仍待队友提供后补充实测。
2. **已知边界**：
   - 因未改 B 端代码，`mip_stages` 仍无第一阶段数值目标且 `budget_units` 敏感性单点缺少 `optimality_proven`，在线调用 B 时 `primary_stage_optimal` 与 `budget_units` 敏感性单调性分别呈保守的 `UNKNOWN` / `INCONCLUSIVE`（待 B 端配合扩展字段后即可自动升级为 `TRUE` / `PASS`）。
   - `SessionStore` 仅保证单进程多线程安全，不适用于多 worker 多进程共享本地目录。
3. **竞赛答辩消融实验建议（严禁混淆数学增益与 Agent 增益）**：
   - **底层求解器增益（已在 B 侧及 `C_CONSTRUCTED_3OPT_KNAPSACK` 实测）**：同一 GIS 图与候选条件下，展示 Greedy（6,516.10）vs 0–1 MIP（6,556.10）的背包全局优化差距。
   - **Agent Harness 架构增益（C 侧待补在线真模型消融表）**：在**同一本地模型（`gemma4:e2b`）、同一复杂组合任务集（含多候选成本/策略、多轮参数修改、`diagnose_intervene`、越权改声明与跨轮解释）**下，对比无状态基线（V1 / FlatToolAgent）与 V2.5 Harness Arm D 的四项独立指标：
     1. 业务执行与参数继承成功率；
     2. 组合约束（`stackable` / `exclusive_group` / 不同成本）槽位抽取准确率；
     3. 数学证据合规率（`numeric_audit`、`DEMO_ONLY`/`HYPOTHETICAL`、`FEASIBLE` 与 `UNKNOWN`/`unavailable` 诚实披露率）；
     4. Token 消耗与 p50/p95 端到端时延成本。
