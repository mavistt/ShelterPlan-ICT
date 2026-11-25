# ShelterPlan-Eval Stage 0/1

本目录是 ShelterPlan-Eval 的独立评测基础设施入口。本轮只完成：

1. A v2 数据基线和来源清单；
2. 只读数据质量审计；
3. demo / verified 分离的 Data Admission Gate；
4. 后续 Scenario Factory、Task Factory、独立 GT/Oracle 和 Evaluator 的契约草案。

本轮明确不做：

- 不生成完整 Benchmark，不凑 144 道题；
- 不覆盖或重建现有 A v2 处理产物；
- 不合并 feature/benchmark-grounded 的历史 merge_data；
- 不修改 Module B Solver、Module C Harness 或 D 前端；
- 不把 DEMO_ONLY、approximate、needs_review 自动升级成 VERIFIED；
- 不调用付费在线服务。

## 输入与证据

- 基线分支：feature/shelterplan-eval-v1
- 起始提交：14d686aa0043dad513eb9c165b873f04a6b5e80f
- A v2 区域：shiqi、east_district、xiaolan
- 历史 benchmark：origin/feature/benchmark-grounded，仅作只读方法参考
- 来源清单：sources/source_manifest.json
- 逐场所准入：sources/eligibility.csv
- 审计报告：reports/quality_audit.md
- 准入摘要：reports/admission_summary.json

## 准入语义

eligible_for_demo 表示：在明确 DEMO_ONLY 假设、已披露坐标/容量/开放状态限制，并且没有结构性或路网致命错误的前提下，可以用于受控软件演示。

eligible_for_verified 表示：已有独立证据支持身份、字段映射、坐标、容量、开放状态、边界和数据版本。此状态不能由本门禁根据合法经纬度或 Solver 通过自动授予。

admission_class 的含义：

- demo_eligible_with_warnings：可用于受控 DEMO_ONLY，但包含需要披露的警告；
- needs_review：源记录保留，但暂不进入正常高可信规划；
- excluded：记录存在明确结构性或空间错误，只从当前准入集合隔离，不删除源数据。

## 运行

~~~powershell
python -B -m sources.preflight
python -B -m sources.admission_gate
python -B -m unittest tests.test_admission_gate
~~~

两个脚本只读取 A v2 输入，写入本目录之外的审计产物，不修改 data/processed/regions 下的旧文件。

## 当前边界


石岐和东区可以用于 DEMO_ONLY 受控演示，但不能描述为现场核验数据。小榄保持 disabled；小榄中一条场所记录的路网吸附距离为 424.726m，超过 200m 门槛，因此该区域暂不进入正常规划准入。所有原始记录、审核备注和异常原因都必须保留。
