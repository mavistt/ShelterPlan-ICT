# ShelterPlan-Eval Stage 3.1 Review

本报告由本轮代码重新生成的冻结 Pilot 结果整理而成。没有重新生成场景，没有覆盖 Stage 2.1 历史归档，没有修改 A v2 GIS/OD 数据，也没有把同伴 B/C 分支合并到 Eval 主线。

## 本轮运行边界

- 冻结输入：`benchmark/shelterplan_eval/pilot_stage2_1/accepted_scenarios.json`
- 案例数：8；没有扩展到 144 道任务
- 实际命令：`python -B -m benchmark.shelterplan_eval.stage3_pilot --no-http --output-dir reports/stage3_pilot`
- 执行模式：`DIRECT_B_SOLVER_EXPLICIT_A_ADAPTER`，调用真实 B Solver、Verifier 和 A v2 区域适配器；不是 Mock/伪造结果
- `dataset_content_hash`：`609a14546cd10baa28c455a59de7503d2010ee980b502cad27451435f97d730f`
- `source_manifest_sha256`：`9f1fa0d4438b7db9c877a34404915a3d8ee2b4090910ba5e3c1bde3f3289faa9`
- 数据状态：所有真实 GIS 结果保持 `DEMO_ONLY`
- LLM/Agent：`NOT_RUN`

## 修复内容

1. 修正 B 请求契约：`PlanningScenario` 是严格模型，Eval 不再把 A 的 `dataset_content_hash` 和 `source_manifest_sha256` 塞入其嵌套字段；两项身份仍保存在 Eval 任务和运行 Manifest 中。
2. 修正多轮结果：子轮复制并保存 `region_id`、`data_version`、`data_status`、`analysis_mode`、`scenario_hash`、`scenario_changes`、结果 ID、选项、成本、Verifier 和 Trace。
3. 多轮校验不再只比较父子 ID：增加父子场景哈希、区域、版本、数据状态、分析模式、需求比例、步行阈值、禁用场所继承、预算变化、选项变化和双轮 Trace 检查。哈希按 B 的 Pydantic 规范化模型计算。
4. Oracle 分级：真实 A v2 浮点数据的穷举仅标记 `CONDITIONAL_SUBSET_OPTIMUM`，明确复用了 B 的 assignment routine；只有 `SYNTHETIC_FIXTURE` 小型整数数据才使用独立纯 Python Dinic 最大流并标记 `INDEPENDENT_ORACLE_PROVEN`。
5. VETS 改为硬断言二元结果；U/W/P/E 诊断、Planning Quality、Evidence 和 Cost 分开输出。`UNSCORABLE`、`NOT_RUN`、`INFRA_ERROR` 不会被转换为成功。
6. REJECT 必须匹配预期错误码、失败阶段、求解器未启动和区域/版本身份；本轮不是“任意 FAILED 都算正确”。
7. 新增 `benchmark/shelterplan_eval/bc_adapter.py`，只负责将 Eval 任务提交给真实 C disaster-recovery 端点并归一化阶段/分支/约束/Trace；端点不存在时返回 `NOT_RUN`，没有 Solver 回退。

## 冻结 8 题的实际结果

| 案例 | 预期行为 | B 实际状态 | Eval/VETS | Oracle | execution_trace_id |
| --- | --- | --- | --- | --- | --- |
| `ST21-shiqi-000` | EXECUTE | SUCCEEDED | PASS / true | NOT_RUN | `d6b63235-8d5f-4fe5-adaf-541c7a692f7e` |
| `ST21-east_district-001` | EXECUTE | SUCCEEDED | PASS / true | CONDITIONAL_SUBSET_OPTIMUM | `97f3d6c4-e6f8-4ea5-aa9a-f04bb5b3ba8e` |
| `ST21-shiqi-002` | EXECUTE | SUCCEEDED | PASS / true | CONDITIONAL_SUBSET_OPTIMUM | `10d0340b-3b0d-413e-b661-da751c631bca` |
| `ST21-east_district-003` | EXECUTE | SUCCEEDED | PASS / true | NOT_RUN | `75726456-d511-47cb-9c4c-a1215e6ff1c7` |
| `ST21-shiqi-004` | EXECUTE / parameter_edit | SUCCEEDED | PASS / true | CONDITIONAL_SUBSET_OPTIMUM | `b4dad5be-2751-4104-9798-f5c1960c638a` |
| `ST21-east_district-011` | SAFE_STOP | SUCCEEDED | UNSCORABLE / null | NOT_RUN | `e472cde6-08af-442b-9dca-2b1db50f0166` |
| `ST21-east_district-029` | CLARIFY | FAILED（缺少 budget） | UNSCORABLE / null | NOT_RUN | NONE |
| `ST21-shiqi-016` | REJECT | FAILED（`CANDIDATE_INVALID`） | PASS / true | NOT_RUN | NONE |

