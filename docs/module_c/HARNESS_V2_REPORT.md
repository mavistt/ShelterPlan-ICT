# Module C｜Harness V2 报告：结构化意图、受控执行与可靠性优化

- 仓库：`mavistt/ShelterPlan-ICT` · 分支 `feature/module-c` · 基于 B 的 `5f8bb512`
- 运行环境：Windows 11 / Python 3.12（Anaconda base）/ 本地 Ollama `gemma4:e2b`（5.1B，RTX 4070 Laptop 8 GB）
- 数据：`zhongshan_pilot_v0.1`，`DEMO_ONLY`
- 本轮新增：`agent/intent/`、`agent/workflow/`、`agent/structured_runner.py`、`tests/module_c/run_v2.py`、`tests/module_c/eval_lib.py`、23 条新用例
- 未改动：`backend/`（B 的数学模型与 Verifier）、`data/`（A 的数据）、`feature/module-b`、`main`

> **证据等级**：本报告里的"真实模型实验"只指第 6 节的四臂运行（真实 Ollama、真实 B API、真实用例）。
> 第 4 节的单元测试是**离线确定性测试**（脚本化 LLM），不代表模型能力。两者不混用。

---

## 1. 本轮实际完成的功能

| # | 功能 | 位置 | 状态 |
|---|---|---|---|
| 1 | 结构化意图信封（9 类意图、5 种参数操作、对比/敏感性/候选槽位） | `agent/intent/schema.py` | 完成 |
| 2 | LLM 抽取器：JSON 模式 → 确定性修复 → 一次重问 → 规则兜底 | `agent/intent/extractor.py` | 完成 |
| 3 | 参数归一化：`set/delta/scale/restore` 作用于完整会话快照 | `agent/intent/normalizer.py` | 完成 |
| 4 | 受控路由：意图→任务、缺参澄清、数据准入、对比/敏感性计划 | `agent/workflow/router.py` | 完成 |
| 5 | 执行控制器：提交、错误分类、有限重试、数值绑定 | `agent/workflow/controller.py` | 完成 |
| 6 | 确定性作答：数字、参数、最优性语义、`DEMO_ONLY` 程序化声明 | `agent/workflow/answer.py` | 完成 |
| 7 | C/D 两臂运行器（同一抽取器，不同作答/错误路径） | `agent/structured_runner.py` | 完成 |
| 8 | 四臂消融运行器（顺序轮换、逐条配对、多次重复） | `tests/module_c/run_v2.py` | 完成 |
| 9 | 公平判分库（修复 V1 的 8 处框架缺陷） | `tests/module_c/eval_lib.py` + `docs/module_c/V1_EVAL_AUDIT.md` | 完成 |
| 10 | 23 条全新测试用例（5 组） | `tests/module_c/cases/v2_cases.json` | 完成 |
| 11 | 离线单元测试 73 条 + MCP 入口 2 条 | `tests/module_c/test_intent.py`、`test_workflow.py`、`test_eval_lib.py` | 完成 |

**没有做**：没有新增 B 的任务类型；没有改 B 的求解器/Verifier；没有把 B 的 12 个原子技能暴露给模型；
没有引入新的付费服务；没有把 V0 的确定性结果混进 LLM 指标。

---

## 2. 架构：模型只做"判断"，其余全部确定性

```
用户话语
   │
   ├─(1) IntentExtractor（1 次 LLM 调用，json_object）
   │      → {intent, ops[], compare, sensitivity, options[], clarify[]}
   │      → 确定性修复（负预算、显式比例、未提及场所、扩容≠关停、无比较词的 compare）
   │      → 解析失败才重问 1 次；仍失败 → 规则兜底
   │
   ├─(2) Normalizer（0 次 LLM）
   │      ops 作用在 Session 的完整快照上 → 只产出"变了哪几个键"
   │      未提及的参数在数学上不可能改变
   │
   ├─(3) Router（0 次 LLM）
   │      意图 → B 真实存在的 task_type；缺预算/候选/停用目标 → 结构化澄清
   │      不重写 B 的技能 DAG，只决定"调哪个业务任务"
   │
   ├─(4) TaskController → HarnessExecutor（B API）
   │      submit → poll → fetch → guards → project（沿用 V0）
   │      错误分类：guard / 上游瞬时 / 上游拒绝 / 结果一致性 / 内部
   │
   └─(5) 作答
          C 臂：1 次 LLM 写 prose + V1 的数值/DEMO 闸门（可重写 1 次）
          D 臂：0 次 LLM，全部字段从 summary 绑定格式化
```

**与 B 的边界**：C 只判断"当前业务任务是什么、参数够不够、能不能跑、结果怎么解释"；
B 仍然负责内部技能 DAG、优化算法、业务 Verifier。C 没有任何一行重新实现预算/容量校验。

