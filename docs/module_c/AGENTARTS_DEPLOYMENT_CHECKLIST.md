# Module C｜AgentArts 集成准备与合并依赖清单

> **状态：准备完成，平台联调未开始。** 本轮**没有**取得 AgentArts 平台资源，
> 没有部署任何节点，没有调用任何收费云服务，也**不声称**云侧已经验证通过。
> 本文是"平台开放后要验什么、谁来验、什么条件才算通过"的清单。
> 现有节点映射见 `V21_AGENTARTS_MAPPING.md`（U-1…U-10），接入说明见
> `AGENTARTS_INTEGRATION.md`；本文只补**可验收的判据**与**合并前依赖**。

---

## 1. 复用原则（先写死，防止上云时重做）

- **只有一条计算路径**：`StructuredRunner → TaskController → HarnessService → BClient → Module B`。
  AgentArts 的工作流只做编排/鉴权/展示，**不重新实现** B 的求解、Verifier 或任何数值计算。
- **MCP 保留**：`agent/mcp_server.py` 的 4 个粗粒度工具继续作为节点入口或旁路调试入口，
  不在云侧另写一套工具。
- **状态留在 C**：会话快照、澄清挂起任务、结果注册表由 `HarnessService`/`SessionStore` 拥有；
  B 的 `parent_scenario_id` 只当血缘，不当参数继承来源。
- **答案与声明由 C 投影**：`compose_answer` / `compose_refusal` 从 B 的 summary 取值；
  平台节点不得自行拼接数字或删改 `DEMO_ONLY` 声明。

---

## 2. 节点映射与验收判据

| # | AgentArts 节点 | 复用的 C 模块 | 输入 → 输出 | 上云必须验证 |
|---|---|---|---|---|
| N1 | 入口 / 鉴权 | 平台网关（C 尚无） | HTTP → `session_id` | Bearer/OAuth、租户隔离、请求体 ≤ 4000 字符 |
| N2 | 意图解析 | `agent/intent/extractor.py` + `quantities.py` | `text` → `ParsedIntent` | 云端 LLM 的结构化输出稳定性；`rules_fallback` 仍能兜底 |
| N3 | 对话状态 | `agent/workflow/dialogue.py` + `state_store.py` | `PendingTask` ↔ `turn_id` | 多副本下状态一致性（当前单进程内存 + 落盘） |
| N4 | 准入 / 契约 | `agent/harness/contract.py` + `guards.py` | `text, config` → `refuse` | 配置注入可信；`DEMO_ONLY` 不可被对话改写 |
| N5 | 路由 | `agent/workflow/router.py` | `intent` → `RoutePlan` | 不因平台重试重复提交 B |
| N6 | 执行与轮询 | `TaskController` + `BClient` | `POST /planning/tasks` → 202 → job → result | 内网/公网可达性、超时、幂等键 |
| N7 | 出话与审计 | `answer.py` + `numeric_audit.py` | summary → 最终文本 | 数字只来自 B；`FEASIBLE` 不得写成"已证明最优" |
| N8 | 结果图层 | `agent/api.py::result_layers` | `result_id` → 24 行表格 | B 无 geometry，D 需自备底图按 `shelter_id` 关联 |
| N9 | Trace | `TraceRecorder` / `trace.jsonl` | `turn_id/result_id` → 事件流 | 平台 trace id 与 C 的四元组关联（见 §7） |

**每个节点的通过条件**：用同一条自然语言请求分别经 MCP 工具与 HTTP API 调用，
`served_demand` / `total_demand` / `weighted_walk_time_min` 逐项相等
（本机已由 `test_entry_points_agree.py` 证明；云侧必须重跑一次）。

---

## 3. 网络 / HTTPS

