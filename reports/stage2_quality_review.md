# ShelterPlan-Eval Stage 2.1 Quality Review

日期：2026-11-25  
分支：`feature/shelterplan-eval-stage2`  
范围：Scenario Factory 质量修复与验收；不进入 Stage 3，不生成 144 道任务。

## 1. Manifest 84 项问题的根因

对石岐、东区、小榄三个 A v2 Manifest 的全部声明文件逐项读取 raw bytes，并对允许的文本文件在内存中重建 UTF-8 CRLF 版本，再比较 expected bytes/SHA-256：

| 分类 | 每区域 | 三个区域合计 |
| --- | ---: | ---: |
| Manifest 文件检查数（含 B adapter） | 36 | 108 |
| `MATCH_RAW` | 8 | 24 |
| `MATCH_RECONSTRUCTED_CRLF` | 28 | 84 |
| `CONTENT_MISMATCH` | 0 | 0 |
| `MISSING` | 0 | 0 |
| `LFS_POINTER` | 0 | 0 |

此前报告中的 84 项失败全部落在“raw bytes 不等于 Manifest、但 CRLF 重建后 bytes 与 SHA-256 完全一致”的类别。它们是跨平台换行传输差异的证据，不是当前文件内容损坏。报告同时保留了每个文件的 raw LF/CRLF/CR 数量、expected/actual/reconstructed bytes 和三组 SHA-256。

因此：

- raw 严格 Manifest 仍未通过：三个区域的 `all_declared_files_match=false`。
- 内容完整性检查通过：三个区域的 `all_content_match=true`。
- 这不能把 A7 `DEMO_ONLY` 升级为 `VERIFIED`。
- 未重写任何 A v2 Manifest expected bytes/hash。

## 2. A Reader 一致性修复

`backend/adapters/a_reader.py` 现在与 preflight 使用同一判定顺序：

1. raw bytes/SHA-256 精确匹配；
2. 对声明的文本文件重建 CRLF 后精确匹配；
3. Parquet、PBF、TIFF、XLSX 等二进制不做换行归一化；
4. 两者都失败才返回 `CONTENT_MISMATCH`。

新增测试证明：真实 `shelters.geojson` 可以通过 `MATCH_RECONSTRUCTED_CRLF`，Parquet 错 hash 仍严格失败。没有修改 B Solver、Verifier 或核心求解逻辑。

## 3. 数据是否被修改

没有修改 `data/processed/regions/**`、原始 A v2 Manifest 或原始数据。新增/更新内容仅为校验代码、稳定身份字段、Scenario Factory、测试、审计报告和 Stage 2.1 Pilot 产物。

`sources/source_manifest.json` 新增 `dataset_content_hash`。它只由固定排序的输入路径、实际 bytes/SHA-256、区域版本、启用状态、行数和准入策略版本计算；`generated_at_utc` 与 git HEAD 保留在 run metadata，不参与数据身份。

## 4. Scenario Factory 业务修复

- `shelter_ids`/`base_shelter_ids`：完整的活动区域 GIS 基础场所环境，不受 12 项限制。
- `improvement_options`：独立的 B API 候选改善集合，最多 12 项。
- `recovery_projects`：与 `improvement_options` 一一绑定，并逐项通过 `ImprovementOption` Pydantic 契约。
- 所有 Pilot 项目都是已有场所的 `capacity_expansion`；没有声称支持 `temporary_site`。
- 每个项目包含 `option_id`、`shelter_id`、`action`、`delta_capacity_persons`、`cost_units`、`eligible`、`source_status`、`hypothetical`、`human_confirmed`、`combination_policy`、`exclusive_group`。
- `budget_tradeoff`、`capacity_competition` 和 `multi_stage_recovery` 产生真实预算不足或互斥条件；预算不再恒能买下全部项目。
- `design_difficulty` 与 `measured_difficulty` 分离。没有 Solver Trace 或独立 Oracle 的场景全部是 `NOT_MEASURED`，不宣称 high 或最优证明。
- 新增不含无意义随机 ID 的 `scenario_family_signature` 与 `semantic_signature`；重复判定使用后者。

## 5. 负例与安全停止