### 2.1 意图与参数操作

| intent | 映射的 B task_type | 说明 |
|---|---|---|
| `assess` | `assess` | 现状评估 |
| `optimize` | `optimize` | 预算约束扩容优化 |
| `compare` | `compare` | 2–12 个情景并列 |
| `stress_test` | `stress_test` | 停用场所压力测试 |
| `sensitivity` | `sensitivity` | 单参数多档 |
| `diagnose` | `diagnose_intervene` | 瓶颈诊断 |
| `explain` | `explain_result` | 解释上一条结果 |
| `modify_scenario` | 复用上一轮任务，否则 `assess` | 纯参数修改 |
| `clarify` | — | 意图不明，直接澄清 |

参数操作只有 4 种，且都作用在**会话快照**上：

```json
{"field": "budget_units",         "op": "set",     "value": 2}
{"field": "budget_units",         "op": "delta",   "value": 2}
{"field": "budget_units",         "op": "scale",   "value": 0.5}
{"field": "max_walk_time_min",    "op": "restore", "value": "previous"}
```

`restore` 的目标可以是 `previous` / `default` / 轮次号 / `scenario_id`，由 `Session.history` 解析。
模型**从不输出完整快照**，因此"未提及参数被重置"这类 V1 失败在结构上不可能发生。

### 2.2 确定性的"廉价修复"层

这些修复不需要额外模型调用，且每条都有单元测试：

| 触发 | 修复 | 理由 |
|---|---|---|
| 文本含 `预算-5` | 强制 `set -5` | 负数是字面值，不是"减少 5" |
| 文本含 `比例150%` | 强制 `set 1.5` | 显式比例是目标值，不是乘数；越界也要进 Guard 被拒 |
| `disabled_shelter_ids` 含用户没打过的 ID | 丢弃并澄清 | 绝不让模型编造场所 |
| 文本有"扩容"无"关停" | 丢弃停用操作；缺候选时用规则补 | 扩容 ≠ 关停 |
| 文本有"扩容 N 人"但模型没给候选 | 规则解析补候选 | 候选结构对 5B 模型过重 |
| 意图是 `compare` 但无"比较/对比/差异" | 降级为 `assess`/`clarify` | 防止误触发对比 |
| 文本有"评估"但意图是 `modify_scenario` | 升级为 `assess` | 明确要结果，不只是改参数 |

### 2.3 真实调用与替身的边界（必须披露）

| 对象 | 本轮实现 | 说明 |
|---|---|---|
| Module B 规划计算 | **真实** | 92 次运行全部经 `BClient` 调用 `backend.app:app`（进程内 ASGI，真实求解器与业务 Verifier） |
| Module B 的 HTTP 语义 | **真实**（ASGI 层） | 202/轮询/404/422 都是 B 自己的行为，没有 mock |
| MCP 传输 | **真实**（测试中） | `test_mcp_stdio` 用真实子进程 + 官方 SDK 客户端；`test_mcp_http` 用真实 uvicorn |
| 模型 | **真实**（本地 Ollama） | `gemma4:e2b`，temperature 0，OpenAI-compatible |
| LLM 替身 | 仅离线测试 | `tests/module_c/scripted_llm.py` 只用于单元测试，**不参与** §6 的任何数字 |
| 故障注入 | **本轮无** | V0 的注入故障（超时/作业丢失）未用于 V2；V2 的失败全部是真实失败 |
| 数据 | **真实**（DEMO_ONLY） | `data/processed/*` 为真实文件；LFS 栅格未下载，不影响 B |

没有任何一条 V2 指标来自"预设结果"或"假求解器"。

**MCP 结构化入口已做真实联通验证**：启动 `uvicorn backend.app:app --port 8000` 后，
调用 `handle_request("评估一下东区现状", "mcp-smoke2")` 返回
`intent=assess / task_type=assess / 1 次模型调用 / result_id=2951f5f6-…`，
答案包含 `已服务 6,456.10 人 / 总需求 110,205.74 人` 与 `DEMO_ONLY` 声明。
这不是 mock：B 走的是真实 HTTP 端口。

---

## 3. 低成本异常处理（D 臂相对 C 臂的差异）

| 场景 | C 臂（结构化 + 路由） | D 臂（+ 廉价错误处理） |
|---|---|---|
| JSON 格式错 | 确定性修复，不重调模型 | 同 C |
| 可识别字段错（负预算、越界比例、未提及场所） | 同 C | 同 C |
| 语义无法判断 | 重问 1 次（计入调用） | 同 C |
| 缺预算/候选/停用目标 | 1 次 LLM 生成澄清话术 | **0 次**，模板化澄清 |
| 执行失败 | 1 次 LLM 解释错误 | **0 次**，按错误类模板化 |
| 最终作答 | 1 次 LLM 写 prose + 可能的 1 次重写 | **0 次**，字段绑定格式化 |
| `DEMO_ONLY` 声明 | 依赖模型写 + 闸门重写 | **程序化固定句** |

