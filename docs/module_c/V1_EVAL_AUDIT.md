# V1 评测框架可复现性审计（V2 前置）

审计对象：`feature/module-c` @ `bb8f9c6`（V1 交付）的 **评测框架**，不是 V1 结论本身。
审计方法：逐行阅读 `tests/module_c/run_v1.py`、`agent/agent_loop.py`、`agent/agent_tools.py`、
`agent/numeric_audit.py`，并用 V1 归档的 `docs/module_c/evidence/v1_holdout_report.json`
逐条回放判分逻辑。

> **不改写历史**：V1 报告与 `docs/module_c/evidence/v1_*` 原样保留。本文只记录"框架哪里不公平"，
> 以及 V2 如何在**新框架**（`tests/module_c/run_v2.py` + `tests/module_c/eval_lib.py`）中修复。
> 因此 **V2 的 A/B 臂数值与 V1 的 3/14、6/14 不可直接相减**：它们是同一机制在公平框架下的重跑。

---

## 1. 实验顺序固定，harness 总是第二个跑

**现象**：`run_v1.py:223-224` 是

```python
for case in cases:
    for mode in modes:          # ("baseline", "harness")
```

每个用例都先跑 baseline、再跑 harness。模型常驻显存，第一个用例冷启动；后续同一用例里
harness 总是享受"刚跑过一遍"的热状态。这不是随机误差，而是**方向固定**的系统偏差，且偏向 harness。

**影响**：V1 的 harness 延迟与失败率可能被低估；反过来，如果机器越跑越热/越慢，也可能反转。

**V2 修复**：`run_v2.py:run()` 对每个用例、每个 repetition 旋转臂顺序：

```python
shift = (index + rep) % len(arms)
order = arms[shift:] + arms[:shift]
```

并支持 `--reps N` 逐条配对。`tests/module_c/test_eval_lib.py::test_paired_counts_wins_losses_ties`
锁定配对统计，不再只报总平均。

---

## 2. harness 的"重写"调用没有计入 token 与延迟

**现象**：`agent_loop.py:344` 的 `_audit()` 直接调用 `self.llm.chat(...)`，但没有追加
`LLMCall`，也不递增 `call_index`：

```python
result = self.llm.chat(messages, tools=self.registry.schemas)   # 未记账
```

**影响**：每次触发重写（V1 保留集里 harness 有多次 demo/数值违规），harness 的
`llm_calls`、`prompt_tokens`、`completion_tokens`、`llm_latency_ms` 全部少算一次完整调用。
V1 报告里 "tokens/用例 5,180" 与 "harness 延迟更高" 都是**下界**。

**V2 修复**：`StructuredRunner._audit()` 通过 `_record_llm_calls(..., phase="rewrite")` 记账
（`agent/structured_runner.py`）；A/B 臂沿用 `AgentRunner`，V2 运行器中每次调用都带 `phase`
并计入 `record["llm_calls"]`。

---

## 3. baseline 的数值审计用了模型**没有看到**的完整文档

**现象**：`run_v1.py:75-84` 的 `_ground_truth_numbers()`：

```python
for document in (baseline_state.get("results") or {}).values():
    numbers |= collect_numbers(document)      # 5-11 MB 原始结果
for row in baseline_state.get("requests") or []:
    numbers |= collect_numbers(row.get("effective_params") or {})
```

baseline 模型实际收到的只是 `elide()` 之后的精简文档（`agent_tools.py` 的 `get_result`），
但判分用的是完整结果 + 内部 `effective_params`。也就是说：**baseline 被允许引用它从未见过的数字**，
而 harness 只被允许引用自己的投影。这条只会让 baseline 更容易"通过"，方向对 harness 有利，
但它让"数值一致率"这个指标失去意义。

**V2 修复**：baseline registry 现在把**模型实际收到的文档**记录在 `state["visible"]`
（`agent_tools.py`），`run_v2.py` 用它做权威集合；C/D 用自己产出的 summaries。

---

## 4. baseline 的"完成"归属错误

**现象**（V1）：`agent_tools.py` 的 `get_result()` 无条件把**最后一个**请求标记为完成：

```python
if state["requests"]:
    state["requests"][-1]["completed"] = ...
```

如果一个用例里模型提交了两次任务（V1 保留集常见），第二次失败、第一次成功时，完成标记会错位，
`task_types` 判分随之出错。

**V2 修复**：`submit_task` 记录 `job_id → request`，`get_job` 记录 `result_id → request`，
`get_result` 按映射归属（`agent_tools.py:baseline_registry`）。

---

## 5. 拒绝/澄清判定过宽（中文子串匹配）

