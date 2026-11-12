# Module C V2.1｜AgentArts 迁移映射

本文只做一件事：把**已经在本仓库跑通**的 Python 模块，逐一映射到 AgentArts 的节点类型，
并明确标出**哪些能力尚未实测**。

- 依据的代码是 `feature/module-c` 分支上的实现（提交见 `HARNESS_V2_1_REPORT.md` 第 9 节）。
- 本文**没有**访问过 AgentArts 控制台，**没有**部署过任何云资源。
  凡标 `待实测` 的条目，都是"在本机成立、在云上未知"。
- 两条不可让步的约束，先写在最前面：
  1. **不重写 B 的求解器**。所有规划数字只能来自 Module B 的
     `result_detail.assignment` / `summary.metrics`，C 侧不复制任何目标函数。
  2. **不另起一套状态机**。会话、槽位回填、取消、超时、错误分类只有
     `agent/workflow/` 与 `agent/harness/session.py` 这一套。AgentArts 里若再写一遍，
     两套状态会漂移，评测结论立即失效。

---

## 1. 总览：三类东西，三种归属

| 归属 | 内容 | 迁移动作 |
|---|---|---|
| **必须留在 C 进程** | 会话状态、参数回填、路由、错误分类、B 客户端、结果投影、Trace、数值审计 | 原样保留，通过 MCP 工具或 HTTP 暴露 |
| **可以交给 AgentArts** | 意图理解、槽位抽取（自然语言 → 结构化）、追问话术、基于结构化结果的解释生成 | 做成 LLM 节点 |
| **禁止进入 AgentArts** | 规划求解、指标计算、可行性判定、`DEMO_ONLY` 判定、数值断言放行 | 一律回到 C / B |

一句话：**AgentArts 负责"听懂"和"说清"，不负责"算对"。**

---

## 2. 逐模块映射表

下表左边是仓库里的真实模块，右边是 AgentArts 的节点形态。
"节点类型"用 AgentArts 的四类通用概念：`LLM`、`Code`、`Condition`、`Tool`（MCP/HTTP）。

### 2.1 意图与槽位（可迁移）

| Python 模块 / 函数 | 职责 | AgentArts 节点 | 迁移说明 |
|---|---|---|---|
| `agent/intent/extractor.py::extract_intent`（LLM 路径） | 自然语言 → `ParsedIntent`（intent + ops + task_type） | `LLM` 节点（结构化输出） | 直接迁。prompt 与 JSON schema 照搬 `agent/intent/schema.py` |
| `agent/intent/extractor.py::rule_extract` | 无模型时的确定性兜底 | `Code` 节点 | 作为 LLM 失败/超时的降级分支保留 |
| `agent/intent/extractor.py::ACTIONABLE_RE` + `clarify` 降级 | 识别"你看着办吧"这类**无动作**表述 | `Condition` 节点 | 迁成条件：抽取结果里没有任何 op → 走追问而不是执行 |
| `agent/workflow/router.py::route` | 意图 + 当前参数 → `RoutePlan` | `Code` 节点 | **不要**迁。这是纯函数但依赖 C 的参数合并语义，包成 MCP 工具 `prepare_turn` 更稳 |
| `agent/workflow/router.py::_clarify` | 记住"用户想做什么任务"，供下一轮续跑 | `Code` 节点调 C | 必须由 C 记账，AgentArts 侧不需要知道 |
| `agent/intent/schema.py::ParamOp` | `set / delta / scale / restore` 四种算子 | 数据结构 | 契约照搬；`scale` 与 `restore` 不允许在云侧展开成具体数值 |

**关于 `scale` 的坑（已在 V2.1 用例集中覆盖）**：
"把比例提高 10%"必须表达为 `{"op":"scale","value":0.1}`，
由 C 用**当前**参数计算，不能在 LLM 节点里先算成 `0.55` 再传 `set`。
否则多轮或并发时会用过期基数，测出来的是幻觉而不是能力。

### 2.2 追问与会话（可迁移，但状态必须留在 C）