D 臂的答案由 `bind_outcome()` 绑定：只允许 `summary` 里的数值进入文本，覆盖率由 `served/total` 计算
（同一份证据的派生值，数值审计认可）。`solver_status=FEASIBLE` 时固定输出"未证明最优"。

---

## 4. 离线自动化测试（真实执行结果）

**执行结果：`153 passed, 25 warnings in 139.70s`**（`python -m pytest tests/module_c -q`）

| 文件 | 用例 | 覆盖内容 | 结果 |
|---|---:|---|---|
| `test_intent.py` | 34 | 解析、别名、百分比、JSON 修复、5 类确定性修复、规则兜底、抽取阶梯（1 次/重问/兜底） | 34 passed |
| `test_workflow.py` | 26 | 参数操作、字段级 restore、路由 12 条规则、控制器重试/分类、确定性作答 | 26 passed |
| `test_eval_lib.py` | 13 | 判分、澄清启发式的两个反例、配对胜负、继承率 | 13 passed |
| `test_guards.py` | 25 | 沿用 V0：边界、准入、候选、最优性 | 25 passed |
| `test_executor.py` | 16 | 沿用 V0：7 类任务 + 超时/丢失/幂等 | 16 passed |
| `test_session.py` | 8 | 沿用 V0：快照、历史不可变、血缘 | 8 passed |
| `test_results.py` | 6 | 沿用 V0：投影预算、数值一致、分页 | 6 passed |
| `test_llm_adapter.py` | 6 | 422/500/非 JSON/重试/token 记账 | 6 passed |
| `test_b_client.py` | 6 | 202 语义、404、异步失败、传输重试 | 6 passed |
| `test_agent_loop.py` | 6 | 沿用 V1：工具循环、双闸门、轮次上限 | 6 passed |
| `test_mcp_stdio.py` | 4 | 真实子进程 + 官方 SDK 客户端 | 4 passed |
| `test_mcp_http.py` | 1 | 真实 uvicorn + Streamable HTTP | 1 passed |
| `test_mcp_structured.py` | 2 | `handle_request` 仅按开关注册、per-session 缓存 | 2 passed |

**这些是离线测试，不是模型实验**：脚本化 LLM 只验证"机制在给定输入下的行为"，
不证明模型会给出这些输入。模型行为只在 §6 的四臂运行里测量。

V0 的 25 条确定性用例本轮**重跑通过**：`25/25`，参数继承率 1.0、错误识别率 1.0、摘要一致率 1.0
（`outputs/module_c/v0-recheck2/report.json`）。重跑与模型实验并发，延迟数值（p50 6.46 s）不作为基准。


---

## 5. 四组消融设计

| 臂 | 定义 | 模型调用方式 |
|---|---|---|
| **A `baseline`** | V1 baseline：裸工具、无状态、单次尝试 | 原生 tool calling |
| **B `harness`** | V1 harness：Session + Guard + 投影 + 闸门 + 有限重试 | 原生 tool calling |
| **C `harness_struct`** | B + 结构化意图 + 受控路由；**作答仍由模型写** | 抽取 1 次 + 作答 1 次（可重写 1 次） |
| **D `harness_opt`** | C + 廉价错误处理 + 确定性作答绑定 | 仅抽取 1 次 |

公平性约束（与 V1 的差异见 `docs/module_c/V1_EVAL_AUDIT.md`）：

1. 同一模型、同一温度（0）、同一数据版本、同一 B 进程；
2. A/B 共用 V1 的系统提示与工具 Schema；C/D 共用同一抽取器与提示；
3. 每个 `(用例, 臂, 重复)` 独立 `session_id`，状态不串；
4. 臂顺序按 `(用例序号 + 重复序号) % 4` 轮换；
5. 逐条配对统计，不只报平均；
6. A/B 与 C/D 的工具粒度不同（业务任务工具 vs 协议工具），因此同时报告
   `llm_calls`、`tool_calls`、`planning_tasks` 三个量，不把粒度差异说成"模型更高效"。

### 5.1 新测试集（23 条，与 V1 的 14 条 holdout 完全不重叠）

