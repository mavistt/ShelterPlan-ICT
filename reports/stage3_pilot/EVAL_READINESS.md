# ShelterPlan-Eval to B/C Readiness

## Adapter contract

`benchmark/shelterplan_eval/bc_adapter.py` 是显式适配器，不实现 Agent、不重写 Solver、不做 Mock，也不在 C 不可用时回退到旧结果。

Eval 向 C `/planning/disaster-recovery` 发送：

- `task_id`、`client_session_id`、`client_turn_id`、`idempotency_key`
- `region_id`、`data_version`、`source_manifest_digest`、`analysis_mode`
- `scenario`、`baseline_parent_scenario_id`
- `damaged_shelter_ids`、`budget_units`、`improvement_options`
- 用户目标和任务行为由冻结 Task/GT 保留，不能由返回标签反推成功

适配器只接受真实 C 返回的 job/result，并归一化以下证据：

- `execution_trace_id`、`result_id`、`region_id`、`data_version`、`data_status`
- 六阶段 `stage_results`
- `branch_decisions`
- `constraint_checks`
- `verification_results`

404/405 会明确返回 `NOT_RUN`/`UNSUPPORTED`；传输错误是 `INFRA_ERROR`；C/B 真实失败保留为失败，不会被转换成正确 Agent 行为。

## 只读核对的同伴 B/C 实现

在 `origin/feature/compositional-skill-harness` 上已核对：

- `/planning/disaster-recovery` 提交后由 C 调用 `execute_disaster_recovery`
- `CompositeWorkflowPlanner` 是有界六阶段：baseline、damage、diagnose、recovery、compare、verify
- composite contract 持久化 `stage_results`、`branch_decisions`、`constraint_checks`、`verification_results`、`execution_trace_id`
- C session 会保存 result/scenario lineage，guards 会校验区域、版本、父场景、阶段父子链、Trace 和数据状态
- B `module_a_source_registry` 使用显式 region/data-version allowlist，区域/版本不匹配时拒绝，不静默回退石岐数据

`origin/feat/composite-recovery-ui-bridge` 主要增加 UI/bridge 层，尚未作为 Eval 主线依赖合并。

## 当前可用性

| 评测对象 | 状态 | 证据/阻塞 |
| --- | --- | --- |
| Eval → 真实 B Solver（显式 A v2 adapter） | READY FOR PILOT | 冻结 8 题已真实执行，6 PASS、2 UNSCORABLE，均有结果文件和 Trace |
| Eval → 当前分支内 HTTP B | NOT_RUN | 本轮为避免 `fastapi.testclient` 导入挂起使用显式 direct B；不是静默切换 |
| Eval → C Harness disaster-recovery | NOT_RUN / BLOCKED | C 同伴代码未合并到长期 Eval 分支，需临时集成工作树或获批准的集成分支联调 |
| Flat Tool Agent | NOT_RUN | 没有实际 Agent/LLM 运行 |
| Fixed Workflow | NOT_RUN | 没有实际 Agent/LLM 运行 |
| Composite Recovery + Harness | NOT_RUN | 未把同伴分支冒充已验收依赖 |
| 正式多 Agent 对照评测 | NOT_READY | 缺 C 真实联调和三个实验组的同预算运行 |

## 联调前置条件

1. 在临时集成工作树中同时放入 Eval 当前 commit、已验收的 B/C commit；不要把未验收同伴分支强行合并到长期 Eval 主线。
2. 用相同 A v2 `dataset_content_hash`、`source_manifest_digest`、region、data_version、候选项目和预算，先跑石岐，再核验东区 allowlist；东区不得静默使用石岐数据。
3. C 返回并保存完整 session snapshot：父场景、有效参数、受损场所、阶段结果、分支决策、约束检查、Verifier 和 Trace。
4. 对 `EXECUTE`、`REJECT`、`CLARIFY`、`SAFE_STOP` 分别按 GT 评分：正确拒绝/追问/安全停止可以成功，但必须有实际 Agent/C 证据。
5. 固定相同 LLM、工具调用预算、Solver/Verifier、数据版本和 GT，分别运行 Flat Tool Agent、Fixed Workflow、Composite Recovery + Harness；没有真实运行的组保持 `NOT_RUN`。

## 下一步需要 B/C 同伴配合

- 提供可运行的 C HTTP/Harness 启动方式和稳定的 `/planning/disaster-recovery` contract。
- 提供东区与石岐的实际 allowlist/版本路由验证日志。
- 确认 C 返回的 session state 与 Eval 的父子多轮字段一一对应。
- 在同一数据和预算下完成三组 Agent 实测，保存 execution trace、tool calls 和真实 LLM token。
- 对 Composite Recovery 的能力表述保持准确：当前实现是固定有界状态机/工作流，不应称为完全动态或自进化 Skill。
