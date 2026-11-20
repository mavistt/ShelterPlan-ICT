# Module A v2 Implementation Research

审计日期：2026-11-20  
审计分支：`feature/module-a-stage1-research`  
审计基线：GitHub `main`，commit `9a2b927`  
范围：只审计 Module A 及其 A→B、A→C 接口，不修改 B/C 实现。

## 1. 执行摘要

Module A v2 已经从单区域演示扩展为三个区域的数据目录、人口图层、场所图层、情景目录和质量报告框架，但目前仍是 `DEMO_ONLY / PARTIAL`，不能宣称为真实应急规划数据底座。

当前最重要的三个结论：

1. 三个区域的场所、人口和 ID 关系完整；东区的 OD 形式上是完整笛卡尔关系，但东区 OD 使用的是 v0.1 旧路网，不能代表 v2 分析范围的真实网络覆盖。
2. 东区 `demand_snap_too_far` 的根因不是简单的阈值过小：38,469 个需求点中 28,656 个超过 200m，snap 距离中位数约 4,036m，最大约 20,094m；路网与 v2 需求分析范围存在明显空间不一致，且场所中也有 2 条超过 200m。
3. A→B/A→C 目前是“文档上有接口、运行时尚未打通”：B adapter 要求 `data_version` 和 A7 摘要字段，但当前 v2 `quality_summary.json` 没有 `data_version`；C 仍从 `configs/pilot.yaml` 获取 v0.1 默认版本，也没有完整的 `region_id` 数据选择链路。

## 2. 当前架构和数据流程

### 2.1 A v2 现有流程

当前主入口为：

- `scripts/run_module_a.py`：多区域编排，调用区域运行器和情景生成器；
- `scripts/run_region_pipeline.py`：单区域命令行入口；
- `scripts/module_a_v2_lib.py`：场所整理、候选坐标、边界/分析范围、WorldPop 裁剪、网络、OD、空间图层、质量报告和 manifest；
- `scripts/generate_scenarios.py`：生成区域情景目录和统一情景 YAML；
- `scripts/validate_module_a_release.py`：校验三块区域的记录、外键、OD、图层、情景和发布状态。

配置入口为 `configs/regions.yaml`，区域输出位于 `data/processed/regions/{region_id}/`。每个区域当前包含场所、人口、需求节点、边界/分析范围、图层清单、质量报告、manifest 和情景目录；只有东区有 `road_walk.graphml` 和 `travel_matrix.parquet`。

### 2.2 责任边界

- A：生产和验证 GIS 数据、OD、空间分析图层及数据状态。
- B：读取 A 数据，执行容量约束分配、优化、诊断和压力测试。
- C：管理自然语言、会话和任务编排，向 B 传递完整场景参数。
- D：展示区域、图层、情景和规划结果。

本次审计没有修改 B/C/D 的代码、配置或测试。

## 3. 三个区域实际数据状态

| region_id | 场所源记录 | 空间场所 | 需求节点 | 人口总量 | OD | 当前状态 |
|---|---:|---:|---:|---:|---:|---|
| `east_district` | 31 | 31 | 38,469 | 1,675,977.06 | 1,192,539 | `DEMO_ONLY` |
| `shiqi` | 25 | 25 | 7,336 | 601,716.81 | 未生成 | `PARTIAL` |
| `xiaolan` | 31 | 31 | 60,406 | 2,218,327.00 | 未生成 | `PARTIAL` |
| 合计 | 87 | 87 | 106,211 | 4,496,020.87 | 1,192,539 | `PARTIAL` |

Stage 1A `source_disposition.csv` 中三个目标区域为 87 条，按区域为 31/25/31，`shelter_id` 唯一，且 87 条仍全部是 `needs_review`。没有发现因 Module A v2 处理而造成的区域串行或场所 ID 重复。

## 4. 数据质量审计

### 4.1 人口栅格

- 文件：`data/raw/chn_pop_2025_CN_100m_R2025A_v1.tif`。
- 文件类型：本地为真实 BigTIFF，不是 Git LFS pointer；`git lfs ls-files` 显示对象已获取，LFS 状态干净。
- CRS：EPSG:4326。
- NoData：`-99999`。
- 来源标签：WorldPop Global2 R2025A v1，100m；需求字段标记为 `modelled_estimate`。
- 三个区域的裁剪栅格和需求节点人口汇总均可对账；栅格读取的浮点求和与节点 GeoJSON 汇总只有栅格精度级别的舍入差异，JSON 统计值能够对齐。