| 组 | 数量 | 覆盖 |
|---|---:|---|
| `plan` | 6 | assess / optimize / compare / sensitivity / diagnose / stress |
| `multi` | 5 | 比例继承、预算减半（候选来自记忆）、停用后改预算、比例回退、双场所停用 |
| `relative` | 4 | 预算 `delta`、预算 `scale`、比例 `set`、`restore previous` |
| `clarify` | 5 | 缺预算、负预算、150% 比例、缺停用目标、无基线时减半 |
| `recover` | 3 | 负预算后改正、150% 后改正、对比缺轴后补全 |

V1 的 14 条 holdout 已被用于 V1 失败分析，本轮**只作为回归集**，不再称为盲测集。

---

## 6. 四臂实验结果

### 6.0 运行口径

- 23 条用例 × 4 臂 × 1 轮，共 92 次运行；全部为真实模型 + 真实 B 计算（进程内 ASGI）。
- 首轮运行暴露了 C/D 的两个实现缺陷（`restore` 未做字段隔离、敏感性/对比计划未从文本补全）。
  缺陷修复后，仅对受影响的 **V2-P-04 / V2-E-01** 两例、四臂共 8 次运行做了重跑，
  并以重跑结果覆盖首轮记录。报告中的其余 19 条用例仍为首轮原始记录。
  覆盖清单见 `outputs/module_c/v2-final/report.json` 的 `overrides` 字段。
- 因此 C/D 的 23/23 应理解为"修复后一轮运行"的结果，**不是**两次独立重复的稳定估计；
  第二次重复（C/D 两臂）在 §6.7 单独报告。

### 6.1 总体指标

| 指标 | `baseline` | `harness` | `harness_struct` | `harness_opt` |
|---|---:|---:|---:|---:|
| 完整任务通过率 | 21.7% (5/23) | 39.1% (9/23) | **100.0% (23/23)** | **100.0% (23/23)** |
| 任务路由正确率 | 16.7% | 38.9% | 100.0% | 100.0% |
| 参数正确率 | 69.2% | 46.2% | 100.0% | 100.0% |
| 多轮继承正确率 | 62.5% | 50.0% | 100.0% | 100.0% |
| 数值解释一致率 | 100.0%\* | 88.9% | 100.0% | 100.0% |
| DEMO_ONLY 合规率 | 0.0% | 27.8% | 100.0% | 100.0% |
| 澄清/拒绝正确率 | 100.0% | 100.0% | 100.0% | 100.0% |
| 意图识别正确率 | n/a | n/a | 100.0% | 100.0% |
| 槽位提取正确率 | n/a | n/a | 100.0% | 100.0% |
| 错误恢复成功率 | 0.0% | 0.0% | 100.0% | 100.0% |
| LLM 调用/用例 | 4.44 | 3.57 | 3.09 | **1.52** |
| 业务任务/用例 | 0.26 | 0.57 | 1.22 | 1.22 |
| tokens/用例 | 6,677 | 7,679 | 3,438 | **1,369** |
| LLM 延迟 p50 (ms) | 31,534 | 45,686 | 36,234 | **4,357** |
| B API 延迟 p50 (ms，仅成功任务) | n/a\*\* | 6,258 | 7,743 | 7,565 |
| 端到端 p50 (ms) | 31,550 | 53,452 | 44,051 | **11,170** |
| 端到端 p95 (ms) | 71,457 | 81,407 | 84,541 | **20,842** |
| 重试总数 | 0 | 0 | 0 | 0 |

\* baseline 的"数值一致率 100%"是**真空成立**：它在 23 条里只有 6 条真正拿到结果，
其余回答没有引用任何规划数字。这个指标必须和"任务通过率 21.7%"一起读。
\*\* baseline 走的是异步协议（submit → get_job → get_result），B 的计算发生在两次工具调用之间，
无法归因到某一次 HTTP 调用；B/C/D 的 `submit_task` 内部完成"提交→轮询→取结果"，因此可以整段计时。

### 6.2 分组通过率

| 组 | `baseline` | `harness` | `harness_struct` | `harness_opt` |
|---|---:|---:|---:|---:|
| plan（6） | 0% | 50% | 100% | 100% |
| multi（5） | 0% | 0% | 100% | 100% |
| relative（4） | 0% | 25% | 100% | 100% |
| clarify（5） | 100% | 100% | 100% | 100% |
| recover（3） | 0% | 0% | 100% | 100% |

两组 100% 需要解释：`clarify` 组对四个臂都不难（"不做"比"做对"容易），
它存在的意义是**防止机制用"乱执行"换来通过率**——四臂都没有用执行来换分。

### 6.3 逐条结果

