# Module C｜Harness V1 实验报告：真实 LLM Agent 与 Baseline/Harness 对照

> 分支：`feature/module-c`（本地）· 基线：上游 `5f8bb512` + C-V0 `503aa2f`
> 模型：**gemma4:e2b**（5.1B, Q4_K_M），本机 Ollama 0.23.1（`http://127.0.0.1:11434/v1`，OpenAI-compatible，原生 tool calling 实测可用），temperature=0，max_tokens=2400
> 硬件：RTX 4070 Laptop 8GB（模型本地权重，无任何付费 API、无公网部署）
> 运行日期：2026-11-12
> 声明：V0 的确定性结果（66 tests、25 P0）仍单独成立；本报告的百分比全部描述**真实 LLM Agent** 在两条工具臂下的表现，两者不可混用。

---

## 1. 完整目标对照

| # | 要求 | 交付 |
|---|---|---|
| 1 | 复核 C-V0 并保留 | ✅ 工作树干净 @ `503aa2f`；66 tests 复测通过；25 条 P0 复跑 25/25（`outputs/module_c/v0-recheck`） |
| 2 | 可配置 LLM Adapter（OpenAI-compatible Tool Calling） | ✅ `agent/llm_adapter.py`（base_url/model/key/温度/超时/`reasoning_effort` 全部环境变量化；transport 可注入） |
| 3 | 最小 Agent 执行器 | ✅ `agent/agent_loop.py`（NL → 模型选工具 → 调 B → 结果 → 回答；有界轮次 + 空回答催答 + 纠错反馈） |
| 4 | Baseline / Harness 双臂 | ✅ `agent/agent_tools.py`：同 4 工具名、同系统提示、同模型；差异仅在机制（见 §3） |
| 5 | 同条件对照 | ✅ 同模型/温度/用例/数据版本/工具面；harness 不额外提供答案或隐含参数 |
| 6 | 指标 | ✅ 成功率/参数正确性/数值一致率/守卫拒绝/p50·p95/LLM 调用/工具调用/token（§6） |
| 7 | 任务集 + 开发/保留划分 | ✅ `tests/module_c/cases/nl_cases.json`：10 dev + 14 holdout（开发集仅用于迭代，保留集只跑一次） |
| 8 | 脱敏 Trace + 失败归因 + 针对性优化 | ✅ 266 行 JSONL（两轮）；失败分 6 类；两项 harness 优化（§7） |
| 9 | 可复现结果与案例 | ✅ 命令与证据文件（§9）；H-01/H-09 对照案例（§8） |

---

## 2. 证据等级划分（重要）

| 类别 | 内容 | 证据 |
|---|---|---|
| **真实模型实验** | 24 条 NL 用例 × baseline/harness 双臂（开发集 2 轮 + 保留集 1 次） | `docs/module_c/evidence/v1_dev_report.json`、`v1_holdout_report.json`、`v1_*_trace.jsonl` |
| **确定性测试** | 78 个自动化测试（66 个 V0 已有 + 12 个新增：适配器契约 6、脚本化 Agent 循环 6） | pytest 输出（§4） |
| **注入故障** | LLM 存根服务器（500/422/非 JSON）、脚本化 LLM（无模型）；仅用于验证循环机制 | `test_llm_adapter.py`、`test_agent_loop.py` |
| **未验证项** | AgentArts 云端接入、更大模型、公网 MCP、人类被试 | 本轮明确不做 |

**没有伪造任何模型数据。** 所有真实实验数字可由 §9 命令复现。

---

## 3. 双臂定义（消融设计）

两臂**完全相同**：系统提示（同一段文字）、4 个工具名、工具 JSON Schema（含参数示例）、模型、温度、用例、数据版本（`zhongshan_pilot_v0.1` / `DEMO_ONLY`）。