人口数据本身目前不是主要阻塞项，但正式发布仍需在报告中保留模型估计声明，不得解释成现场人口普查。

### 4.2 边界和分析范围

三个区域的 `boundary_metadata.json` 都显示：

- `boundary_status=demo_analysis_boundary`；
- `source_url` 为空；
- 几何是候选场所凸包加缓冲，不是官方行政边界。

因此当前分析范围不能用于行政区统计或对外宣称的精确覆盖率。正式修复需要带来源、版本和 CRS 的东区、石岐、小榄行政边界 GeoJSON/GPKG；在边界缺失期间只能继续明确标记 DEMO_ONLY。

### 4.3 场所坐标、容量和开放状态

三个区域空间场所均为 EPSG:4326，所有当前空间记录的 `coordinate_status=approximate`，`verified_status=demo_unverified`，`current_open_status=assumed_open`。

- 东区：31 条容量均为原始登记容量。
- 石岐：24 条原始容量，1 条使用 `demo_imputed`；源表空容量没有被回写。
- 小榄：31 条原始容量；Stage 1A 的 `likely_swapped`/`both_address_like_review` 仍只能视为人工审核提示。

因此 B 可以读取 GeoJSON 的有效演示容量进行算法测试，但不能把它解释成当前可用容量；容量、身份、坐标和开放状态仍需要人工确认。

### 4.4 原始数据和门户对账

当前仓库存在 `data/raw/zhongshan_shelters.xls` 和清洗后的 453 条全市记录，但没有：

- `data/raw/portal_shelters_batch_1.xls`；
- `data/raw/portal_shelters_batch_2.xls`。

因此不能宣称已经完成 453 条与门户 906 条记录的完整对账。当前所有重复候选仍应作为身份核验候选，不能自动去重。

## 5. 道路网络和东区 snap 问题

### 5.1 网络来源

`build_network()` 通过 OSMnx 实时调用三个公共 Overpass endpoint，使用 `walk` 网络、分析范围外扩 1,000m、`max_snap_distance_m=200`。代码没有固定 OSM PBF 快照，因此实时网络结果不能完全复现。

本轮运行中公共 Overpass 多端点持续超时：

- 东区复用 `data/processed/road_walk.graphml` 和旧图层作为明确记录的 v0.1 fallback；
- 石岐和小榄没有伪造网络，保留 `BLOCKED_EXTERNAL_OVERPASS`，因此没有生成 OD。

### 5.2 东区当前网络和 OD

东区 fallback 图：9,040 个节点、26,822 条有向边、19 个连通分量，最大连通分量占约 99.1%。OD 形式上满足：

- 38,469 × 31 = 1,192,539 行；
- OD 组合无重复；
- `demand_id`、`shelter_id` 外键可回到本区域数据；
- walking speed 全部为 1.1m/s；
- `data_version` 为 `shelterplan_module_a_v2.0-demo__east_district`。

但原因分布暴露了空间质量问题：

| reason | 行数 |
|---|---:|
| `demand_snap_too_far` | 888,336 |
| `ok` | 281,851 |
| `shelter_snap_too_far` | 19,626 |
| `no_path` | 2,726 |

### 5.3 根因判断

需求 snap 审计：

- 需求点数：38,469；
- 超过 200m：28,656，约 74.5%；
- 中位 snap 距离：约 4,036m；
- 最大 snap 距离：约 20,094m。

场所 snap 审计：

- 场所数：31；
- 超过 200m：2 条；
- 最大 snap 距离：约 18,116m。

这不是把阈值从 200m 调大就能合理修复的问题。主要证据指向：v2 通过候选点凸包加缓冲形成了新的分析范围和需求网格，但东区 OD 复用了面向旧 v0.1 试点的路网；此外，候选坐标是近似地理编码结果，部分场所与路网也可能存在坐标/道路覆盖偏差。调大 snap 阈值会把本来没有合理步行连接的需求点强行接入，掩盖数据问题，不应作为首选修复。

### 5.4 OD 实现和内存风险

`build_od()` 先计算各场所的 Dijkstra 路径长度，然后用 Python `records` 列表逐一构造 demand × shelter 的所有字典，最后一次性转换为 pandas DataFrame 并写 Parquet。