| 用例 | 组 | baseline | harness | C | D |
|---|---|---|---|---|---|
| V2-P-01 现状评估 | plan | ❌ | ✅ | ✅ | ✅ |
| V2-P-02 预算优化 | plan | ❌ | ❌ | ✅ | ✅ |
| V2-P-03 情景比较 | plan | ❌ | ❌ | ✅ | ✅ |
| V2-P-04 敏感性 | plan | ❌ | ✅ | ✅ | ✅ |
| V2-P-05 瓶颈诊断 | plan | ❌ | ❌ | ✅ | ✅ |
| V2-P-06 关停压力测试 | plan | ❌ | ✅ | ✅ | ✅ |
| V2-M-01 比例继承 + 阈值修改 | multi | ❌ | ❌ | ✅ | ✅ |
| V2-M-02 预算减半（候选来自记忆） | multi | ❌ | ❌ | ✅ | ✅ |
| V2-M-03 停用后改预算 | multi | ❌ | ❌ | ✅ | ✅ |
| V2-M-04 比例回退 | multi | ❌ | ❌ | ✅ | ✅ |
| V2-M-05 双场所停用 | multi | ❌ | ❌ | ✅ | ✅ |
| V2-R-01 预算 delta | relative | ❌ | ✅ | ✅ | ✅ |
| V2-R-02 预算 scale | relative | ❌ | ❌ | ✅ | ✅ |
| V2-R-03 比例 set | relative | ❌ | ❌ | ✅ | ✅ |
| V2-R-04 restore previous | relative | ❌ | ❌ | ✅ | ✅ |
| V2-C-01 缺预算 | clarify | ✅ | ✅ | ✅ | ✅ |
| V2-C-02 负预算 | clarify | ✅ | ✅ | ✅ | ✅ |
| V2-C-03 150% 比例 | clarify | ✅ | ✅ | ✅ | ✅ |
| V2-C-04 缺停用目标 | clarify | ✅ | ✅ | ✅ | ✅ |
| V2-C-05 无基线减半 | clarify | ✅ | ✅ | ✅ | ✅ |
| V2-E-01 负预算后改正 | recover | ❌ | ❌ | ✅ | ✅ |
| V2-E-02 150% 后改正 | recover | ❌ | ❌ | ✅ | ✅ |
| V2-E-03 对比缺轴后补全 | recover | ❌ | ❌ | ✅ | ✅ |

### 6.4 失败分类（按失败类型计次，一例可多类）

| 失败类型 | baseline | harness | C | D |
|---|---:|---:|---:|---:|
| `wrong_task`（选错任务/没执行） | 15 | 11 | 0 | 0 |
| `param_error`（参数错/丢失） | 4 | 7 | 0 | 0 |
| `demo_violation`（缺 DEMO 声明或越权表述） | 5 | 4 | 0 | 0 |
| `numeric_violation`（引用不存在的数字） | 0 | 2 | 0 | 0 |
| `none`（无失败） | 5 | 9 | 23 | 23 |

### 6.5 配对胜负（同一用例、同一轮，逐条比较）

- `baseline → harness`：**4 胜 / 0 负 / 19 平**
- `harness → harness_struct`：**14 胜 / 0 负 / 9 平**
- `harness_struct → harness_opt`：**0 胜 / 0 负 / 23 平**
- `baseline → harness_opt`：**18 胜 / 0 负 / 5 平**

C 与 D 的 23 平是本轮最重要的结果之一：**在完全去掉作答阶段的模型调用之后，正确率没有下降**，
而成本（调用数、tokens、延迟）显著下降。

### 6.6 四个关键对照案例（原文）

**（1）V2-P-02 预算优化** —— 工具协议对弱模型的门槛

| 臂 | 行为 | 结果 |
|---|---|---|
| baseline | 提交任务后回答"请稍候，我将轮询"然后**停止**（2 次调用） | 没有取回结果 |
| harness | 把 `improvement_options` 塞进了 `changes`，被 B 的 allowlist 正确拒绝；随后反问用户"支持哪些参数" | 没有完成任务 |
| C | 2 次调用，正确报告 `6756.10` 人、`8.809` 分钟，并声明 DEMO_ONLY | 通过 |
| D | 1 次调用，结构化输出"优化前 6,456.10 → 优化后 6,756.10，变化 300.00"，含 result_id | 通过（11.2 s） |

**（2）V2-M-01 多轮继承** —— "只改步行阈值，其它不变"

- baseline：10 次调用后仍在问"您想评估还是优化"，比例与阈值都没落地；
- harness：被 `compare` 带偏，反问"请提供 2–12 个场景"，并且引用了不存在的数字；
- C/D：比例保持 `0.35`、阈值改为 `20`，最终报告 `已服务 11,332 / 总需求 192,860`。
  C 用 4 次调用 / 63 s，D 用 2 次调用 / 17.6 s。

**（3）V2-C-03 非法比例** —— 机制必须在模型之外兜底