| Python 模块 / 函数 | 职责 | AgentArts 节点 | 迁移说明 |
|---|---|---|---|
| `agent/workflow/dialogue.py::DialogueState` | 跨轮 `PendingTask`：缺什么、什么时候过期 | `Tool` 调 C，或 `Code` 直连 C HTTP | 不迁状态本体，只迁"调用" |
| `dialogue.py::fill_pending` + `_FILLERS` | 用"只补那一个值"的方式填槽 | `Code` 调 C | 每个字段一个填充器；`BARE_NUMBER_RE` 支持用户只回 `0.3` |
| `dialogue.py::is_cancel` / `detect_task_switch` | 取消、切任务 | `Condition` → `Code` 调 C | 取消词表刻意收窄：`CANCEL_RE` 只匹配整句，避免"不要扩容"被误判为取消 |
| `dialogue.py::build_resume_ops` / `resume_intent` | 补齐后**恢复原任务**而不是重新猜意图 | `Code` 调 C | 这是"续跑而非重解析"的唯一实现 |
| `agent/structured_runner.py::_dialogue_turn` | 过期 → 取消 → 续跑 → 重问 → 抽取 的固定顺序 | `Code`（调 C） | 顺序即语义，云侧不得调换 |
| `agent/state_store.py::SessionStore` | 会话落盘、重启恢复、`INTERRUPTED_BY_RESTART` | 云侧**不要**重写 | 见 §5 待实测 |

追问话术（`CLARIFY_QUESTIONS`）可以由 LLM 重写得更自然，但**必须**保留"只问缺的那几个字段"的约束，
否则用户会被要求重复已经给过的信息——V2.1 的 `vague` 组就是在测这个。

### 2.3 准入与执行（半迁移）

| Python 模块 / 函数 | 职责 | AgentArts 节点 | 迁移说明 |
|---|---|---|---|
| `agent/harness/guards.py::preflight` | 参数合法性、数据准入、`DEMO_ONLY` 判定 | `Condition` 调 C | 判定权在 C。云侧只根据返回码分支 |
| `agent/harness/guards.py::validate_changes` / `validate_improvement_options` | 单位、范围、候选合法性 | `Code` 调 C | 纯函数，可迁，但**不要在云里复制一份** |
| `agent/workflow/controller.py::TaskController.run` | 提交 B → 轮询 Job → 取 Result → 绑定 | `Tool`（MCP 或 HTTP） | 见 §3 的轮询设计 |
| `agent/workflow/controller.py::bind_outcome` | B 结果 → 结构化 binding（含 `result_id`） | `Code` 调 C | 唯一真值入口 |
| `agent/workflow/controller.py::classify_error` | 错误码 → `upstream_rejected` / `upstream_transient` / `user_error` 等 | `Condition` | 已扩展覆盖 `SCENARIO_INVALID` / `CANDIDATE_INVALID` / `DATA_INVALID` 等 |
| `agent/b_client.py` | B 的 `POST /planning/tasks` → `GET /planning/jobs/{id}` → `GET /planning/results/{id}` | `Tool`（HTTP） | 若 AgentArts 直连 B，须保留同样的三段式与轮询上限 |

### 2.4 出话与审计（最后一道闸，不建议迁）

| Python 模块 / 函数 | 职责 | AgentArts 节点 | 迁移说明 |
|---|---|---|---|
| `agent/workflow/answer.py::compose_answer` | 确定性模板回答（含"已取消"分支） | `Code` | 无 LLM 时的答案来源，也是降级路径 |
| `agent/structured_runner.py::_llm_answer` | LLM 生成解释 | `LLM` | 可迁。输入只给 `summary` 的结构化字段 |
| `agent/structured_runner.py::_audit` | 回答里的数字必须能在 `summary` 中找到 | `Code` 调 C | **迁成 MCP 工具 `check_claims`，不要在 Code 节点里重写正则** |
| `agent/harness/guards.py::check_claims` / `assert_claims_consistent` | 数值断言 | `Code` 调 C | 同上 |
| `agent/harness/guards.py::assert_demo_disclosure` | 输出必须带 `DEMO_ONLY` | `Condition` | 命中即重写一次；仍失败则降级为"只返回结构化结果" |
| `agent/numeric_audit.py` | 数字引用审计 | `Code` 调 C | 与 `_audit` 同源 |

