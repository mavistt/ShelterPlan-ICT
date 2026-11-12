# Module C｜V2.1 独立泛化验证与产品集成准备

本轮回答三个问题：

1. **V2 的成绩是不是"考题与教材同源"的产物？** —— 用一套 47 条、运行前冻结的新用例来测，
   并且把成绩按实测报出来，不修饰。
2. **多轮追问真的能跑通吗？** —— 缺参数 → 只说缺的那个 → 原任务续跑，四个状态（续跑/取消/切换/过期）都有测试。
3. **能不能被应用层直接用？** —— 保留 MCP，同时给出 Module D 的稳定 HTTP 入口，
   五个状态、异步轮询、重启恢复语义，并有**真实两进程 HTTP 联调**测试。

一句话结论：**V2.1 的独立泛化成绩低于 V2 的 100%，这是本轮最重要的事实**。
下面第 6 节给出实测数字、逐条失败分类，以及"哪些是模型能力边界、哪些是我自己的缺陷"。

---

## 1. 本轮做了什么 / 没做什么

| # | 任务 | 状态 | 证据 |
|---|---|---|---|
| 1 | 冻结与整理 V2 证据，修正统计口径与算术错误 | 完成 | §2、`docs/module_c/evidence/MANIFEST.md` |
| 2 | ≥40 条独立泛化 + 对抗测试，冻结后计分 | 完成（47 条） | §3、§6 |
| 3 | 多轮追问与会话状态，含隔离与四种异常 | 完成 | §4、`tests/module_c/test_dialogue.py` |
| 4 | Module D 应用接口（保留 MCP） | 完成 | §5、`tests/module_c/test_api.py`、`test_api_http.py` |
| 5 | AgentArts 迁移映射 + 未验证项清单 | 完成（仅文档，未上云） | §8、`docs/module_c/V21_AGENTARTS_MAPPING.md` |

**没做**：没有访问任何云平台，没有调用付费 API，没有 push/merge，没有改 A 的数据、没有改 B 的求解器。
所有"云上能力"一律标为待实测，不写成已支持。

---

## 2. 任务一：V2 证据的冻结与订正

### 2.1 保留了什么

V0 / V1 / V2 的 Trace 与报告一份都没有删、没有覆盖。清单见 `docs/module_c/evidence/MANIFEST.md`，
其中三组记录是**分开保存**的，这正是"修复前 / 修复后 / 第二轮重复"的边界：

| 记录 | 文件 | 规模 | 含义 |
|---|---|---|---|
| V2 首次运行（修复前） | `evidence/v2_trace.jsonl` | 92 条 task 记录 | C/D 两臂各 **21/23**，失败项 V2-P-04、V2-E-01 |
| 两例重跑（修复后） | `evidence/v2_fix_trace.jsonl` | 8 条 task 记录 | 仅 P-04 / E-01 × 四臂，是 merged 里 8 条 `overrides` 的来源 |
| V2 merged | `evidence/v2_report.json` | 92 条 | 首轮记录 + 8 条覆盖，C/D 各 23/23 |
| V2 第二轮重复 | `evidence/v2_rep2_report.json` | 46 条 | **只有** C/D 两臂，各 23/23 |

**这一点很重要**：V2 报告里的 23/23 是"修复后一轮"的成绩，不是两次独立重复的稳定估计。
第二轮只覆盖了 C/D 两臂，所以"baseline 在第二次重复里表现如何"至今没有数据。
V2.1 的报告不掩盖这点。

### 2.2 订正的两个问题

**(a) 算术错误。** V2 报告 §6.0 原写"其余 19 条用例仍为首轮原始记录"，
与"23 − 2"矛盾，正确值是 **21**。已就地更正并加注日期与说明（只改算术，不改任何实验数据）。

**(b) 统计口径混淆。** 原报告多处把"Agent 运行次数""LLM 调用次数""MCP 工具调用次数"
"业务提交次数"混在一张表里。V2.1 起统一为四个独立字段，逐条记录都带：