"需求比例按150%评估一下现状"：模型把 `150%` 读成了 `scale 1.5`（0.2×1.5=0.3，恰好是合法值）。
仅靠模型，这条会**静默变成 30% 的正常评估**；C/D 的确定性修复把"比例X%"强制还原为 `set 1.5`，
Guard 拒绝，最终以结构化澄清收场（`clarification_fields=["population_demand_ratio"]`）。

**（4）V2-E-01 错误恢复** —— 第一轮被拒，第二轮改正

- baseline：第二轮仍未产出优化结果；
- harness：同样失败（预算未落地）；
- C/D：第二轮 `set 2` + 记忆中的候选 → 成功优化。
  这条用例还暴露了 C 的一个真实缺陷（`restore` 会把整份快照回滚），已修复并加了回归测试。

### 6.7 第二次重复（稳定性检查）

由于 A/B 两臂单次运行已耗时约 60 分钟，第二次重复只覆盖 C/D 两臂（23 条 × 2 臂），
目的是检验"结构化的收益是否稳定"，而不是重复已经明显落后的 A/B。

| 指标 | C 第 1 轮 | C 第 2 轮 | D 第 1 轮 | D 第 2 轮 |
|---|---:|---:|---:|---:|
| 通过率 | 23/23 | **23/23** | 23/23 | **23/23** |
| 意图识别 | 100% | 100% | 100% | 100% |
| 槽位提取 | 100% | 100% | 100% | 100% |
| 数值一致 | 100% | 100% | 100% | 100% |
| DEMO 合规 | 100% | 100% | 100% | 100% |
| LLM 调用/用例 | 3.087 | 3.087 | 1.522 | 1.522 |
| 端到端 p50 | 37.5 s | 37.1 s | 11.2 s | 10.7 s |

两轮之间**没有出现任何一例翻转**（46 次 C/D 运行全部通过），调用次数完全一致，
延迟差异在 ±5% 以内。这说明在本测试集上，C/D 的结果是稳定的；
但它仍然只覆盖 23 条作者设计的用例，不构成对外部语料的泛化证明（见 §7.4）。

A/B 的第二次重复未运行。它们在首轮的失败集中在"没执行/选错任务"，属于结构性失败，
不是采样噪声；报告不对 A/B 的稳定性做任何声明。


### 6.8 成本结构

| 臂 | LLM 调用 | tokens | LLM 延迟 p50 | B 延迟（成功任务） | 端到端 p50 |
|---|---:|---:|---:|---:|---:|
| baseline | 4.44 | 6,677 | 31.5 s | n/a | 31.5 s |
| harness | 3.57 | 7,679 | 45.7 s | 6.3 s | 53.5 s |
| C | 3.09 | 3,438 | 36.2 s | 7.7 s | 44.1 s |
| D | **1.52** | **1,369** | **4.4 s** | 7.6 s | **11.2 s** |

D 的端到端时间已经接近"B 的计算时间 + 一次抽取调用"的下界：B 本身要 6–8 s，
抽取要 3–5 s，剩下的是进程内开销。继续压缩只能从 B 的求解时间或抽取的批处理入手，
不是 Harness 层能解决的。


---

## 7. 失败归因与改善对照

### 7.1 四类失败是怎样被逐层消除的

| 失败类型 | baseline | harness | C | D | 消除机制 |
|---|---:|---:|---:|---:|---|
| 选错任务 / 根本没执行 | 15 | 11 | 0 | 0 | C/D 不再让模型"选工具"，只让它输出意图；意图→任务是一个固定映射表 |
| 参数错 / 丢失 | 4 | 7 | 0 | 0 | 模型只输出 `ops`，作用在会话快照上；未提及字段不进 `changes` |
| 缺 DEMO 声明 / 越权表述 | 5 | 4 | 0 | 0 | D 用固定句；C 用闸门 + 一次重写 |
| 引用不存在的数字 | 0\* | 2 | 0 | 0 | D 的数字全部由 `bind_outcome()` 格式化，模型不接触数字 |

\* baseline 的 0 是真空成立，见 §6.1 脚注。

### 7.2 改善的因果链（不是"提示词变好了"）

1. **A → B（+4 例）**：会话快照 + 结果投影 + 数值/DEMO 闸门。B 仍然要模型自己选工具，
   所以 `wrong_task` 只从 15 降到 11。
2. **B → C（+14 例）**：把"选工具 + 填嵌套 JSON"换成"分类 + 平铺 ops"。
   这一步同时消灭了 `wrong_task`（15→0）和 `param_error`（7→0），
   因为任务选择变成了 `TASK_BY_INTENT[intent]`，参数变成了对快照的确定性运算。
