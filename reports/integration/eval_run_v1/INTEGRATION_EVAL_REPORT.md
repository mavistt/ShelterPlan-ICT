# A/B/C + ShelterPlan-Eval 集成与评测报告

生成时间（UTC）：2026-11-26T19:19:11.731261+00:00

## 集成范围与冻结 GT

- 冻结任务：24 个；`TASK_GT.json` 与 Release 原始任务集逐字节一致，SHA-256 为 `a2e2a846b903e738e6bdece6e9835ea8bd2b847eb515870dc2e3dfee9ef9ac4b`。
- B/C、前端以 UI Bridge 集成分支为主线；Eval 的场景、准入、Oracle、Evaluator、GT 与报告资产选择性并入。来源提交见 `MERGE_PROVENANCE.md`。
- 本报告中的 B 结构化 Pilot 是求解器/适配器执行结果，不代表 C Agent 成绩。

## 冻结任务与实测结果

- B 结构化 Pilot 实测 8/24：PASS=6，FAIL=0，UNSCORABLE=2，NOT_RUN=16；带完整执行 Trace 的结果=6。
- VETS hard success：6/8 个已尝试任务（75.0%）；可评分子集 6/6（100%）。2 个 UNSCORABLE 与 16 个 NOT_RUN 不计为成功，不能外推为完整任务集成绩。
- U/W/P/E 诊断均值（仅 B 结构化 Pilot）：U=0.9999999999912329，W=1.0，P=1.0，E=1.0。这些诊断值不等于 VETS，也不是 Agent 评分。
- Oracle：{"NOT_RUN": 5, "CONDITIONAL_SUBSET_OPTIMUM": 3}；其中 `CONDITIONAL_SUBSET_OPTIMUM` 不代表完全独立 Oracle 证明。
- Direct LLM、Flat Tools、Fixed Workflow、Composite + Harness 四个真实 Agent 对照组均为 NOT_RUN：未配置本地真实 LLM；未调用收费 API。RuleLLM 仅为 C Session/Turn → B Composite 联调 Smoke，不参与评分。
- 所有当前 A v2 规划数据均标记 `DEMO_ONLY`；`PASS_DEMO` 不表示现场核验或真实部署保证。

## A v2 数据准入

- 审计 87 条场所记录：56 条 DEMO 候选，0 条 VERIFIED，30 条待审核，1 条排除。
- 石岐与东区可用于受控 DEMO；小榄保持 disabled（30 条待审核、1 条排除）。未自动升级任何记录为 VERIFIED。
- 区域 Manifest 校验为只读；详见 `SOURCE_HASH_AUDIT.json`。

## 验证结果

- A/Eval 选定回归：53 项通过（PASS）。
- Module B 全量测试：{'passed': 35, 'failed': 3, 'skipped': 0}；失败来自旧版 `configs/pilot.yaml` 的 A v1 Manifest 哈希不匹配，详见 `reports/integration/eval_run_v1/module_b_pytest.log` 与 `FAILURE_ANALYSIS.md`。
- Module C 全量测试：{'passed': 266, 'failed': 48, 'skipped': 0}；失败以同一旧版 A v1 Manifest 阻塞及其依赖失败为主，详见 `reports/integration/eval_run_v1/module_c_pytest.log` 与 `FAILURE_ANALYSIS.md`。
- C 错误 Trace 关联回归：{'passed': 1, 'failed': 0, 'skipped': 0}。Python compileall：PASS。
- 前端：`PASS_WITH_PRE_FIX_FAILURES_RECORDED`；单元测试 6 通过，typecheck=PASS，build=PASS，浏览器 E2E=4 通过、0 失败。详情见 `FRONTEND_VALIDATION.json`。
- 浏览器 E2E 覆盖数据清单与 Eval 面板、石岐真实 C→B 规划、MIP/Greedy 对照、东区 C→B 与小榄禁用状态。

## 本地服务

- 前端：`http://127.0.0.1:5173`；Module B：`http://127.0.0.1:8000`；Module C：`http://127.0.0.1:8080`。C 提供只读评测摘要 `GET /v1/evaluation/summary`，没有独立 Eval 服务。所有服务仅绑定 loopback，不是公网服务。
- 启动命令（Windows PowerShell）：`powershell -ExecutionPolicy Bypass -File scripts/start_integration_demo.ps1`。脚本采用 RuleLLM Smoke 配置，不调用外部/收费模型。

## 限制

本报告记录真实集成证据，不宣称已完成真实 LLM 公平对照、24 题完整 Agent 评测或经现场核验的 GIS 规划。旧版 `configs/pilot.yaml` 的 `data/processed/demand_nodes.geojson` Manifest 不匹配是 B/C 全套回归的已知阻塞；不得通过改写历史 GT 或 A v1 Manifest 掩盖。
