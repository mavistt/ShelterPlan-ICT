# Module A：数据准备与 GIS 可复现流水线

更新时间：2026-11-11  
项目范围：ShelterPlan Module A

## 1. Module A 做什么

Module A 把避难场所原始数据、人口栅格、分析范围和步行路网整理成可交给 Module B 的结构化输入。

| 阶段 | 任务 | 当前状态 | 主要产物 |
|---|---|---|---|
| A1 | 读取、清洗场所表，统一容量/面积单位 | 已完成当前演示输入 | `data/processed/shelters_clean.csv` |
| A2 | 地址地理编码、坐标来源和人工核验 | 演示完成，真实验收未完成 | `data/processed/shelters_geocoded.geojson`、`data/processed/geocoding_audit.csv` |
| A3 | 定义试点分析范围并声明边界性质 | 演示完成 | `configs/pilot_analysis_area.geojson` |
| A4 | 裁剪人口栅格、生成需求节点 | 已完成演示 | `data/processed/pop_pilot.tif`、`data/processed/demand_nodes.geojson` |
| A5 | 构建步行路网 | 已完成演示 | `data/processed/road_walk.graphml` |
| A6 | 计算人口节点到场所的时间矩阵 | 已完成演示 | `data/processed/travel_matrix.parquet` |
| A7 | 独立检查数据、路网、矩阵和边界影响 | 已完成演示审计 | `data/processed/a7_quality_summary.json`、`docs/data_quality_a7.md` |

## 2. 当前数据到底覆盖什么

当前真实源文件是 `data/raw/zhongshan_shelters.xls`：

| 数据层级 | 数量 | 解释 |
|---|---:|---|
| 中山源表 | 453 | 2024 年下载到的全市原始记录，不等于已核验的当前完整名录 |
| 东区街道源记录 | 31 | 从 453 条记录中按镇区筛出的试点候选 |
| 当前进入 GIS 演示的场所 | 24 | 地址/候选坐标筛选后的演示子集 |
| 已核验场所 | 0 | 当前没有逐条可信 WGS84、容量和开放状态证据 |

因此，“24 个场所”表示当前 GIS 演示使用的子集，不表示中山市只有 24 个场所，也不表示东区街道的完整场所清单已经完成。

当前 A7 判定为：`DEMO_ONLY`。这不是代码失败，而是数据证据门槛尚未满足。

## 3. A 阶段还需要完成什么

要从演示版变成真实试点版，还需要：

1. 对官方目录显示的 906 条记录/附件批次完成完整对账；当前脚本为 `scripts/12_reconcile_portal_batches.py`，附件应放在 `data/raw/portal_shelters_batch_1.xls` 和 `data/raw/portal_shelters_batch_2.xls`。
2. 为进入模型的场所补充可信 WGS84 坐标来源和逐条人工核验状态。
3. 补充当前容量与开放状态证据；2024 年登记容量不能自动当作当前可用容量。
4. 获取权威镇街边界，或始终使用“试点分析范围（非正式行政边界）”的表述。
5. 重新运行 A2–A7 和 manifest，确认新的输入和证据已进入审计结果。

比赛不一定要求把中山市全部 906 条记录都放进最终模型。如果最终定位为“一个镇街试点”，应准备该镇街的完整数据，并明确列出：全市源表数量、试点镇街数量、进入模型数量、排除数量及排除原因。当前 24 条只能作为功能演示子集，不能宣称完整试点覆盖率。

## 4. Module A 的使用方法

在远程服务器执行：

```bash
cd /data2/liyutong/ShelterPlan_DataGIS_Starter
source .venv/bin/activate
```

检查原始 XLS：

```bash
python scripts/00_inspect_shelters.py
```

重新清洗真实源表：

```bash
python scripts/01_clean_shelters.py
```

完整重建演示 GIS 产物：

```bash
bash scripts/run_stage_a_demo.sh
```

该脚本会重建人口裁剪、步行路网、时间矩阵、结构校验、A7 审计、敏感性分析和 manifest；不会调用 Amap，也不会覆盖人工复核工作簿。

## 5. 给 Module B/C/D 负责人的交接

### Module B：规划算法

先看：

- `docs/handoffs/A_to_B.md`
- `docs/data_dictionary.md`
- `configs/pilot.yaml`
- `data/processed/shelters_clean.csv`
- `data/processed/shelters_geocoded.geojson`
- `data/processed/demand_nodes.geojson`
- `data/processed/travel_matrix.parquet`
- `data/processed/a7_quality_summary.json`

使用方式：读取需求节点、场所 ID、容量和时间矩阵，测试容量约束、未服务需求、扩容假设和情景比较。当前输入必须标记为演示数据，不能输出真实东区覆盖率或当前可用容量。

### Module C：AgentArts/MCP

当前 Module A 没有实现 C 模块。C 负责人只需要把 A 的结构化输入和 A7/manifest 作为数据来源，不应把自然语言说明当作新的事实来源。数据来源和限制应引用：

- `data/manifests/pilot_manifest.json`
- `docs/data_quality_a7.md`
- 本文档第 2、3 节

### Module D：Web/API/报告

当前 Module A 没有实现 D 模块。D 负责人需要使用：

- `configs/pilot.yaml` 中的默认参数；
- `data/processed/demand_nodes.geojson` 和 `shelters_geocoded.geojson` 作为地图输入；
- `data/processed/travel_matrix.parquet` 作为时间矩阵输入；
- `docs/data_quality_*.md` 和 `data/manifests/pilot_manifest.json` 作为来源、假设和限制说明。

## 6. 文件结构约定

当前只整理 Module A，不创建 `backend/`、`frontend/`、`agent/` 等其他模块目录。

- `data/raw/`：原始下载文件，只读保存。
- `data/interim/`：人工复核表、候选坐标和中间结果。
- `data/processed/`：可供下游模块读取的标准化产物。
- `configs/`：试点范围和参数。
- `scripts/`：A 阶段检查、清洗、GIS 构建、审计和复现脚本。
- `docs/`：A 阶段质量报告、数据字典和交接说明。
- `tests/`：当前 A 阶段自动化测试。

## 7. 最终状态

| 交付目标 | 状态 |
|---|---|
| Module A 可复现演示流水线 | 已完成 |
| Module A 真实试点验收 | 未完成 |
| Module B/C/D | 不属于当前板块，尚未实现 |
| 当前数据能否用于联调 | 可以，但必须标记 `DEMO_ONLY` |
| 当前数据能否宣称完整真实试点覆盖 | 不可以 |