**现象**：`run_v1.py:87-94`：

```python
if "？" in text or "?" in text:
    return True
return any(token in text for token in ("请提供", ..., "无法", "不能", "无效", "拒绝"))
```

两个方向都错：

* 一句正常结论里出现"当前方案**无法**覆盖全部需求"会被判成"已拒绝/已澄清"；
* 一个反问句（"您是想评估还是优化？"）会被判成澄清，即使用例要求的是执行。

**V2 修复**：`eval_lib.asks_or_refuses()` 只在以下情况判定为澄清/拒绝：
明确的拒绝短语（"未执行""无法执行""缺少必要参数"…）、明确的请求短语（"请提供""请指定"…）、
或长度 ≤80 字的疑问句。C/D 臂优先使用**结构化** `clarification_fields`，不依赖文本。
`test_asks_or_refuses_is_narrow` 锁定两个反例。

---

## 6. 两臂工具 Schema 相同、能力不同

**现象**：`_submit_schema()` 同时被两臂使用，其中声明了 `result_id` 与 `claim`；
harness 的 handler 接受这两个参数，V1 的 baseline handler **不接受**（`agent_tools.py:209-212`）。
模型只要填了 `claim`，baseline 就抛 `TypeError`，而 harness 正常。

**影响**：这是"同一份 Schema、不同实现"的不公平，方向偏向 harness。

**V2 修复**：baseline handler 接受并语义化处理 `result_id`（`explain_result` 时转发）与 `claim`（忽略）。

---

## 7. 缺少必填工具参数时抛 `TypeError` 而不是结构化错误

**现象**：模型漏掉 `task_type` 时，`ToolRegistry.execute` 把 Python `TypeError` 原样作为
`error.code` 返回。两臂都受影响，但 harness 的"有限重试"因此无法识别"可纠正错误"。

**V2 修复**：新增 `ToolArgumentError`（`TOOL_ARGUMENT_ERROR`，`agent/errors.py`），
两臂的 `submit_task` 在缺少 `task_type` 时抛出；`AgentRunner.CORRECTABLE_ERRORS` 包含它，
重试仍然有界（≤2）。

---

## 8. 工具粒度不同，调用次数不可直接比较

**设计事实**（不是 Bug）：harness 的 `submit_task` 内部完成 submit→poll→fetch→project，
模型只调用一次；baseline 需要 submit→get_job→get_result 三次协议调用。

**影响**：V1 报告里 "LLM 调用/用例 3.57 → 2.57" 部分来自工具粒度，而不是模型更聪明。

**V2 处理**：同时报告 `llm_calls_per_case`、`tool_calls_per_case` 与
`planning_tasks_per_case`（真实业务任务数），并在报告中明确说明该差异是**机制设计**的一部分，
不把它当作"模型效率提升"。

---

## 9. 温度 0 不等于确定性

Ollama 在 temperature=0 下仍因批处理/并行而出现输出差异（V1 §5.3 已记录）。
V1 每个用例只跑一次，无法区分"机制差异"与"采样噪声"。

**V2 处理**：`--reps N` + 逐条配对（`paired()`），报告中给出 wins/losses/ties；
样本量不足时**不宣称统计显著**（报告 §7 明确写出）。

---

## 结论

| # | 问题 | 方向 | V2 是否修复 |
|---|---|---|---|
| 1 | 臂顺序固定 | 偏向 harness | ✅ 旋转顺序 + reps |
| 2 | 重写调用未记账 | 低估 harness 成本 | ✅ 分 phase 记账 |
| 3 | baseline 数值集合过宽 | 高估 baseline 数值一致率 | ✅ 用模型可见文档 |
| 4 | baseline 完成归属错误 | 随机偏向 | ✅ job/result 映射 |
| 5 | 澄清判定过宽 | 双向失真 | ✅ 结构化 + 窄文本启发 |
| 6 | Schema 声明与实现不一致 | 偏向 harness | ✅ 补齐 handler |
| 7 | 缺参抛 TypeError | 双方都受损 | ✅ 结构化错误 |
| 8 | 工具粒度不同 | 混淆调用次数 | ✅ 分列指标 |
| 9 | 单次采样 | 噪声不可分 | ✅ reps + 配对 |

**对 V1 结论的影响**：V1 的核心定性结论（"机制能消除数值编造与 DEMO 越权"）与代码结构一致，
仍然成立；但 **3/14 vs 6/14 的具体数值不再作为 V2 的对照基线**。V2 重新运行 A/B 臂，
并在同一框架、同一测试集、同一模型下与 C/D 比较。