当前东区 1,192,539 行已经产生较大的中间对象；小榄预计为 60,406 × 31 = 1,872,586 行。继续使用 Python 字典列表会同时保留中间记录、DataFrame 和序列化缓冲，存在明显的内存峰值风险。下一版应采用分批生成、分批写入 Parquet，并在每批完成后释放临时对象；B adapter 已经采用 PyArrow row-group 流式读取，可作为下游读取模式参考。

## 6. OD、版本和空间图层逻辑

### 6.1 当前正确部分

- OD 是完整 demand × shelter 关系，未通过删除不可达记录来掩盖问题。
- 不可达记录保留 `reason`，时间和距离为空；可达记录使用 `reason=ok`。
- 覆盖图层明确标注为 network candidate reachability，不等于容量分配后的服务人口。
- `access_gaps_*.geojson` 是没有候选场所达到时间阈值的需求点，不等于考虑容量后的未服务人口。

### 6.2 当前版本不一致

存在三层版本表达：

1. `configs/regions.yaml` 的基础版本：`shelterplan_module_a_v2.0-demo`；
2. 区域 manifest、B adapter 和东区 OD：基础版本加 `__{region_id}`；
3. `configs/planning_scenarios.yaml` 的统一情景：目前主要记录基础版本，没有把每个区域的独立版本绑定为强制字段。

区域隔离目前主要靠路径和 ID 实现，尚未形成单一、强制、可由 B/C 共同验证的版本契约。

### 6.3 Manifest 缺口

`write_quality_and_manifest()` 当前先扫描区域目录生成 `files`，再写 B adapter YAML。因此：

- manifest 没有记录 B adapter 自身的路径、大小和 SHA-256；
- B adapter 没有保存对应 manifest 哈希；
- `quality_summary.json` 顶层没有 `data_version`，虽然包含 `region_id`、状态、人口、网络和 OD 统计；
- manifest 自身不能简单把自己加入自己的哈希列表，否则会形成循环，应该采用“manifest 内容哈希单独字段”或排除自身的清晰规则。

## 7. A→B 现有接口审计

### 7.1 B 当前读取能力

Module B `backend/adapters/a_reader.py` 已具备相当严格的只读校验：

- GeoJSON 必须是 EPSG:4326 Point；
- 场所和需求 ID 必须唯一；
- OD 必须有完整笛卡尔行数、无重复键、无悬空外键；
- `reachable`、`reason`、空时间/距离和 snap 距离会被逐行检查；
- OD `data_version` 必须等于 B config 的 `project.data_version`；
- manifest 中列出的文件会逐个检查大小和 SHA-256。

### 7.2 当前实际阻塞

现有 `configs/module_a_v2_b/{region_id}.yaml` 将 `a7_quality_summary` 指向 `quality_summary.json`，但 B reader 会要求该摘要存在并且：

- `data_version` 等于 adapter 配置版本；
- `verdict` 为 `DEMO_ONLY` 或 `VERIFIED`。

当前 `quality_summary.json` 没有顶层 `data_version`，因此按实际 B reader 运行，A v2 adapter 不能直接通过完整读取校验。这个问题应优先修复 A 的质量摘要/adapter 契约，不能靠修改 B reader 绕过。

此外，`configs/module_b.yaml` 的默认 `source_config` 仍然是 `configs/pilot.yaml`，没有 region-aware 选择器。B 需要后续支持：区域列表、每区域 adapter、版本绑定、缓存隔离以及没有 OD 时的明确不可用错误。

## 8. A→C 现有接口审计

Module C `agent/config.py` 的 `defaults_from_pilot_config()` 默认读取 `configs/pilot.yaml`，fallback 版本为 `zhongshan_pilot_v0.1`。`agent/contracts.py` 的 `ScenarioParams` 包含：

- `population_demand_ratio`；
- `max_walk_time_min`；
- `budget_units`；
- `disabled_shelter_ids`；
- `data_version`；
- `analysis_mode`。

但 C 当前场景值对象没有 `region_id` 字段，`SCENARIO_CHANGE_KEYS` 也没有区域切换或容量调整字段。因此 A v2 的区域目录和 19 个情景不能直接由 C 选择并安全传递给 B。C 需要后续增加区域查询/绑定、区域版本绑定、会话隔离、图层清单读取和对 PARTIAL 区域的明确提示；本次不修改 C 实现。

## 9. 修复优先级

### P0：接口可用性和版本契约