- `invalid_generated_scenario`：生成器自身结构错误，不能进入 Pilot。
- `valid_negative_benchmark`：GIS 与基础场所数据合法，但请求故意使用错误 ID、负步行阈值、负预算或错误版本；保留并设 `expected_behavior=REJECT`。
- 缺少预算等必要参数设 `expected_behavior=CLARIFY`。
- 只有 A v2 空间覆盖证据已经达到用户目标时才设 `SAFE_STOP`；不能因为 family 名称自动设定。
- `rejected_log.json` 与 `unselected_log.json` 分开保存。

## 6. 新 Pilot 对比

旧归档仍保存在 `benchmark/shelterplan_eval/pilot`，未覆盖。新结果写入 `benchmark/shelterplan_eval/pilot_stage2_1`：

| 指标 | Stage 2 旧 Pilot | Stage 2.1 |
| --- | ---: | ---: |
| 候选场景 | 40 | 48 |
| 入选场景 | 24 | 24 |
| 结构有效 | 32 | 48 |
| strict raw Manifest 可用 | 0 | 0 |
| 内容完整性可用 | 未区分 | 48 |
| EXECUTE | 未单独验收 | 13 |
| CLARIFY | 未明确覆盖 | 1 |
| REJECT | 负例未单独归类 | 9 入选；16 个候选含 REJECT |
| SAFE_STOP | 未与 CLARIFY 分开 | 1 |
| 实际执行 | 0 | 1 个真实 B HTTP 成功证据 |
| 独立 Oracle 证明 | 0 | 0 |
| measured difficulty | 固定 family 标签 | 全部 `NOT_MEASURED` |

新 Pilot 的 24 个入选场景覆盖石岐/东区，未使用小榄正常规划；六类任务均有实际语义。所有场景仍为 `DEMO_ONLY`，未标记 `VERIFIED`。

真实 B HTTP 证据保存在 `reports/stage2_1_b_http_contract.json`：石岐 `ST21-shiqi-000` 通过 `/planning/tasks`，HTTP 202，job `SUCCEEDED`，Trace、结果和 `PASS_DEMO` 验证状态均已保存。首次请求因把评测字段 `expected_behavior` 错误放入 B `parameters` 而失败，已移除该字段后重跑成功；这项失败没有被隐藏。

## 7. 测试与验收状态

| 检查 | 状态 | 结果 |
| --- | --- | --- |
| Manifest raw/CRLF/content 分类 | PASS | 108 项逐文件证据；84 CRLF 重建吻合，0 内容不一致 |
| Stage 1.5/2.1 单元与契约测试 | PASS | 31 tests |
| 数据身份稳定性 | PASS | 相同输入重复 build 的 `dataset_content_hash` 一致 |
| Scenario Schema/ImprovementOption | PASS | 生成器与 B Pydantic 契约测试通过 |
| Module A 回归 | PASS | 21 passed，23 个依赖库弃用警告 |
| Module B 回归 | PASS | 32 passed；保留 1 个 Starlette/httpx 弃用警告 |
| 真实 B HTTP EXECUTE | PASS | 1 个石岐案例，`SUCCEEDED` / `PASS_DEMO` |
| 全量 24 个场景真实执行 | NOT_RUN | 不以凑数为目的，尚未进入 Stage 3 |
| 独立 Oracle/GT/VETS | NOT_RUN | Stage 3 前置条件未满足 |
| 真实 LLM 自然语言路径 | NOT_RUN | 本轮不进入 Stage 3 |

## 8. Stage 3 仍需条件

1. 保持 A7/发布状态为 `DEMO_ONLY`，不能把 CRLF 证据当作 VERIFIED。
2. 对需要 raw reproducibility 的发布环境固定 Git checkout/eol 策略，或由 A 重新生成与当前传输格式一致的正式 Manifest；本轮不重写 Manifest。
3. 对选定 EXECUTE 场景补充真实 B Trace，明确哪些是执行结果、哪些仍是设计目标。
4. 由独立 Oracle 生成 GT 并绑定 `region_id`、`data_version`、`dataset_content_hash`、seed；没有 Oracle 时不能写 `ORACLE_PROVEN`。
5. 完成后再评估是否进入 Stage 3；当前结论为：**不能正式进入 Stage 3**。