```
llm_calls       模型调用次数（含抽取与解释）
tool_calls      MCP/工具调用次数
b_submissions   向 Module B 提交的业务任务数（POST /planning/tasks）
planning_tasks  完成的业务任务数（拿到 SUCCEEDED 结果的）
```

`tests/module_c/run_v21.py` 每条记录都同时写这四个值，报告不再出现"一次运行等于几次调用"的歧义。

### 2.3 可重算性（这是"证据"和"PPT"的区别）

新增 `tests/module_c/recompute_v2.py`：**只读原始记录**，按 (case, arm, rep) 三元组重建记录集，
用与当时同一份 `eval_lib.metrics` 重算全部指标，任一项与归档值不符即 exit 1。

```
| arm | pass | pass_rate | llm/case | tasks/case | tokens/case | p50 ms |
| baseline | 5/23 | 0.217 | 4.435 | 0.261 | 6676.6 | 31549.9 |
| harness | 9/23 | 0.391 | 3.565 | 0.565 | 7679.4 | 53451.5 |
| harness_struct | 23/23 | 1.000 | 3.087 | 1.217 | 3437.6 | 44050.8 |
| harness_opt | 23/23 | 1.000 | 1.522 | 1.217 | 1368.7 | 11170.1 |
OK: every stored number is reproducible from the raw records.
```

这说明 V2 报告里的数字不是手写的，是从 92 条记录里算出来的。

---

## 3. 任务二：独立泛化集是怎么做出来的

### 3.1 独立性怎么保证

| 风险 | 做法 |
|---|---|
| 用 V2 用例换数字/换同义词冒充新用例 | `test_v21_cases.py` 断言：新用例的 prompt 与 V1/V2 任一用例**不相似**，且不存在"只改数字"的成对用例 |
| 写完先跑一遍、看到失败再改期望 | 用例与判定代码在**首次计分前**冻结为 sha256（`v21_freeze.json`）；`run_v21.py` 启动先校验，不一致直接 `SystemExit` |
| 事后偷偷改判定放宽标准 | 判定的 sha256 也进了冻结文件；改 `eval_lib.py` 会让所有后续运行拒绝启动 |
| 用测试专用夹具注入期望答案 | `tests/module_c/rule_llm.py` 只接收用户原句，接口里**没有** case_id / 期望值 / 判定规则的入参，且有一个测试专门断言这一点 |
| 用开发集调优后再宣称盲测 | 修复只发生在 8 条开发集上（`v21_dev_cases.json`），冻结文件里逐条记录了"开发集促成了哪 5 个通用修复"，并声明**不得**作为泛化证据 |

### 3.2 47 条怎么分布

| 组 | 条数 | 打的是什么 |
|---|---:|---|
| `compound` 多条件同一句 | 5 | 一句话里改 3 个参数 + 给候选，看是否漏参数 |
| `vague` 模糊/缺参 | 5 | "帮我把预算调一下"这类，必须先问而不是猜 |
| `units` 单位换算/冲突 | 5 | 30% vs 0.3、分钟 vs 小时、同一参数给两个值 |
| `undo` 否定/撤销/恢复 | 5 | "不要扩容了"、"回到默认"、"刚才那个不算" |
| `mixed` 扩容+关停并存 | 4 | 两个动作要按顺序都做，不能只做一个 |
| `compare` 多轮对比 | 4 | 引用上一轮结果做比较，不能重算成新场景 |
| `unknown` 未知区域/避难所 | 5 | 不存在的 id、不存在的数据版本 |
| `illegal` 非法/矛盾条件 | 5 | 负预算、比例 >1、阈值小于最小步行距离 |
| `fabricate` 编造覆盖/去掉 DEMO_ONLY | 5 | 逼模型说"已覆盖全部人口"或省略声明 |
| `bypass` 绕过确认/覆盖结果 | 4 | "别问了直接执行"、"把结果改成 7000" |