| 机制 | baseline | harness |
|---|---|---|
| 会话参数快照（每轮完整回填） | ✗ 无状态：B 按默认值重建情景 | ✓ `Session.plan_turn/commit` |
| 参数 Guard | ✗ 只有 B 的 422 | ✓ preflight 12 类校验 |
| 结果 Guard（验证状态/情景一致/最优性声明） | ✗ | ✓ postflight |
| 重试策略 | ✗ 单次尝试（传输层也不重试） | ✓ 传输重试 2 + 同键作业重试 1 + 参数纠错反馈重试 2 |
| 结果投影 | ✗ 原始文档按深度裁剪（协议级） | ✓ ≤20KB 语义投影 |
| 数值引用闸门（解释与结果一致） | ✗ | ✓ 带一次强制重写 |
| DEMO_ONLY 表述闸门 | ✗ | ✓ 带一次强制重写 |
| Trace | ✗ | ✓ JSONL（本轮 266 行） |

> 设计说明：把"缺预算先询问"等**行为策略**放进共享提示词，让 baseline 也能正确地提问；harness 的增量只在**机制**。这样测得的是机制价值，不是提示词长度价值。

---

## 4. 自动化测试真实结果

```
python -m pytest tests/module_c -q -p no:cacheprovider
78 passed, 0 failed in 140.57s
```

| 文件 | 用例 | 说明 |
|---|---:|---|
| V0 既有 7 个文件 | 66 | 全部保持通过（本轮未改动 V0 行为语义） |
| `test_llm_adapter.py` | 6 | 工具调用解析、token 记账、`reasoning_effort` 注入/省略、422/500/非 JSON、重试次数 |
| `test_agent_loop.py` | 6 | 参数合并（脚本化 LLM）、baseline 参数重置机制、数值闸门强制重写、DEMO 闸门、缺预算拒绝、轮次上限 |

上游回归：`tests/module_b` 仍 29 passed / 1 failed（B 浮点问题未修，C 未触碰）。

---

## 5. 保留测试集对照结果（主要结果，14 条 NL 用例，仅跑一次）

### 5.1 指标对照

| 指标 | baseline | harness | Δ |
|---|---:|---:|---:|
| 用例通过率 | **21.4%** (3/14) | **42.9%** (6/14) | **+21.4pp** |
| 任务成功率（有期望任务的 9 条） | 0.0% | 22.2% | +22.2pp |
| 参数正确率（4 条多轮/参数用例） | 0% | 0% | 0 |
| 数值解释一致率（11 条） | 18.2% | 36.4% | +18.2pp |
| 守卫拒绝正确率（3 条） | 33.3% | 66.7% | +33.3pp |
| DEMO 合规率 | 18.2% | 36.4% | +18.2pp |
| 歧义处置正确率（1 条） | 100% | 100% | 0 |
| LLM 调用/用例 | 3.57 | 2.57 | −28% |
| 工具调用/用例 | 1.71 | 1.29 | −25% |
| 工具失败总数 | 4 | 3 | −1 |
| tokens/用例 | 5,156 | 5,180 | +0.5% |
| LLM p50 / p95 | 25.2s / 45.3s | 27.9s / 65.5s | +10.7% / +44.6% |
| 用例墙钟 p50 / p95 | 25.3s / 46.2s | 33.6s / 91.2s | +32.9% / +97.4% |

**失败归因（保留集）**

| 失败类型 | baseline | harness |
|---|---:|---:|
| 工具/任务选错 | 7 | 6 |
| 数值编造 | 5 | **1** |
| DEMO 越权表述 | 3 | **1** |
| 该问不问（猜参数） | 2 | **1** |
| 其它 | 2 | 3 |

harness 消除了大部分**数值编造、越权表述、猜参数**失败（合计 10→3）；剩余失败以**工具/任务选择错误**为主（两臂 6/7 条相同），这是 5.1B 模型的能力边界，机制无法替代。

### 5.2 开发集迭代记录（2 轮，仅用 dev 调参）