| 项 | 本机现状 | 上云待验 | 负责人 |
|---|---|---|---|
| C → B | `http://127.0.0.1:8000`，`SHELTERPLAN_B_BASE_URL` 可配 | 沙箱能否访问内网 HTTP；若只能公网 HTTPS 需反向代理 | 平台 / B |
| B 暴露面 | 无 CORS、无鉴权、无限流，绑定 127.0.0.1 | **B 不得直接暴露公网**；由网关代理并加 TLS | 平台 / B |
| C 对外 | `CORSMiddleware allow_origins=["*"]`（本地 demo） | 收紧为 D 的域名；HTTPS 终止与 HSTS | 平台 / D |
| MCP 托管 | stdio / Streamable HTTP 长驻进程 | 平台是否托管长驻进程、单次响应大小上限 | 平台 |
| 代理 | `NO_PROXY=127.0.0.1,localhost`，`BClient(trust_env=False)` | 云侧代理变量不得污染 loopback/内网调用 | 平台 |

未验证项沿用 `V21_AGENTARTS_MAPPING.md` 的 U-3、U-4、U-5；**不得**把"本机 socket 通"
写成"云侧网络已验证"。

---

## 4. 鉴权与多租户

- 现状：**本地 demo 无鉴权**，仅靠 `session_id` 与 `result_id` 成员校验
  （`session_has_result`，跨会话取结果返回 403）。
- 上线前必须补：Bearer/OAuth 校验、`session_id` 与租户绑定、`turn_id`/`result_id`
  不可跨租户枚举、写操作幂等键 `client_turn_id` 的租户作用域。
- **不能只靠 prompt 做权限**：`contract.py` 的拒绝是业务声明保护，不是认证替代品。
- 负责人：平台（网关）+ C（会话归属）+ D（前端不缓存他人 `result_id`）。

---

## 5. 超时与轮询

| 参数 | 当前值 | 说明 |
|---|---|---|
| `job_timeout_s` | 180 s | B 作业最大等待；`SHELTERPLAN_JOB_TIMEOUT_S` 可配 |
| `submit_timeout_s` / `read_timeout_s` | 20 s / 30 s | 提交与读取 |
| `max_transport_retries` / `max_job_retries` | 2 / 1 | 有限重试，不无限轮询 |
| 轮询建议 | 500 ms | `GET /v1/turns/{turn_id}`，五状态之一 |
| 本机实测（post-fix regression） | p50 7.6 s / p95 22.6 s；B 成功调用 p50 6.3 s | `HARNESS_V2_2_REPORT.md` §5.2 |

上云待验：平台节点自身超时必须 **> 180 s**（否则 C 还在等 B，节点先超时）；
网关空闲超时不得短于轮询窗口；超时后不得重复提交同一 `client_turn_id` 之外的请求。

---

## 6. 状态恢复

| 场景 | 当前行为 | 上云待验 / 限制 |
|---|---|---|
| C 进程重启 | `SessionStore.mark_interrupted()` 把 `RUNNING` 改为 `FAILED` + `INTERRUPTED_BY_RESTART`；会话参数、结果 id、终态轮次可恢复 | 平台重启后这条标记是否还能被前端读到 |
| B 进程重启 | job 表是内存态，**不可续跑**；C 明确说明"请重新发起" | 不假装恢复；若平台提供持久 job，需要新契约 |
| 多副本 | 单进程内存 + 每会话文件，**无跨进程锁** | 多副本会各存一份 → 必须粘性会话或外部状态存储 |
| 澄清挂起 | `DialogueState` 落盘，TTL 1800 s，连续 3 次无效回复关闭 | 副本切换后 pending 是否一致 |
| 取消 | 排队中可取消；已开始的 B 求解不可中断（`cancel_accepted=false`）；已结束 409 | 平台"取消"按钮必须区分这三种 |

负责人：C（状态语义）+ 平台（副本与调度）+ D（前端按 `error.code` 显示）。

---

## 7. Trace 关联

C 侧每轮已有：`session_id`、`turn_id`、`result_id`、`scenario_id`、`job_id`、
`task_id`、`execution_trace_id`（来自 B）、逐事件 `trace.jsonl`。
上云必须补一条显式映射表：

