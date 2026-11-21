# 真实 GIS 锚定的困难合成规划实验

本目录保存 Module B 的可复现实验定义、场景叠加、独立 ground truth、运行结果和报告。原始 Module A 文件保持只读；生成数据不得伪装成真实避难场所、工程成本或已核验规划能力。

## 数据来源与限制

- A 数据版本：zhongshan_pilot_v0.1
- A7 / 所有本轮规划结果：DEMO_ONLY
- A 需求节点、场所、OD 时间及路径均从 Module A 原始派生文件读取，不做坐标、属性或关系修改。
- 假设扩容项目使用抽象 cost_units，必须标记 HYPOTHETICAL；不是人民币或工程报价。
- 真实场所、容量、边界与道路数据尚未获得权威现场核验；GT-C 固定为 NOT_VALIDATED。
- source_manifest.json 记录被读取文件的相对路径、SHA-256 和数据版本。情景仅存 A ID 引用和 Overlay，不复制整份 OD 矩阵。

## 可复现性

固定 Python / 依赖版本、seed、生成器版本、source manifest 哈希及每次独立 run ID。任何没有实际运行的项使用 NOT_RUN / N/A；Oracle 未证明时记录 best feasible、bound 和状态，不标 OPTIMAL。

在项目根目录的 PowerShell 中复现情景和实验（每次运行使用新的 run ID）：

```powershell
$env:PYTHONPATH = '.'
$env:PYTHONDONTWRITEBYTECODE = '1'
& '.venv\Scripts\python.exe' 'merge_data\tools\grounded_scenario_generator.py'
& '.venv\Scripts\python.exe' 'merge_data\tools\grounded_experiment_runner.py' --preflight --run-id 'preflight-<unique-id>'
& '.venv\Scripts\python.exe' 'merge_data\tools\grounded_experiment_runner.py' --run-id 'full-<unique-id>'
```

runs/ 下的运行工件可能较大，仅供本地复现，不应提交到 Git；`.gitignore` 已排除整个 runs/ 目录。

## 状态

结构审计、生成器回归、三案例预检和 26 案完整运行已完成。正式运行目录：`runs/full-20261113-main/`；三案例预检目录：`runs/preflight-20261113-final/`。结果摘要见 `runs/full-20261113-main/summary.csv`，逐案 B/Oracle 记录、GT 与 Graph trace 均保存在对应 run 目录。

最终报告位于 `reports/01_REAL_STRUCTURE_AUDIT.md` 至 `reports/07_NEXT_STEP_RECOMMENDATIONS.md`。本批 17 个自然有效预算规划案例均 OPTIMAL；D03 是有意注入的 FEASIBLE 状态测试，D02/D04 是负向验收。没有观察到自然 MIP 超时或非预期 Verifier 失败。Greedy 与独立 Oracle 的差异小于 Oracle 的取整误差界，说明这些抽象预算情景没有充分挑战当前规划算法。

轻量逐案 CSV 汇总：`reports/CASE_SUMMARY.csv`。`source_manifest.json` 还记录了实验脚本、情景清单和 20 个候选 JSON 文件的 SHA-256。

**边界**：A7 仍为 DEMO_ONLY，真实场所/容量/道路和工程成本未核验；源清单的原始人口栅格本地文件是未匹配的 Git LFS 指针；GT-C 保持 NOT_VALIDATED。候选数 16/32 通过 B 内部评估入口测试，公开 TaskRequest schema 仍限制 12 个候选。所有新生成的场景、报告和运行工件都在本目录下；没有改写 A 或旧的 B 输出。