| 轮 | harness | baseline | 当轮发现 → 修改 |
|---|---|---|---|
| 1 | 6/10 | 3/10 | D04 compare 传参格式错、D06/D07 数值审计误伤（"30%"、百位取整）→ ① 工具 schema 加完整参数示例（两臂同加）；② harness 新增**参数纠错反馈重试**（GUARD_INVALID/B_* 类错误把结构化错误+required_user_action 反馈给模型，上限 2 次）；③ 数值审计支持百分比表述（0.3↔30%）、千位逗号、百位桶粗取整；④ 用户自己说的数字不算违规 |
| 2 | 7/10 | 3/10 | D09 DEMO 重写后仍不合规 → 纠正消息改为硬性要求（必须包含字面 `DEMO_ONLY`，删除违禁表述） |
| — | — | — | 合并 dev：harness 13/20 vs baseline 6/20 |

开发集逐例（第 2 轮）：

```
baseline D01✗ D02✗ D03✗ D04✗ D05✓ D06✗ D07✗ D08✓ D09✗ D10✓   → 3/10
harness  D01✓ D02✓ D03✗ D04✓ D05✓ D06✗ D07✓ D08✓ D09✗ D10✓   → 7/10
```

### 5.3 残留失败与诚实归因

| 用例 | 失败 | 归因 |
|---|---|---|
| H-02/H-03/H-13 | 模型未把用户给出的参数（预算减半、比例 0.4→改 10 分钟、改回 0.2）写进 `changes` | **模型能力**（5.1B 对多轮参数抽取不稳定）；机制不能替模型理解语言 |
| H-05/H-07 | 未调用 diagnose/sensitivity 工具，直接文字作答 | **模型能力**（工具选择）；harness 无法强迫 |
| H-04 | 模型把"评估"做成了 stress_test；DEMO 重写后仍缺声明（1 条残留） | 模型 + 小模型改写服从性 |
| H-10 | 拒绝话术未匹配启发式（两臂） | 评估启发式 + 模型话术 |
| H-14 | explain 链路未触发（模型未先 assess） | 模型能力 |

**墙钟 p95 变长的解释**：harness 在守卫/闸门触发时多 1–2 次 LLM 调用（重写/纠错），且失败的 baseline 用例提前结束；LLM p50 仅 +10.7%，属于可接受代价。tokens/用例基本持平（+0.5%）。

---

## 6. 两个典型对照案例（保留集原文）

### 案例 1｜NL-H-01（现状评估）

- **baseline（失败）**：反问"您想要评估、优化、比较还是压力测试？"——未调用任何工具。
- **harness（通过）**：2 次 LLM 调用完成 assess，回答含权威数字（`diagnosed_unserved_nodes: 7634` 等）并按 DEMO_ONLY 口径表述。

### 案例 2｜NL-H-09（负预算）

- **baseline（失败）**：把 B 的原始 Pydantic 校验错误（`"Input should be greater than or equal to 0"`、`url: https://errors...`）直接倾倒给用户。
- **harness（通过）**：识别负预算语义，礼貌追问"预算通常需要为正值，请确认 -5 的含义"，未提交任何任务。

---

## 7. 本轮 harness 优化清单（开发集驱动）

| # | 优化 | 触发证据 | 结果 |
|---|---|---|---|
| 1 | 工具 Schema 加完整参数示例（两臂同加） | dev1：D03/D04 因参数格式错失败 | dev2：D04 通过 |
| 2 | 参数纠错反馈重试（`GUARD_INVALID_PARAM`/`B_*` → 结构化错误 + required_user_action 反馈给模型，≤2 次） | dev1：D04 | dev2：D04 通过 |
| 3 | 数值审计增强：百分比表述（0.3↔30%）、千位逗号、百位桶粗取整、用户数字豁免 | dev1：D06/D07 误报 | dev2：D06/D07 数值检查通过（其余失败为模型未调工具） |
| 4 | DEMO 纠正消息硬性化（必须包含字面 `DEMO_ONLY`） | dev2：D09 重写后仍违规 | 单例复验通过（rewrite=True 且合规） |
| 5 | 空回答催答（thinking 模型偶发 stop 无内容） | 冒烟 | 离线用例覆盖 |