3. **C → D（0 例，但成本大幅下降）**：把"写作答"从模型换成模板 + 绑定。
   正确率不变，因为 C 的闸门已经能纠正 C 的作答；但 D 把调用数从 3.09 降到 1.52，
   端到端 p50 从 44.1 s 降到 11.2 s，p95 从 84.5 s 降到 20.8 s。

**结论**：本轮的收益来自**结构**，不是提示词。A/B/C/D 的提示词差异只有"做什么判断"，
没有任何一臂获得了额外的答案或隐含的正确参数。

### 7.3 实验暴露的两个实现缺陷（已修复，计入 §6.0 的重跑）

| 缺陷 | 现象 | 根因 | 修复 | 回归测试 |
|---|---|---|---|---|
| `restore` 回滚整份快照 | "预算恢复到上一轮"会把同一轮刚设的预算也回滚掉 | `normalizer` 对 restore 写了全部 4 个字段 | 只恢复 `op.field` 指定的字段 | `test_restore_is_field_scoped` |
| 敏感性/对比计划缺失 | 模型给出了数值但没填 `sensitivity`/`compare` 槽位，路由只能澄清 | 只信任模型的槽位 | 缺槽位时用规则解析补全 | `test_rule_intent_mapping`、`test_rule_compare` 等 |

这两个缺陷都不是"提示词问题"，而是 C 自己的状态/计划代码问题；它们是在真实运行里暴露的，
不是在写测试时想到的。这也是本轮坚持"先跑再写报告"的原因。

### 7.4 这次 100% 不能直接外推

必须明确三点：

1. **测试集是作者设计的**，23 条覆盖了 5 类能力，但不是随机抽样的用户语料；
2. **V2 的设计受 V1 失败分析影响**（例如"候选措施要独立承载"直接来自 V1 的 422 观察），
   所以 V2 在这 23 条上表现好，部分是因为"考题与教材同源"；
3. **单轮 23 条不足以宣称统计显著**。C 与 D 的 23 平只能说明"在本次运行中没有观察到差异"，
   不能说明"两者永远等价"。

下一步的独立验证应使用：V1 的 14 条 holdout（仅作回归）+ 一组未参与设计的新语料 +
一个更强的模型（换模型能直接检验"剩余失败是否来自 5B 的能力边界"）。

### 7.5 仍然存在的风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 意图抽取依赖模型 | 换模型/换语言可能改变抽取质量 | 抽取有规则兜底；关键字段有确定性修复；报告了 `extract_mode` |
| 规则兜底的能力有限 | 复杂句式（多重条件、指代）规则无法覆盖 | 兜底只在小模型失败时启用，且会标记 `source="rules"` |
| `pending_changes` 只在内存 | 进程重启后"澄清到一半"的状态丢失 | 明确不持久化（避免复活半验证的规划上下文） |
| B 的作业表在内存 | 重启后 `JOB_STATE_LOST` | 有限重试后报错，不无限轮询 |
| 单机 8 GB 显存 | 模型加载受内存影响（本轮曾因 RAM 不足无法加载） | 记录环境；实验前回收工作集；报告中注明 |


---

## 8. 当前仍受 B / A 限制的问题

| 来源 | 问题 | 对 C 的影响 | 处理 |
|---|---|---|---|
| B | `optimality_proven` 不在结果顶层，只在 `result_detail.*` 内 | 直接读顶层会得到 `None` | C 用 `resolve_optimality()` 统一解析（V0 已修） |
| B | MIP 字典序 1e-9 下界导致偶发 `optimality_proven=false` | `OPTIMAL` 与"已证明"不等价 | C 不假设 OPTIMAL；D 的措辞按 `optimality_proven` 分支 |
| B | `parent_scenario_id` 只记血缘、不继承参数 | 只发 delta 会静默重置 | C 每轮发送完整快照（V0 已修） |
| B | 错误是异步的（202 → `job.status=FAILED`） | 4xx 不能作为失败依据 | C 只信 `job.status` + `error` |
| B | `improvement_options[].action` 必填，但工具示例曾缺该字段 | 模型产出的候选被 422 拒绝 | 本轮修正两臂的工具示例（同一 Schema） |
| B | 进程内 Job 表重启即丢 | 无限轮询会挂死 | C 识别 `JOB_STATE_NOT_FOUND`，有限重试后报错 |
| A | 数据 `DEMO_ONLY` | 任何结果都不能表述为现场可用 | `compose_answer` 固定输出 `DEMO_ONLY` 声明 |
| A | 本地 LFS 大文件只有指针 | 无法重建 GIS 原始链路 | 不影响 B 的 API 与 C 的 Harness |