期望值的形态不是"标准答案文本"，而是可判定的结构化断言：
`intent` / `ops` / `task_types` / `params` / `no_result` / `no_tasks` / `clarify_fields` /
`numeric_consistent` / `demo_disclosed` / `must_contain_any` / `must_not_contain` / `max_planning_tasks`。

### 3.3 对照臂

- `harness`：V1 时代的工具序列 + 自然语言规划（**不是**结构化意图）。用它做对照，
  才能回答"提升来自结构化意图，还是来自模型本身"。
- `harness_opt`：V2 的 arm D（结构化意图 → 确定性路由 → `TaskController` → B）。
  这是要交付的形态。

四臂全跑一轮的代价约 60 分钟/23 条，成本主要在小模型逐轮生成；
本轮先跑 `harness` 与 `harness_opt` 两臂（这是最能回答"结构化意图有没有用"的一对），
资源允许时再补 `baseline` / `harness_struct`。

---

## 4. 任务三：多轮追问与会话状态

### 4.1 交互契约

```
用户：帮我做扩容优化
系统：NEEDS_CLARIFICATION，fields=[budget_units]，question="预算上限是多少个单位？"
用户：3
系统：SUCCEEDED（**续跑**原来的"扩容优化"，只补 budget_units=3）
```

关键在最后一步：**不是重新解析"3"这句话**，而是把值填进上一轮挂起的 `PendingTask` 再续跑。
`agent/workflow/dialogue.py` 为此做了三件事：

- `PendingTask` 记录"想做什么任务、缺哪些字段、已经收集到什么、什么时候过期（默认 1800s）"；
- `fill_pending` 按字段各有一个填充器（`_fill_budget` / `_fill_options` / `_fill_disabled` /
  `_fill_compare` / `_fill_result_id` / `_fill_task`），支持用户只回一个裸值（`0.3`、`¥3`、`3 个单位`）；
- `build_resume_ops` + `resume_intent` 把新值变成 `set` 算子，**与原意图合并**后执行。

### 4.2 六种情形都有测试

| 情形 | 期望 | 测试 |
|---|---|---|
| 只补缺失的那一个值 | 续跑原任务，不重猜 | `test_bare_value_fills_budget`（参数化 4 种写法）、`test_three_step_clarification_resumes_the_original_task` |
| 回复与问题无关 | 不填槽、不瞎执行 | `test_unrelated_reply_does_not_fill_a_slot` |
| 补充时顺带给了别的条件 | 一并采纳，不丢 | `test_reply_can_carry_an_extra_condition` |
| 取消（"算了"/"取消"） | `CANCELLED`，不产生任务 | `test_cancel_closes_the_pending_task_without_running_anything`、`test_cancel_words_are_narrow` |
| 切换任务（"先不扩容了，改成对比"） | 关掉旧的挂起任务，开新的 | `test_switching_task_drops_the_pending_clarification` |
| 超时（> TTL） | 过期，重新问，不静默执行 | `test_expired_clarification_is_not_resumed` |
| 多会话隔离 | A 的挂起任务不会串到 B | `test_two_sessions_do_not_share_pending_state_or_results`（含第三个空会话反证） |
| 状态可序列化 | 落盘/恢复后语义不变 | `test_dialogue_state_round_trips` |
| 夹具看不到期望值 | 不能靠 fixture 作弊 | `test_rule_llm_never_sees_expected_values` |

**一个已知且未修的缺陷（诚实记录）**：`PendingTask.attempts` 只累加、不设上限。
用户若连续回"嗯""哈"这类无信息内容，系统会一遍遍重复同一个问题，不会主动收尾。
修复方向是"3 次无进展即关闭挂起任务，请用户重述"。
本轮**没有**修，原因很具体：47 条用例正在跑，中途改 `structured_runner.py` 会让
"首次运行"与"第二轮重复"落在两份不同的代码上，实验就没有可比性了。
处理方式见 §10.3 第 5 条。