```
agentarts_trace_id ↔ session_id ↔ turn_id ↔ job_id ↔ result_id ↔ scenario_id
```

验收方式：随机取 3 个成功轮次，能在平台 Trace 里按 `agentarts_trace_id` 找回
C 的 `result_id`，且在 B 的 `execution_trace_id` 里能找回同一 `scenario_id`；
断链即不通过（U-9，当前**未联调**）。

---

## 8. 数据声明（不可协商）

- A 数据为 `zhongshan_pilot_v0.1` / `DEMO_ONLY`，**未经过现场核验**。
- 声明出现在三处：`/v1/capabilities.disclosure`、每轮 `warnings`、最终 `message` 的
  `【数据声明】DEMO_ONLY …`。
- `analysis_mode` / `data_status` / `data_version` **不在** `SCENARIO_CHANGE_KEYS` 中，
  自然语言不能开启 `verified`；越权请求返回 `CONTRACT_VIOLATION` 且不改变会话状态。
- 云侧任何节点都不得"优化"掉这段声明，也不得把 `FEASIBLE` 写成"已证明最优"。

---

## 9. C 分支合并依赖清单（**合并前逐项确认**）

图例：**必须先修** = 不修就不能合并/上线；**C 已兼容** = 可由 C 侧规避，但仍是 B/A 的待办。

### 9.1 B 必须先修（阻塞上线，不阻塞 C 分支合并）

| # | 问题 | 证据 | 为什么必须先修 | 负责人 |
|---|---|---|---|---|
| B-1 | `backend/engine.py:368-372` 第二阶段把浮点求和 `best_served` 直接作下界，求和略大于可行上界时判 infeasible，整体降级 `FEASIBLE` / `optimality_proven=false` | `MODULE_B_TO_C_ANALYSIS.md` §3.3：`test_budget_mip_accepts_more_than_the_old_enumeration_bound` 真实失败 | 数值/最优性声明不可信；C 只能"不谎称最优"，不能替 B 证明最优 | B 求解器 |
| B-2 | 结果文档 5–11 MB，且 `parent_scenario_id` 不继承参数（父 0.35 → 子重置 0.2） | 同上 §8.2 实测 | 多轮参数静默重置会直接给出错误规划 | B 求解器 |
| B-3 | 同一 `scenario_id` 对应多个 `scenario_hash`，`find_scenario_version()` 线性扫描返回首个 | 同上 §8.2 | 反查不保证是用户看到的那版 | B 求解器 |
| B-4 | 解释一致性校验辅助函数存在但 API 从未调用 | 同上 §3.2 | 线上没有原语接线，C 只能自己审计 | B 求解器 |
| B-5 | 无 CORS / 鉴权 / 限流 / HTTPS；job 表进程内 | 同上 §3.2、§5.5 | 公网暴露即越权与重启丢任务 | B + 平台 |
| B-6 | 错误体两套形状（扁平 `{error_code}` vs `{"detail":{...}}`），`verified` 报错文案误写 "synthetic fixture data" | 同上 §4.3 D1/D2/D3、§5.5 | 网关/前端无法稳定判错 | B 接口 |

### 9.2 C 已兼容（不阻塞 C 合并，但需登记为 B/A 待办）