**一个仍未解决的上游契约问题**：B 的 `changes` 与 `improvement_options` 是两个顶层字段，
但 5B 模型经常把候选塞进 `changes`，被 B 的 allowlist 正确拒绝。A/B 臂因此失败；
C/D 臂因为候选由独立 schema 承载，不受影响。这是"结构化意图"的直接收益之一，
但也说明 B 的工具契约对弱模型不友好（建议 B 在 422 的 `required_user_action` 里给出字段位置）。

---

## 9. 与 V1 的关系（不改写历史）

- V1 报告与 `docs/module_c/evidence/v1_*` 原样保留；
- V1 的 3/14 vs 6/14 **不再作为本轮的对照基线**：框架已修复 8 处不公平（见审计文档），
  本轮重新运行了 A/B 两臂；
- 因此本报告的 A/B 数值与 V1 数值不可直接相减；可比较的是**同一轮内**的四臂。

---

## 10. 复现命令

```bash
# 0) 依赖（本机已在 E:/ICT/_sp/pylibs* 中）
pip install -r requirements-b.txt -r requirements-c.txt

# 1) 本地模型（Windows）
set OLLAMA_NUM_PARALLEL=1
set OLLAMA_MAX_LOADED_MODELS=1
set OLLAMA_KV_CACHE_TYPE=q8_0
ollama serve

# 2) B 服务（可选：评测默认用进程内 ASGI，不强制起 HTTP）
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

# 3) 测试
python -m pytest tests/module_c -q

# 4) 四臂消融（--reps 2 会在同一进程内跑两轮；--rep-start 用于续跑）
python -m tests.module_c.run_v2 --arms all --reps 1 --output outputs/module_c/v2

# 5) MCP 的结构化入口（默认不注册）
set SHELTERPLAN_MCP_STRUCTURED=1
python -m agent.mcp_server --transport stdio
```

---

## 11. 交付文件

| 文件 | 说明 |
|---|---|
| `agent/intent/schema.py` | 意图信封、别名、JSON 修复 |
| `agent/intent/extractor.py` | LLM 抽取 + 修复 + 规则兜底 |
| `agent/intent/normalizer.py` | 参数操作、对比/敏感性计划、候选展开 |
| `agent/workflow/router.py` | 意图→任务路由、澄清、准入 |
| `agent/workflow/controller.py` | 执行、错误分类、重试、数值绑定 |
| `agent/workflow/answer.py` | 确定性作答 |
| `agent/structured_runner.py` | C/D 两臂运行器 |
| `tests/module_c/eval_lib.py` | 公平判分与指标 |
| `tests/module_c/run_v2.py` | 四臂评测器 |
| `tests/module_c/cases/v2_cases.json` | 23 条新用例 |
| `tests/module_c/test_intent.py` 等 | 75 条离线测试（含 MCP 入口 2 条） |
| `agent/mcp_server.py` | 新增可选工具 `handle_request`（默认关闭） |
| `docs/module_c/V1_EVAL_AUDIT.md` | V1 框架审计（8 处修复） |
| `docs/module_c/evidence/v2_*` | 原始 report / summary / trace |

---

## 12. 交付记录

| 项 | 值 |
|---|---|
| 分支 | `feature/module-c`（基于 B 的 `5f8bb512`） |
| 代码提交 | **`46fc667`** — 33 文件，+16,988 / −33 |
| 新增代码 | 2,688 行（`agent/intent` 1,111 · `agent/workflow` 515 · `structured_runner` 362 · `eval_lib`+`run_v2` 700） |
| 新增测试 | 75 条（`test_intent` 34 · `test_workflow` 26 · `test_eval_lib` 13 · `test_mcp_structured` 2），全量 153 条 |
| 上游改动 | **0**：`backend/`、`data/`、`configs/`、`scripts/` 均未触碰 |
| 远端 | **未 push、未合并** |
| 证据 | `docs/module_c/evidence/v2_report.json`、`v2_trace.jsonl`、`v2_fix_trace.jsonl`、`v2_rep2_*`、`v2_tables.md` |

## 13. 下一轮（V1.5 → AgentArts）计划

1. **把槽位抽取从提示词搬到结构化节点**：AgentArts 的 Code 节点做 `ops` 归一化，
   Condition 节点做缺参分支，LLM 只负责意图分类。
2. **换更强模型复跑同一 23 条用例**，验证"剩余失败是小模型能力边界"这一归因。
3. **把 D 臂的确定性作答抽成模板层**，让 C/D 的差异只剩"是否调用模型写作答"，
   便于未来在 AgentArts 里做开关。
4. **补多轮澄清回路**：当前 `pending_changes` 只在同一会话内保留，尚未覆盖"用户隔轮补充参数"的完整状态机。
5. **MCP 侧暴露同样的结构化入口**：`submit_structured_intent` 作为第 5 个工具（默认不暴露给模型）。