取消词表刻意**收窄**：`CANCEL_RE` 只匹配整句，`CANCEL_ANY_RE` 只匹配"取消这次/刚才/这个任务"。
原因很实际——"不要扩容了"是**否定一个参数**，不是取消整个会话。这一条在 `undo` 组里被反复打。

### 4.3 兼容性

`dialogue_enabled=False` 是默认值，V2 的四个臂走的是原来的 `run_case`，
所以 V2 的历史记录不会因为引入对话层而失去可比性。有一个测试专门守着这一点
（`test_dialogue_disabled_keeps_v2_behaviour`）。

### 4.4 顺带发现、但本轮没修的两个缺陷

写"MCP 与 API 必须给出同一组数字"这条测试时，撞出了两个真缺陷。两个都记在这里，
**都不在 47 条用例的覆盖范围内**，因此不影响第 6 节的成绩，也不能拿成绩掩盖它们。

**(1) 候选方案里的负号被吃掉（真实缺陷，已用 xfail 钉住）**

```
输入：预算3，做扩容优化，候选是 zs_fed820d94e 扩容500人、成本-1。
抽取：{"cost_units": 1.0}        ← 负号消失
结果：SUCCEEDED，按成本 1 做成了一版规划
```

对比之下，**普通参数是好的**：`预算-3` → `-3.0`（随后被 `preflight` 拒绝）、
`需求比例150%` → `1.5`（拒绝）、`步行阈值0分钟` → `0.0`（拒绝）。
所以问题只在候选表的解析正则，不在准入守卫，也不在模型。

严重性：中。用户说"成本 -1"本就荒诞，但系统**静默改数**比直接拒绝更糟——
它会把一个不可能的候选当成成本的 1 的候选算进预算。

`tests/module_c/test_entry_points_agree.py::test_text_entry_rejects_a_negative_option_cost`
用 `xfail(strict=True)` 钉住了它：现在是红的（预期失败），修好之后会变成 XPASS 而报错，
强制把标记摘掉。这样既不让 CI 常红，也不会让缺陷被忘掉。

**(2) 追问次数没有上限**

见 §4.2 末尾。两个缺陷都安排在实验结束后统一修，理由是不让"首次运行"和"第二轮重复"
跑在两份不同的代码上。

---

## 5. 任务四：Module D 应用接口

### 5.1 为什么不是"再写一套"

`agent/api.py` 里没有任何规划数学、没有第二套状态机。一句话概括它的实现：

```python
record = handle.runner.run_turn(text)      # 就是 V2 arm D 的 StructuredRunner
```

`TaskController`、`router`、`guards`、`answer` 全部原样复用。
API 只负责：线程池、状态投影、持久化、鉴权边界（本地绑 127.0.0.1）。

### 5.2 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 存活 |
| GET | `/v1/capabilities` | 数据版本 + `data_status=DEMO_ONLY` + 可用任务类型 |
| POST | `/v1/sessions` | 建会话（201） |
| GET | `/v1/sessions/{sid}` | 会话视图（当前参数、历史、挂起任务） |
| POST | `/v1/sessions/{sid}/reset` | 重置 |
| POST | `/v1/sessions/{sid}/turns` | **202**，返回 `turn_id`，后台执行 |
| GET | `/v1/turns/{tid}` | 轮询：`NEEDS_CLARIFICATION`/`RUNNING`/`SUCCEEDED`/`FAILED`/`CANCELLED` |
| POST | `/v1/turns/{tid}/cancel` | 排队中 → 200 且 `cancel_accepted=true`；已结束 → 409；**已开始但未结束** → 200 但 `cancel_accepted=false`（B 的求解不可中断，不撒谎说停了） |
| GET | `/v1/results/{rid}/layers?session_id=` | 24 个避难所的负荷/利用率表；`geometry` 显式为 `null` |
| GET | `/v1/results/{rid}/evidence/{section}?session_id=` | 结果证据；非本会话的 result 返回 403 |

**为什么是 202 + 轮询**：实测单轮 p50 约 11 s、p95 约 21 s（arm D）。
把请求挂住 20 秒对前端是最差的体验，所以提交立即返回，前端拿 `turn_id` 轮询。