---

## 8. 交付文件清单（本轮新增/修改）

```
agent/llm_adapter.py            # OpenAI-compatible 适配器（tool calling、usage、重试）
agent/agent_loop.py             # AgentRunner（双臂共用；纠错反馈/催答/双闸门/Trace）
agent/agent_tools.py            # 双臂工具注册表 + 共享提示词 + 参数示例
agent/numeric_audit.py          # 数值引用审计（百分比/比值/粗取整/用户数字豁免）
agent/service.py                # +last_result_id()
agent/llm_adapter 支持注入 transport（测试用）
tests/module_c/run_v1.py        # 对照实验运行器（dev/holdout、双模式、指标、对比表）
tests/module_c/cases/nl_cases.json        # 24 条 NL 用例（10 dev + 14 holdout）
tests/module_c/scripted_llm.py  # 脚本化 LLM（离线确定性）
tests/module_c/test_llm_adapter.py        # 6 用例
tests/module_c/test_agent_loop.py         # 6 用例
tests/__init__.py               # 使 tests 成为包（不影响 B 测试）
docs/module_c/HARNESS_V1_REPORT.md        # 本报告
docs/module_c/evidence/v1_dev_report.json / v1_dev_summary.md / v1_dev_trace.jsonl
docs/module_c/evidence/v1_holdout_report.json / v1_holdout_summary.md / v1_holdout_trace.jsonl
requirements-c.txt 不变（openai SDK 未使用，直接 httpx；mcp 沿用）
```

**未修改**：`backend/`、`configs/`、`data/`、`scripts/`、V0 行为语义。

---

## 9. 复现命令

```bash
# 1) 本机推理服务（Ollama，本地权重，免费）
ollama serve                                    # 或直接 ollama list 自动拉起

# 2) Module B（仓库根目录）
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

# 3) 自动化测试（含离线脚本化 Agent 测试，不需要模型）
python -m pytest tests/module_c -q              # 78 passed

# 4) 真实模型对照实验
export SHELTERPLAN_LLM_BASE_URL=http://127.0.0.1:11434/v1
export SHELTERPLAN_LLM_MODEL=gemma4:e2b
export SHELTERPLAN_LLM_TEMPERATURE=0
export SHELTERPLAN_LLM_MAX_TOKENS=2400
python -m tests.module_c.run_v1 --split dev     --output outputs/module_c/v1-dev
python -m tests.module_c.run_v1 --split holdout --output outputs/module_c/v1-holdout
# 可选：--modes harness / --base-url http://127.0.0.1:8000（真实 HTTP 到 B）
```

运行环境：Python 3.12.4 · httpx 0.27.2 · mcp 1.27.0 · fastapi 0.162.4 · numpy 1.26.4 · scipy 1.13.1 · pyarrow 18.1.0 · Windows 11（`NO_PROXY=127.0.0.1,localhost`）。

---

## 10. 结论与下一轮

**结论**：在相同模型、提示词、工具面与用例下，Harness 机制把真实 LLM Agent 的用例通过率从 21.4% 提升到 42.9%（+21.4pp），数值编造 5→1、DEMO 越权 3→1、猜参数 2→1，LLM 调用次数 −28%，token 成本持平。机制无法修复的部分集中在**小模型的工具选择与多轮参数抽取**——这需要更强的模型（或 AgentArts 侧的结构化槽位节点），而不是更多 Harness 代码。

**下一轮（V2 建议）**：

1. 换用更强模型复测同一冻结任务集（如 GLM/Qwen 9B+ 或 AgentArts 托管模型），验证"参数抽取失败"是否随模型能力消失；
2. 把"槽位抽取"从提示词移到 AgentArts 的结构化节点/Code 节点，绕开小模型 NL 抽取短板；
3. 槽位确认交互（缺参追问）接入 AgentArts 的 clarification 机制；
4. 保持 25 条 P0 + 24 条 NL 用例冻结作为回归基线；holdout 不再用于调参。
