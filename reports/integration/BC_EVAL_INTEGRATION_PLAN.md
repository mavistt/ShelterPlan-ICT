# B/C 与 ShelterPlan-Eval 联调计划

版本：2026-11-26  
状态：RuleLLM 已完成冻结 8 题 Session/Turn Smoke；正式 C Agent 评测仍待真实 LLM、Composite 六阶段验收和公平对照

## 适配器边界

- B Composite 走结构化灾后恢复合同，必须保留 `region_id`、`data_version`、六阶段结果、约束检查、Verifier 和 `execution_trace_id`。
- C Agent 只走自然语言 `POST /v1/sessions`、`POST /v1/sessions/{id}/turns`、Turn 轮询和 Session snapshot；创建 Session 时显式传 `region_id` 与 `data_version`。
- C 适配器不发送 GT、Oracle、Manifest hash、B request payload 或预先计算答案；VETS 只能由独立 Evaluator 计算。

## 已完成的真实联调

2026-11-26 使用真实 B/C uvicorn 进程、localhost socket 和现有冻结 8 题完成：

- 8/8 任务创建了真实 C Session 并完成至少一个真实 Turn；
- 5 题 `SUCCEEDED`，3 题 `NEEDS_CLARIFICATION`；
- 多轮题复用了同一个 `session_id` 并取得最终 Session snapshot；
- 每题核对 `region_id`、A v2 `data_version`、`data_status=DEMO_ONLY`；成功任务返回 `result_id` 和 `execution_trace_id`；
- RuleLLM 只用于确定性 Smoke，不写入正式 Agent/VETS 成绩。

## 已进入 B/C 候选的契约修复

候选提交 `c42e967` 完成以下最小修复：

1. `RegionInfo` 公共字典补齐 `enabled` 与 `exclusion_reason`；
2. C Session 请求接收并校验 `data_version`，不匹配时拒绝；
3. B 结果已有的 `provenance.execution_trace_id` 透传到 C Turn；
4. legacy 配置不会静默把 `shiqi` 映射到旧区域；正式 v2 联调必须显式使用 A v2 配置和版本。

## 尚未完成的验收

- 真实 LLM：当前没有可核验的模型凭据/配置，状态保持 `NOT_RUN`；
- Composite 自然语言六阶段：B 结构化路由和单元回归存在，但 C Session/Turn 的完整六阶段证据尚未正式验收；
- Flat Tool Agent、Fixed Workflow、Composite Skill + Harness：尚未在同模型、同数据、同 Solver/Verifier、同工具预算下公平运行；
- 正式 VETS：尚未生成，不得使用 RuleLLM、直接 B Solver 或普通单测替代；
- D 前端：Vitest 4/4 与 Vite production build 已通过，浏览器点击链路和地图结果展示仍需复验。

## 验收命令

```bash
python scripts/run_session_demo.py
python scripts/run_session_demo.py --llm-mode real
cd frontend
npm install
npm run test
npm run build
```

所有输出继续标注 `DEMO_ONLY`。完整判断见 `reports/integration/PROJECT_INTEGRATION_ACCEPTANCE.md`。
