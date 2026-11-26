# B/C 与 ShelterPlan-Eval 联调计划

版本：2026-11-26
状态：准备完成；真实 C Agent 运行待集成工作树和 LLM 条件

## 1. 适配器边界

### B Composite Adapter

benchmark/shelterplan_eval/b_composite_adapter.py 是结构化 B Composite Recovery 适配器的明确入口；旧的 bc_adapter.py 保留兼容导出。

它发送灾后恢复结构化合同到 B/C 集成分支的 planning disaster-recovery 路由，用于检查：

- region、data_version、source manifest digest；
- baseline、damage、diagnose、recovery、compare、verify 六阶段；
- stage_results、branch_decisions、constraint_checks、verification_results；
- result_id、execution_trace_id、Verifier 和 DEMO_ONLY 声明。

这条路径不是自然语言 Agent 评测，也不能产生正式 VETS。

### C Agent Adapter

benchmark/shelterplan_eval/c_agent_adapter.py 只调用 C 的会话 API：

1. POST /v1/sessions，显式传 region_id；
2. POST /v1/sessions/{session_id}/turns，发送审核过的 user_turns；
3. GET /v1/turns/{turn_id}，轮询 RUNNING 到终态；
4. GET /v1/sessions/{session_id}，保存同一会话的状态和结果引用。

C 适配器不发送 b_request_payload、gt_assertions、oracle_ref、manifest hash 或预先计算的答案。它只返回原始 Session/Turn 证据，评分仍由独立 Evaluator 完成。

## 2. user_turns 规则

user_turns 由冻结任务的业务参数生成，允许包含区域、需求比例、步行阈值、受损场所、预算和候选项目；不得包含：

- expected_behavior；
- GT assertion 或错误码；
- Oracle 结果、目标数值或 source hash；
- “请一定执行/请一定拒绝”一类直接泄漏标签的指令。

多轮任务必须复用同一 session_id，后续轮只修改用户明确修改的字段。缺预算任务不预填预算；非法 ID 任务把非法 ID 作为真实用户输入送入 C。

## 3. 临时集成工作树

建议以以下状态建立独立工作树：

- Eval：feature/shelterplan-eval，当前 e93a133；
- B/C 候选：origin/feat/composite-recovery-ui-bridge；
- 基线：origin/integration/abc-shiqi-v2；
- 数据：A v2 当前版本，保持 DEMO_ONLY；
- 运行目录：独立 trace、session state、B outputs，不能复用用户会话。

禁止直接把 B/C 候选分支合并到长期 integration。若候选代码无法在同一工作树启动，记录 NOT_RUN 或 INFRA_ERROR，不回退到直接 B 结果。

## 4. 验收顺序

| 顺序 | 检查 | 通过条件 |
| --- | --- | --- |
| 1 | B/C 进程健康 | B planning capabilities 与 C healthz 可访问 |
| 2 | Session 创建 | region_id 被接受，返回 session_id，data_version/data_status 可核对 |
| 3 | 自然语言单轮 | C 收到 user_turn，返回真实 turn_id 和终态 |
| 4 | 缺参追问 | 缺 budget 时返回 NEEDS_CLARIFICATION，不把 B 失败当作追问成功 |
| 5 | 多轮继承 | 同一 session 继续；区域、版本、受损场所和未修改参数保持 |
| 6 | 复合阶段 | 真正产生六阶段证据、分支决策、约束检查和 Trace |
| 7 | 东区路由 | 东区不能静默使用石岐配置，allowlist 和 data_version 必须匹配 |
| 8 | GT 评分 | 只在实际 C 证据齐全后计算 VETS；没有 Agent 输出保持 UNSCORABLE/NOT_RUN |

## 5. 当前阻塞

同伴分支已经包含 B 的灾后恢复路由和 C 的 session/turn API，但长期 Eval 分支没有合并该候选；当前环境也没有确认可用的真实 LLM 凭据。RuleLLM 只能作为确定性 Smoke Test，不能写入正式 Agent 成绩。

因此本计划完成后，正确的当前状态仍是：

- B 结构化 Solver Pilot：已完成，6 PASS、2 UNSCORABLE；
- C Agent 端到端：NOT_RUN，直到临时集成和模型条件满足；
- Flat、Fixed、Composite 公平对照：NOT_RUN；
- 正式 Agent VETS：NOT_READY。