**为什么审计不能放云侧**：一旦云侧也实现一遍数字核对，两份规则必然不同步；
实测中出现的"模型编造 6456 人服务量"这类问题，正是靠 C 侧的单一闸门拦下的。
V2.1 的 `fabricate` 组（5 条）就是专门打这个闸门。

---

## 3. 工作流节点图（推荐形态）

```
[Start]
  │
  ├─ LLM#1  意图识别 + 槽位抽取（结构化输出，schema 同 ParamOp）
  │           └─ 失败/超时 → Code#1 rule_extract（确定性兜底）
  │
  ├─ Condition#1  是否有任何可执行 op？
  │      否 → LLM#2 追问（只问缺的字段）→ 回 LLM#1（带 PendingTask 上下文）
  │      是 → Tool#1  prepare_turn（调 C，回填未修改参数、判定准入）
  │
  ├─ Condition#2  prepare_turn 结论
  │      NEEDS_CLARIFICATION → LLM#2 追问
  │      FAILED(upstream_rejected) → Code#2 按 error_code 组织说明（不编方案）
  │      OK → Tool#2  submit_task
  │
  ├─ Tool#3  get_job（轮询；上限见 §4）→ Tool#4 get_result（≤20KB 投影）
  │
  ├─ Condition#3  data_status == DEMO_ONLY ?
  │      是 → 打标，后续解释强制附带声明
  │
  ├─ LLM#3  基于 summary 生成解释（不得引入 summary 之外的新数字）
  │
  ├─ Tool#5  check_claims（调 C）── 不一致 → LLM#3 重写一次
  │                               └ 仍不一致 → Code#3 降级为纯结构化输出
  │
  └─ [End]  输出 envelope + 写 Trace（join result_id / execution_trace_id）
```

对应到 V2.1 已经落地的 HTTP 形态（`agent/api.py`），节点图是同一张：

| 图中节点 | 现有 HTTP 端点 |
|---|---|
| `prepare_turn` + `submit_task` | `POST /v1/sessions/{sid}/turns` → 202 |
| `get_job` 轮询 | `GET /v1/turns/{turn_id}` |
| `get_result` | envelope 内 `metrics` / `map`；明细走 `GET /v1/results/{rid}/layers`、`/evidence/{section}` |
| 取消 | `POST /v1/turns/{turn_id}/cancel` |
| 能力与数据状态 | `GET /v1/capabilities` |

也就是说：**AgentArts 接的是同一套语义，只是把本地 202+轮询换成了它的 Tool 节点。**

---

## 4. 超时与轮询（本机已实测，云上待实测）

本机实测数据（47 条 V2.1 用例、arm D，见报告第 6 节）：

| 指标 | 值 | 用途 |
|---|---|---|
| 单轮 p50 | ~11 s | 轮询间隔起点 |
| 单轮 p95 | ~21 s | 需要留 2× 余量 |
| 单轮最大 | 见报告 | 决定硬上限 |

因此建议：**轮询间隔 500 ms → 1000 ms 退避，硬上限 120 s**，
超时后返回 `JOB_TIMEOUT` 并**只重试 1 次**（同 `client_turn_id`，靠 C 的幂等键去重）。

`待实测`：
- AgentArts 单个 Tool 节点的最大执行时间是否 ≥ 120 s？未知。
- 若不足，必须把"提交"与"取结果"拆成两次节点调用（本机 API 已经支持这种拆法，
  因为 `POST` 立即 202 返回 `turn_id`）。

---

## 5. 待实测清单（明确未验证）

以下每一条都是**本机成立、云上未知**，不得在对外材料里写成"已支持"。

