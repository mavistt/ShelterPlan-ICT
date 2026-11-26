# ShelterPlan-Eval Stage 0/1/1.5/2/2.1 Pilot

本目录是 ShelterPlan-Eval 的独立评测基础设施入口。本轮完成：

1. A v2 数据基线和来源清单；
2. 只读数据质量审计；
3. demo / verified 分离的 Data Admission Gate；
4. Stage 1.5 A v2 Manifest 逐文件校验；
5. Stage 2 HardBench-inspired Scenario Factory Pilot；
6. Stage 3 所需 GT/Oracle 输入契约草案。
7. Stage 2.1 Scenario Factory 质量修复与验收。
8. Stage 3 Pilot Task Factory、GT Assertions、独立枚举 Oracle、Evaluator 和 VETS 评分闭环。
9. A-only difficulty discovery、纯评测器和 compact frozen release package。

本轮明确不做：

- 不生成完整 Benchmark，不凑 144 道题；
- 不覆盖或重建现有 A v2 处理产物；
- 不合并 feature/benchmark-grounded 的历史 merge_data；
- 不修改 Module B Solver、Module C Harness 或 D 前端；
- 不把 DEMO_ONLY、approximate、needs_review 自动升级成 VERIFIED；
- 不调用付费在线服务。
- 不生成完整 144 道任务；Pilot 只生成 30-50 个候选。

## 输入与证据

- 基线分支：feature/shelterplan-eval-v1
- 起始提交：14d686aa0043dad513eb9c165b873f04a6b5e80f
- A v2 区域：shiqi、east_district、xiaolan
- 历史 benchmark：origin/feature/benchmark-grounded，仅作只读方法参考
- 来源清单：sources/source_manifest.json
- 逐场所准入：sources/eligibility.csv
- 审计报告：reports/quality_audit.md
- 准入摘要：reports/admission_summary.json
- Manifest 阻塞报告：reports/preflight_blocking_report.md
- Stage 2 Pilot 摘要：reports/stage2_pilot_summary.md
- Stage 2 候选：benchmark/shelterplan_eval/pilot/candidates.json
- Stage 2 接受集：benchmark/shelterplan_eval/pilot/accepted_scenarios.json
- Stage 2 拒绝日志：benchmark/shelterplan_eval/pilot/reject_log.json
- Stage 2.1 质量审查：reports/stage2_quality_review.md
- Stage 2.1 Pilot：benchmark/shelterplan_eval/pilot_stage2_1
- Stage 2.1 B HTTP 证据：reports/stage2_1_b_http_contract.json

## 准入语义

eligible_for_demo 表示：在明确 DEMO_ONLY 假设、已披露坐标/容量/开放状态限制，并且没有结构性或路网致命错误的前提下，可以用于受控软件演示。

eligible_for_verified 表示：已有独立证据支持身份、字段映射、坐标、容量、开放状态、边界和数据版本。此状态不能由本门禁根据合法经纬度或 Solver 通过自动授予。

active 只表示源记录的 active 字段，不等同于当前开放状态。正常求解候选必须同时满足 region enabled、active 和结构校验；DEMO_ONLY 的坐标、身份、容量、边界和开放状态警告仍然随记录传递。

admission_class 的含义：

- demo_eligible_with_warnings：可用于受控 DEMO_ONLY，但包含需要披露的警告；
- needs_review：源记录保留，但暂不进入正常高可信规划；
- excluded：记录存在明确结构性或空间错误，只从当前准入集合隔离，不删除源数据。

## 运行

~~~powershell
python -B -m sources.preflight
python -B -m sources.admission_gate
python -B -m unittest tests.test_admission_gate
python -B -m unittest tests.test_preflight_manifest tests.test_scenario_factory
python -B -m benchmark.shelterplan_eval.scenario_factory --candidate-count 48 --target-accepted 24 --scenario-seed 20261125
python -B -m unittest tests.test_stage3_pilot
python -B -m benchmark.shelterplan_eval.stage3_pilot --output-dir reports/stage3_pilot
python -B -m unittest tests.test_module_a_release
python -B -m benchmark.shelterplan_eval.release
~~~