1. 修复 A v2 `quality_summary.json`、manifest 和 B adapter 的 data_version/状态契约。
2. 让 manifest 在不自循环的前提下包含 B adapter 及所有关键产物的 SHA-256。
3. 用 B 现有 `a_reader.py` 实际执行三个 adapter 验收：东区应能加载并明确 DEMO_ONLY；石岐/小榄应返回结构化 OD 不可用，而不是路径缺失或版本错误。
4. 给情景目录增加强制 `region_id` 和区域 data_version，明确 `capacity_expansion` 只是参数，不在 A 中实现 B 的优化。

### P1：道路、snap 和 OD 稳定性

1. 获取可审计、获许可的静态 OSM PBF 或等价道路快照；记录下载来源、日期、哈希、CRS 和 network_type。
2. 用同一固定网络流程分别重建三个区域；东区不能继续把 v0.1 图当作 v2 网络。
3. 在网络重建后重新审计需求/场所 snap 距离、连通分量、无路径原因；不通过放大阈值掩盖异常。
4. 将 OD 改为 PyArrow 分批写入，控制小榄约 1.87m 行的内存峰值。

### P2：边界和空间分析

1. 补齐三个区域官方边界，保留来源、版本和 CRS。
2. 用固定边界重新裁剪人口和需求节点。
3. 三个区域都生成覆盖/缺口图层后，再补容量分配后的未服务人口图层；继续区分网络可达性缺口和容量分配缺口。
4. 对大 GeoJSON 增加 GeoParquet/简化图层或前端友好的发布层，原始审计层不覆盖。

## 10. 最终希望实现的 Module A 结构

```text
configs/
  regions.yaml                 # 区域、边界、网络和版本配置
  planning_scenarios.yaml      # 带 region_id/data_version 的情景目录
  module_a_v2_b/{region}.yaml  # B adapter，含 manifest hash

data/processed/regions/{region_id}/
  boundary.geojson
  boundary_metadata.json
  shelters_all.csv             # 原始/清洗/审核可追溯层
  shelters.geojson             # 空间/B 规范化层
  population.tif
  demand_nodes.geojson         # 或明确版本化的 Parquet
  road_walk.graphml
  road_graph_metadata.json
  travel_matrix.parquet        # 分批写入，完整笛卡尔键
  map_layers/
  quality_summary.json
  manifest.json
  scenario_catalog.json

docs/module_a/
  IMPLEMENTATION_RESEARCH.md
  IMPLEMENTATION_REPORT.md
```

每个区域都必须能够独立通过：源记录完整性、ID/外键、CRS、人口汇总、网络和 snap、OD 完整性、版本一致性、manifest 哈希和 DEMO_ONLY/PARTIAL 状态验收。

## 11. 需要 B、C 配合的事项

### B

- 接受 `region_id` 和区域 adapter，而不是固定 `configs/pilot.yaml`；
- 每次规划任务绑定一个区域和精确 `data_version`；
- 缓存键包含区域和版本，禁止跨区域复用；
- 对没有 OD 的区域返回明确的数据不可用状态；
- 结果保留 `region_id`、`data_version`、`data_status` 和来源 manifest hash；
- 明确 `capacity_expansion` 的 B 侧成本、预算、可叠加/互斥和优化语义；
- 使用 A reader 的流式 Parquet 检查，避免小榄矩阵造成内存峰值。

### C

- 提供区域列表、区域状态和图层清单查询；
- 创建会话时绑定 `region_id`、区域 `data_version` 和 `analysis_mode`；
- 调用 B 时发送完整场景，而不是只发送变化量；
- 会话、历史结果和缓存不得跨区域混用；
- 对 `DEMO_ONLY`、`PARTIAL` 和 OD 缺失给出用户可理解的提示；
- 将区域、版本和数据状态保留在 API 结果中，供 D 展示。

## 12. Stage 1 审计验收

本次 Stage 1 只完成审计和研究报告，未修改 Module A、B、C 的代码、测试或数据产物。审计报告中的数量均由 HPC 当前文件重新读取得到；现有测试基线此前为全量 18 passed，但发布校验保持 `PARTIAL`，因为石岐和小榄尚无网络和 OD。

下一阶段应依据本报告先修复 A→B manifest/data_version 契约，再准备固定道路数据和分批 OD 方案；不应先通过放大 snap 阈值或伪造边界来制造 `PASS`。