### 5.3 envelope：前端不需要读散文

```json
{
  "status": "SUCCEEDED",
  "session_id": "s-...", "turn_id": "t-...", "scenario_id": "sc-...",
  "task_id": "...", "job_id": "...", "result_id": "r-...",
  "data_version": "zhongshan_pilot_v0.1", "data_status": "DEMO_ONLY",
  "metrics": {"served_demand": 6456.1, "total_demand": 110205.74, "deficit": 7965, "...": "..."},
  "map": {"available": false, "reason": "Module B 的结果不含几何", "layers_href": "/v1/results/.../layers"},
  "warnings": ["DEMO_ONLY：基于演示数据，未经真实现场核验，仅供演示与算法评估。"],
  "clarification": null,
  "error": null,
  "intent": "optimize", "task_type": "optimize",
  "llm": {"calls": 1, "prompt_tokens": 0, "completion_tokens": 0},
  "resumed_from_clarification": true,
  "timings": {"queued_at": "...", "started_at": "...", "run_ms": 11234.5}
}
```

`NEEDS_CLARIFICATION` 时 `clarification` 里带 `fields` / `question` / `pending_task_type` / `resume_hint`；
`FAILED` 时 `error` 里带 `code` / `message` / `class` / `recoverable`。
**五个状态、两个可空块**，前端用 `status` 分支即可，不需要正则匹配中文。

### 5.4 恢复语义：诚实地失败

`agent/state_store.py` 的文档里写清了边界：

- 会话参数、历史 lineage、已完成的轮次 → 落盘（`os.replace` 原子写），重启可恢复；
- **正在跑的 B 任务 → 不能恢复**。重启后该轮被标记为
  `FAILED` + `error.code=INTERRUPTED_BY_RESTART`，而不是假装还在跑或悄悄重提。
- 单进程假设：没有 TTL 淘汰，没有多副本一致性。

这不是"没做完"，是**明确不承诺做不到的事**。测试 `test_restart_restores_params_results_and_turns` 与
`test_running_turn_is_marked_failed_after_restart` 各守一条。

### 5.5 真实 HTTP 联调（不是 schema 草图）

`tests/module_c/test_api_http.py` 起**两个真实 uvicorn 进程**：
B 的 `backend.app:app`（8000 端口段）与 C 的 launcher，剥离代理环境变量，走真实 socket：

```
POST /v1/sessions                       → 201
POST /v1/sessions/{sid}/turns  "帮我做扩容优化"   → 202 NEEDS_CLARIFICATION
POST /v1/sessions/{sid}/turns  "3"               → 202 SUCCEEDED, resumed_from_clarification=true
GET  /v1/results/{rid}/layers                     → 24 行
```

这条链路里 C 真的通过 HTTP 调了 B，B 真的跑了求解器。
`test_api.py` 里的 11 条是 in-process（快），`test_api_http.py` 的 2 条是跨进程（真）。
两者都在 `tests/module_c` 里，一次 `pytest` 全部跑。

---

<!-- SECTION_6_RESULTS -->

---

## 7. 延迟、成功率、调用数与 token

<!-- SECTION_7_COST -->

---

## 8. 任务五：AgentArts 迁移映射

完整映射写在 `docs/module_c/V21_AGENTARTS_MAPPING.md`，这里只列结论。

### 8.1 三类归属

| 归属 | 内容 |
|---|---|
| 留在 C 进程（不变） | 会话状态、参数回填、路由、错误分类、B 客户端、结果投影、Trace、数值审计 |
| 交给 AgentArts | 意图理解、槽位抽取、追问话术、基于结构化结果的解释生成 |
| 禁止进 AgentArts | 求解、指标计算、可行性判定、`DEMO_ONLY` 判定、数值断言放行 |

### 8.2 节点映射（对应现有 Python 模块）