汇总：`PASS=6`、`FAIL=0`、`NOT_RUN=0`、`INFRA_ERROR=0`、`UNSCORABLE=2`。VETS 硬成功为 `6/8`；若只看可评分样本是 `6/6`，但正式汇报必须同时保留 2 个 `UNSCORABLE`，不能写成 100% 的全体任务成功率。

### 关键证据

- `ST21-shiqi-004` 的父子轮均通过场景哈希、区域、版本、`DEMO_ONLY`、需求比例、步行阈值、禁用场所继承、预算显式变化、选项显式变化和 Trace 校验。
- `ST21-shiqi-016` 的实际错误码是 `CANDIDATE_INVALID`，失败阶段为 `candidate_validation`，并确认没有进入 Solver Trace；因此是正确拒绝。
- `ST21-east_district-011` 的容量约束安全停止探针为 `false`，所以没有因为 family 名称直接授予 SAFE_STOP 分数。
- `ST21-east_district-029` 的 B 失败是请求缺少 budget；由于本轮没有 Agent 输出，正确状态仍是 `UNSCORABLE`，不是把 B 的失败伪装成 CLARIFY 成功。

## Oracle 与难度

真实 A v2 的三个预算案例都有可复现的合法子集枚举，但其结果调用了 B 的 assignment routine，证明等级为 `CONDITIONAL_B_ASSIGNMENT_SUBSET_CHECK`，不能宣称独立全局最优。小型 synthetic fixture 的独立 Dinic 回归测试通过，但没有把该夹具证据移植给真实 GIS 案例。

本轮实际测得的难点包括：区域/版本绑定、预算候选竞争、真实 OD 容量服务量、父子状态继承以及无效候选拒绝。没有 Agent Trace 的 CLARIFY/SAFE_STOP 仍为 `NOT_MEASURED`/`UNSCORABLE`，没有用设计标签替代实测难度。

## 测试结果

- PASS：`python -B -m unittest tests.test_stage3_pilot tests.test_admission_gate tests.test_preflight_manifest tests.test_scenario_factory tests.test_a_reader_hash_policy tests.test_stage2_1_quality`（39 tests）
- PASS：`python -B -m pytest -q tests/test_module_a_v2.py tests/test_gis_data.py tests/test_region_review_packages.py -p no:cacheprovider`（21 passed，23 warnings）
- PASS：`python -B -m unittest discover -s tests/module_b -p "test*.py"`（32 tests）
- PASS：8 个 Stage 3 回归测试
- PASS：冻结 8 题真实 B Solver 重跑，结果/Trace 已写入 `results.json`
- NOT_RUN：真实 C Harness disaster-recovery HTTP 联调；当前长期 Eval 分支未合并同伴 B/C 分支，不能冒充已接通
- NOT_RUN：Flat Tool Agent、Fixed Workflow、Composite Recovery + Harness 的真实 LLM 对照；无 LLM Token/费用成绩

## 当前结论

Eval 侧已经具备真实 B Solver、Verifier、Trace、冻结场景、严格 GT 和独立评分闭环；不能宣称已经完成正式多 Agent 对照评测。进入正式对照前，必须在不改 A v2 的集成工作树中接入 C Harness，验证真实 `/planning/disaster-recovery`、session state、六阶段 `stage_results`、`branch_decisions`、`constraint_checks`、`execution_trace_id` 和东区来源白名单。