sources.preflight 对 A v2 Manifest 做 raw bytes 校验；文本额外在内存中重建 CRLF 并报告 bytes/SHA-256 证据，Parquet/TIFF/PBF/XLSX 等二进制必须严格匹配。失败时返回非零并生成逐文件阻塞证据，不能通过重写 Manifest 消除失败。CRLF 重建吻合是传输格式证据，不是 VERIFIED 证据。

Stage 2.1 Factory 的每个场景绑定 region_id、A v2 data_version、稳定 dataset_content_hash、审计 source Manifest hash、A v2 Manifest hash、完整基础 shelter IDs、独立 improvement_options、参数来源标签和 seed。B 的 12 项上限只应用于 improvement_options。默认严格验证会拒绝 raw Manifest 错误；Pilot 为了保留结构候选，显式标记为 accepted_demo_only_manifest_blocked_crlf，这些场景绝不具备 VERIFIED 或发布资格。design_difficulty 与 measured_difficulty 分离；没有 Solver Trace/独立 Oracle 时 measured_difficulty 为 NOT_MEASURED。

Pilot 场景族覆盖 coverage baseline、budget tradeoff、capacity competition、facility disruption、multi-stage recovery、clarification/safe-stop 六类。所有 reject 都保留在 reject log，不静默丢弃。

## Stage 3 Pilot 实测

Stage 3 默认复用 `pilot_stage2_1/accepted_scenarios.json` 中的 24 个冻结场景，只选取 8 个多样案例进行 Pilot 实测，不重新随机生成场景。任务工厂会把非法 shelter ID、负预算、负步行阈值、历史版本和缺失预算构造成真正送入 B/API 的请求；多轮任务在运行时使用父轮真实返回的 `scenario_id`，不使用虚拟 parent ID。

Stage 3 输出位于 `reports/stage3_pilot/`：

- `tasks.json`：Agent 输入任务和 B 请求契约；
- `gt_assertions.json`：区域、版本、预算、行为、安全和状态继承 GT；
- `results.json`：真实 B Solver/Verifier 结果、Trace、独立 Oracle、VETS 和运行成本；
- `stage3_pilot_report.md`：逐例 PASS/FAIL/NOT_RUN/INFRA_ERROR/UNSCORABLE 及阻塞说明。

石岐使用当前 Module B 配置的 HTTP 路由；东区使用显式 A v2 adapter 调用真实 B Solver，当前 HTTP 路由仍明确标为 UNSUPPORTED，不静默回退石岐。没有 Agent/LLM 输出的 CLARIFY 和 SAFE_STOP 保持 UNSCORABLE；没有独立 Oracle 的案例不会宣称最优已证明。所有真实 GIS 结果仍为 DEMO_ONLY。

## 当前边界


石岐和东区可以用于 DEMO_ONLY 受控演示，但不能描述为现场核验数据。小榄保持 disabled；小榄中一条场所记录的路网吸附距离为 424.726m，超过 200m 门槛，因此该区域暂不进入正常规划准入。所有原始记录、审核备注和异常原因都必须保留。

## A-only handoff

本轮 A 交付的唯一长期工作分支是 `feature/shelterplan-eval`；本节不创建新的 Stage 分支，也不修改、合并或删除队友的 B/C/D 分支。

`python -B -m benchmark.shelterplan_eval.release` 只生成 `reports/final_release/` 下的 A-owned 产物：

- `difficulty_discovery.json/.md`：24 个冻结场景的预算、候选、互斥、空间重叠、可达性、容量上界和结构重复证据；不调用 B/C/LLM。
- `task_dataset.json`：24 个带 user_turns、行为 GT、数据身份、provenance、difficulty evidence 和 split 的冻结任务。
- `evaluation_set.json`：纯评测契约，保留 `U/W/P/E` 维度和 `NOT_RUN/UNSCORABLE/INFRA_ERROR` 规则。
- `release_manifest.json`、`MODULE_A_RELEASE_REPORT.md`：文件哈希、限制、复现命令和明确的 `DEMO_ONLY` 边界。

真实 GIS 场景的 Oracle 状态保持 `NOT_PROVEN`。`oracle.py` 提供不依赖 B 的独立整数最大流和小规模枚举内核，并由单元测试证明；这不等于已经对真实 GIS 组合做了完整最优性证明。`ST21-shiqi-004` 的多阶段字段目前只有结构性证据，没有损坏/禁用输入和运行时父子 Trace，因此不能写成已测得的恢复难度。