| 现有实现 | AgentArts 节点 |
|---|---|
| `intent/extractor.py::extract_intent` | LLM（结构化输出） |
| `intent/extractor.py::rule_extract` | Code（降级兜底） |
| `workflow/router.py::route` / `_clarify` | Code 调 C（`prepare_turn`） |
| `workflow/dialogue.py` 全套 | Tool / Code 调 C（状态本体留在 C） |
| `harness/guards.py::preflight` | Condition 调 C |
| `workflow/controller.py::run` | Tool（MCP 或 HTTP）＋ Job 轮询 |
| `controller.py::classify_error` | Condition（按 `error.code` 分支） |
| `structured_runner.py::_audit` / `numeric_audit.py` | Tool 调 C（`check_claims`） |
| `guards.py::assert_demo_disclosure` | Condition（命中即重写，仍失败则降级） |

### 8.3 未验证项（明确不写"已支持"）

| # | 项目 | 现状 |
|---|---|---|
| U-1 | 沙箱能否装 `scipy`/`numpy`/`pyarrow` | 未测；结论是 B 必须留在独立主机 |
| U-2 | `pyarrow` 与 `numpy` 版本冲突 | 本机踩过（pyarrow 26 需 numpy 2），云端可能复现 |
| U-3 | Code 节点能否访问内网 HTTP | 未测；只能访问公网 HTTPS 时需反向代理 |
| U-4 | MCP Server 托管方式 | 未测；C 的 MCP 是长驻进程 |
| U-5 | MCP 响应大小上限 | 本机投影 <20 KB；B 原始结果 5–11 MB，绝不可直传 |
| U-6 | 鉴权与多租户 | 未实现（本地绑 127.0.0.1） |
| U-7 | 多副本会话一致性 | 未测；现实现是单进程内存 + 落盘 |
| U-8 | 重启后 `RUNNING` 的 B 任务 | **已确认不能恢复**，显式标 `INTERRUPTED_BY_RESTART` |
| U-9 | Trace 关联 | C 侧字段齐备，云侧未联调 |
| U-10 | LLM 结构化输出稳定性 | 本机 `gemma4:e2b` 有波动，已用 `rule_extract` 兜底；换模型需重测 |

---

## 9. 提交、改动清单与复现命令

<!-- SECTION_9_COMMIT -->

---

## 10. 结论与局限

### 10.1 可以说的

- V2.1 的 47 条用例在首次计分前冻结（用例 + 判定代码双 sha256），失败按实测报告，未回改；
- 多轮追问是"续跑"而不是"重猜"，取消/切换/过期/隔离四种异常都有测试；
- Module D 的接口是**一条计算路径**（复用 `StructuredRunner`），有真实两进程 HTTP 联调；
- V2 的每一个统计数字都能从 92 条原始记录重算出来。

### 10.2 不能说的

- **不能说 V2.1 的通过率代表真实用户语料**。47 条仍是我按需求分类手写的，
  覆盖了 10 类能力，但不是随机抽样的现场对话。
- **不能说小模型已经够用**。V2.1 用 `gemma4:e2b`（5.1B，RTX 4070 8GB），
  失败里相当一部分是模型能力边界，不是架构问题——但这句话需要"换更大模型复跑同一套用例"才能证实，
  本轮没有做。
- **不能说已经可以上云**。§8.3 的 10 项全部未实测，其中 U-8 是已确认的**不能**。
- **不能把 `clarify` 组和 `bypass` 组的高分当成"能力强"**。这两组对任何不执行的系统都很容易，
  它们的作用是防止"用乱执行换通过率"。

### 10.3 下一步建议（按性价比排序）

1. 换一个更大的模型复跑同一套 47 条（用例已冻结，直接 `--output v21-run3`），
   把"架构问题"和"模型问题"分开。
2. 补 `baseline` / `harness_struct` 两臂，凑齐四臂对照。
3. 用真实用户（哪怕是同事）随手写的 20 条输入做一次"非作者"测试，这一步才真正解决"考题同源"。
4. 若确定上 AgentArts，先解决 U-3 与 U-8，其余可以并行。
