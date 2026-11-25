# ShelterPlan-Eval Stage 0/1/1.5/2/2.1 Pilot

本目录是 ShelterPlan-Eval 的独立评测基础设施入口。本轮完成：

1. A v2 数据基线和来源清单；
2. 只读数据质量审计；
3. demo / verified 分离的 Data Admission Gate；
4. Stage 1.5 A v2 Manifest 逐文件校验；
5. Stage 2 HardBench-inspired Scenario Factory Pilot；
6. Stage 3 所需 GT/Oracle 输入契约草案。
7. Stage 2.1 Scenario Factory 质量修复与验收。

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
~~~

sources.preflight 对 A v2 Manifest 做 raw bytes 校验；文本额外在内存中重建 CRLF 并报告 bytes/SHA-256 证据，Parquet/TIFF/PBF/XLSX 等二进制必须严格匹配。失败时返回非零并生成逐文件阻塞证据，不能通过重写 Manifest 消除失败。CRLF 重建吻合是传输格式证据，不是 VERIFIED 证据。

Stage 2.1 Factory 的每个场景绑定 region_id、A v2 data_version、稳定 dataset_content_hash、审计 source Manifest hash、A v2 Manifest hash、完整基础 shelter IDs、独立 improvement_options、参数来源标签和 seed。B 的 12 项上限只应用于 improvement_options。默认严格验证会拒绝 raw Manifest 错误；Pilot 为了保留结构候选，显式标记为 accepted_demo_only_manifest_blocked_crlf，这些场景绝不具备 VERIFIED 或发布资格。design_difficulty 与 measured_difficulty 分离；没有 Solver Trace/独立 Oracle 时 measured_difficulty 为 NOT_MEASURED。

Pilot 场景族覆盖 coverage baseline、budget tradeoff、capacity competition、facility disruption、multi-stage recovery、clarification/safe-stop 六类。所有 reject 都保留在 reject log，不静默丢弃。

## 当前边界


石岐和东区可以用于 DEMO_ONLY 受控演示，但不能描述为现场核验数据。小榄保持 disabled；小榄中一条场所记录的路网吸附距离为 424.726m，超过 200m 门槛，因此该区域暂不进入正常规划准入。所有原始记录、审核备注和异常原因都必须保留。