| # | 问题 | C 的兼容方式 | 证据 |
|---|---|---|---|
| C-1 | 多轮参数静默重置 | `Session` 保存完整快照，每轮重发全部参数 | `agent/harness/session.py`；`test_dialogue.py` |
| C-2 | `OPTIMAL` 不保证 | `_solver_sentence` 按 `solver_status`/`optimality_proven` 分支，禁止"已证明最优" | `answer.py` |
| C-3 | 结果过大 | 只投影 ≤20 KB summary；`get_result_evidence` 分页 | `harness/results.py`、`b_client.py` |
| C-4 | 异步错误 / 错误体不统一 | `B_ERROR_MAP` + `classify_error` 归一化 | `errors.py`、`controller.py` |
| C-5 | job 重启不可续跑 | `INTERRUPTED_BY_RESTART`，不假装恢复 | `state_store.py`、`test_api.py` |
| C-6 | B 无 geometry | 图层以 `shelter_id` 关联，`geometry=null` 明确标注 | `api.py::result_layers` |
| C-7 | `DEMO_ONLY` 越权请求 | `contract.py` 拒绝 + 不改状态；答案声明来自 B summary | `HARNESS_V2_2_REPORT.md` §3 |
| C-8 | pyarrow 26 需 numpy 2（本机 `DATA_INVALID`） | 固定 `pyarrow 18.1.0` + numpy 1.26，写入启动配方 | `requirements-b.txt`、本机实测 |

### 9.3 A / D / 平台依赖

| # | 阻塞项 | 负责人 | 通过条件 |
|---|---|---|---|
| A-1 | A 数据仍 `DEMO_ONLY`，原始栅格为 LFS 指针、未验收 | A 数据 | 出具验收 manifest 并把 `data_status` 升为 `VERIFIED`；否则全线保持演示声明 |
| D-1 | 前端按五状态渲染、澄清补参、取消三态、`CONTRACT_VIOLATION` 显示"已拒绝" | D 前端 | `C_TO_D_API_HANDOFF.md` 的字段契约逐项对齐 |
| D-2 | 底图与 `shelter_id` 关联（B 不给几何） | D 前端 | 24 个场所都能关联到自备底图，缺失显式标注 |
| P-1 | AgentArts 资源/节点/网络/鉴权/Trace 均**未取得** | 平台 | 本文 §2–§7 全部勾选后才可声称云侧联调完成 |

### 9.4 合并约束（本轮遵守）

- 只在 `feature/module-c` 提交；**不 push、不 merge**；
- 不修改 A 数据与 B 求解器（`backend/`、`data/` 零改动）；
- 合并前必须由 B 侧确认 B-1…B-6 的修复计划，由 A 侧确认 A-1 的验收时间；
- 未取得 P-1 之前，所有报告中的云侧能力一律写"未验证"。

---

## 10. 本轮明确未做

1. 没有部署 AgentArts 节点，没有创建云端资源，没有使用收费云 API；
2. 没有修改 `backend/` 的数学模型、约束、目标函数顺序或 Verifier；
3. 没有把作者自建回归用例标成非作者独立测试（见 `HARNESS_V2_2_REPORT.md` §6）；
4. 没有把"本机两个 uvicorn 进程互通"说成"平台网络上云验证"。

---

## 11. 平台开放后的执行顺序

**本机基线（本轮实测，只代表本机，不代表云侧）**：全量自动化测试
`260 passed / 0 failed`；两种模型模式的四步业务演示各 4 步 `SUCCEEDED` +
3 步澄清收敛；旧 47 条 `post-fix regression (V2.3 final)` 为 `46/47`
（唯一失败是冻结判分器的文本启发式，见 `C_TO_D_API_HANDOFF.md` §12.2）。
平台侧的每一项都必须与这条基线做对照，不能拿本机结果替代云侧验收。

```bash
# 0. 先在本机复跑基线（真实 B + 真实 HTTP）
python examples/c_to_d_business_flow.py --real-llm

# 1. 节点逐个打通（N1 → N9），每步记录 trace 四元组
# 2. 网络/HTTPS/鉴权/超时/状态恢复按 §3–§6 勾表
# 3. 用同一批自然语言输入对比 MCP 与 HTTP 两个入口的数字一致性
# 4. 由 B 侧出具 B-1…B-6 的修复证据，A 侧出具 A-1 manifest
# 5. 全部通过后再讨论合并；在此之前保持 DEMO_ONLY 与"未验证"标注
```