| # | 项目 | 现状 | 风险 |
|---|---|---|---|
| U-1 | AgentArts 沙箱能否安装 `scipy` / `numpy` / `pyarrow` | 未测 | B 依赖 SciPy/OR-Tools，若强行上云会失败 → 结论：B 留在独立主机 |
| U-2 | 沙箱内 `pyarrow` 与 `numpy` 版本冲突 | 本机踩过（pyarrow 26 需 numpy 2） | 云端若锁版本，可能复现 `DATA_INVALID` |
| U-3 | 代码节点能否访问内网 HTTP | 未测 | 若只能访问公网 HTTPS，需反向代理（见 `AGENTARTS_INTEGRATION.md` §2） |
| U-4 | MCP Server 托管方式（AgentArts 是否托管长驻进程） | 未测 | C 的 MCP 是 stdio/HTTP 长驻进程，托管模型未知 |
| U-5 | MCP 工具单次响应大小限制 | 本机投影 <20 KB | B 原始结果 5–11 MB，绝不可直传 |
| U-6 | 鉴权（Bearer / OAuth）与多租户隔离 | 未实现（本地绑 127.0.0.1） | 上线前必须补，且不能只靠 prompt |
| U-7 | 会话状态在多副本下的一致性 | 未测 | 现实现是**单进程内存 + 落盘**，多副本会各存一份 |
| U-8 | 重启后 `RUNNING` 的 B 任务能否恢复 | **本机已确认不能** | 已显式标记 `INTERRUPTED_BY_RESTART`，不假装恢复 |
| U-9 | Trace 关联（`agentarts_trace_id` ↔ `result_id`） | C 侧字段齐备，云侧未联调 | 断链会导致无法复盘 |
| U-10 | LLM 节点结构化输出的稳定性 | 本机 `gemma4:e2b` 实测有波动 | 已用 `rule_extract` 兜底；云端模型不同，需重测 |

---

## 6. 迁移时**不要**做的四件事

1. **不要在 AgentArts 里重算指标。** 服务人数、步行时间、缺口只能来自 B。
2. **不要复制一套会话状态。** 云侧状态与 C 侧状态一旦并存，就会出现"两边都以为自己对"。
3. **不要把 `scale` / `restore` 展开成绝对值。**
4. **不要在云侧放行数值断言。** 断言失败必须能阻断输出，而不是"提示一下继续"。

---

## 7. 与 V2.1 评测的对应关系

V2.1 的 47 条用例按 10 组设计，每一组都对应上表里的一个节点或一条闸门：

| 用例组 | 数量 | 打的节点 / 闸门 |
|---|---|---|
| `compound` 复合修改 | 5 | LLM#1 多槽位 + `ParamOp` 合并 |
| `vague` 模糊缺参 | 5 | Condition#1 + LLM#2 追问（只问缺的） |
| `units` 单位换算/冲突 | 5 | `validate_changes`（比例 30% → 0.3、分钟/小时） |
| `undo` 否定/撤销/恢复 | 5 | `restore` 算子 + `is_cancel` 的收窄词表 |
| `mixed` 扩容+关停并存 | 4 | `submit_task` 的多任务编排 |
| `compare` 多轮对比 | 4 | 结果绑定 + 跨轮引用 `result_id` |
| `unknown` 未知区域/避难所 | 5 | `preflight` 准入 + `upstream_rejected` 分支 |
| `illegal` 非法/矛盾条件 | 5 | `validate_changes` 拒绝 + 不编方案 |
| `fabricate` 编造覆盖/去掉 DEMO_ONLY | 5 | `_audit` + `assert_demo_disclosure` |
| `bypass` 绕过确认/覆盖结果 | 4 | 确认闸门 + 结果不可被用户话术覆盖 |

这张表也是"哪些能力可以上云、哪些必须留在 C"的直接证据：
凡是打到 `preflight` / `_audit` / `assert_demo_disclosure` 的组，**都不能**把判定权交给 LLM 节点。
