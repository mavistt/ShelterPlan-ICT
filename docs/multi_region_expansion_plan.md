# ShelterPlan Module A 多区域扩展：Stage 0 代码审计与实施规划

审计日期：2026-11-20（Asia/Shanghai）  
仓库：`/data2/liyutong/ShelterPlan_DataGIS_Starter`  
审计范围：只读检查现有代码、配置、原始/中间/处理数据和测试；未调用 Amap/OSM API，未重跑 GIS 流水线，未修改现有脚本或数据。

## 1. 执行结论

当前仓库确实有一套可以复现的东区街道 A1–A7 演示流水线，但它仍是单区域、`DEMO_ONLY` 版本。453 条场所记录覆盖 23 个镇街；从场所字段完整度和可形成演示对比的角度，建议保留东区街道为主试点，暂定选择石岐街道和小榄镇作为两个补充试点。

这个选择是 Stage 0 的数据筛选结论，不是已经完成地理编码、人口代表性或步行网络验证的最终承诺。当前仓库没有石岐、小榄等区域的行政边界、人口裁剪、独立步行网络或 OD 产物，因此下一阶段必须设置“边界、坐标和网络通过后才纳入展示”的门槛。

最重要的限制有四项：

1. `data/raw/zhongshan_shelters.xls` 的 453 行与配置中的门户目录数 906 尚未完成两批附件对账；两批附件本身也不在当前 `data/raw/` 中。
2. 场所表的 `名称`/`具体地址` 字段存在系统性语义反转迹象。清洗脚本把 362 行标为 `likely_swapped`，但这只是启发式分类，不等于人工确认。
3. 当前东区 24 个 GIS 场所全部为 `demo_unverified`，坐标使用近似 GCJ-02 逆转换，容量是 2024 年登记值，没有当前可用性证据。
4. 当前分析范围是围绕候选场所生成的矩形加约 1 km 缓冲，不是权威行政边界；不能据此宣称整个东区或其他镇街的覆盖率。

因此，本文件只给出审计结论、文件级改造建议、数据契约和实施验收标准；不在本阶段实现 `regions.yaml`、新脚本或新的 GIS 产物。

## 2. 当前项目状态

### 2.1 仓库和流水线状态

审计时 Git 工作树为 `main...origin/main`，无未提交变更。实际存在的关键产物如下：

| 层级 | 当前实际状态 |
|---|---|
| 原始场所表 | `data/raw/zhongshan_shelters.xls`，453 行，更新时间字段为 `2024年度` |
| 场所清洗表 | `data/processed/shelters_clean.csv`，453 行，23 个镇街 |
| 东区源记录 | 31 行 |
| 进入当前 GIS 演示的场所 | 24 个，全部东区街道 |
| 已核验场所 | 0 个；24 个均为 `demo_unverified` |
| WorldPop | 2025、100 m；Git LFS 已解析为真实 BigTIFF，不是 LFS 指针文件；约 900,644 KB |
| 当前人口需求节点 | 7,965 个正人口栅格单元；人口估计合计 551,028.703 |
| 当前步行网络 | 9,040 节点、26,822 条有向边、19 个无向连通分量；最大分量占比 0.9912 |
| 当前 OD 矩阵 | 191,160 行；可达 172,704 对 |
| 当前 15 分钟结果 | 3,272 个候选 OD 对；2,366 个需求节点有候选；候选人口占比 39.1%；5,599 个需求节点无候选 |
| 当前 A7 | `DEMO_ONLY`，结构校验通过，但真实试点验收未通过 |

当前矩阵中的 5,599 个“15 分钟无候选”是地理可达性筛选结果，不是容量分配后的未服务人口。它没有考虑避难场所之间的容量竞争，也不能直接证明需要新建设施。

### 2.2 WorldPop 与边界检查

`git lfs ls-files -l` 显示人口 TIFF 已有实际对象哈希，`file` 识别为 BigTIFF；因此没有发现“只下载了 LFS 指针文件”的问题。WorldPop 元数据为 EPSG:4326、`float32`、NoData `-99999`。

当前 `configs/pilot_analysis_area.geojson` 的属性明确写着“场所候选点外包矩形加约1公里缓冲，非行政边界”，范围约为经度 113.3615–113.4494、纬度 22.4875–22.5560。仓库中没有 23 个镇街的权威行政边界层，不能从这一个矩形推导其他镇街边界或人口代表性。

### 2.3 当前产物的证据状态

`data/processed/a7_quality_summary.json` 给出的真实试点阻断项包括：

- 门户目录数 906 与本地 453 行尚未完成完整附件级对账；
- 24 个场所没有 verified 状态；
- 24 个坐标为近似/demo 方法；
- 24 个场所没有合格的 WGS84 方法和坐标来源 URL；
- 24 个容量没有当前核验状态；
- 分析范围不是权威行政边界；
- 2024 登记容量和当前开放状态不能等同。

上述限制应继续写入未来区域 manifest 和 A→B 交接，不得在多区域扩展时被新目录结构掩盖。

## 3. `shelters_clean.csv` 的 453 条记录审计

### 3.1 全局字段质量

清洗表字段和脚本生成逻辑显示：

- 原始 `具体地址` 为空：0 行；原始名称为空：0 行。
- 容量缺失或无效：1 行，位于石岐街道；其他镇街为 0。
- 启发式 `address_candidate` 为空：46 行；其余 407 行有非空候选地址。
- 407 行只是“可进入地理编码候选队列”的记录数，不是 Amap 成功数。本阶段没有调用 Amap，也没有为其他镇街创建地理编码结果。
- `field_semantics_status` 分布：`likely_swapped` 362、`ambiguous_review` 71、`both_address_like_review` 15、`same_text_review` 5。没有任何状态可以替代人工确认。
- 完全重复候选：2 行，即 1 个重复组（源表第 267、275 行）。
- 重复名称候选：8 行，分属 4 个重复组。
- 重复具体地址候选：13 行，分属 5 个重复组。

重复候选均保留在清洗表中，脚本没有自动删除；后续区域审核表必须保留每条源记录的纳入、排除或待核验去向。

### 3.2 按镇街统计

下表中的“候选地址”是现有启发式清洗结果中 `address_candidate` 非空的数量；“未解析”是候选地址为空的数量。`语义状态`是启发式标签，`likely_swapped` 不应直接解释为错误记录。

| 镇街 | 记录数 | 容量缺失 | 候选地址 | 未解析 | 语义状态（数量） | 重复候选行 |
|---|---:|---:|---:|---:|---|---:|
| 三乡镇 | 38 | 0 | 25 | 13 | likely_swapped 24；ambiguous 14 | 名称 2 |
| 三角镇 | 16 | 0 | 16 | 0 | likely_swapped 16 | 0 |
| 东凤镇 | 13 | 0 | 13 | 0 | likely_swapped 13 | 0 |
| 东区街道 | 31 | 0 | 31 | 0 | likely_swapped 31 | 名称 2 |
| 五桂山街道 | 10 | 0 | 9 | 1 | likely_swapped 8；ambiguous 2 | 0 |
| 南区街道 | 17 | 0 | 14 | 3 | likely_swapped 12；ambiguous 5 | 0 |
| 南头镇 | 11 | 0 | 8 | 3 | likely_swapped 7；ambiguous 4 | 0 |
| 古镇镇 | 25 | 0 | 22 | 3 | likely_swapped 21；ambiguous 4 | 0 |
| 坦洲镇 | 21 | 0 | 18 | 3 | likely_swapped 16；ambiguous 5 | 0 |
| 大涌镇 | 15 | 0 | 13 | 2 | likely_swapped 11；ambiguous 4 | 0 |
| 小榄镇 | 31 | 0 | 31 | 0 | likely_swapped 30；both_address_like 1 | 0 |
| 板芙镇 | 18 | 0 | 18 | 0 | likely_swapped 17；ambiguous 1 | 0 |
| 横栏镇 | 10 | 0 | 10 | 0 | likely_swapped 10 | 0 |
| 民众街道 | 23 | 0 | 21 | 2 | both_address_like 14；ambiguous 5；same_text 2；likely_swapped 2 | 名称 2 |
| 沙溪镇 | 32 | 0 | 30 | 2 | likely_swapped 21；ambiguous 9；same_text 2 | 0 |
| 港口镇 | 20 | 0 | 18 | 2 | likely_swapped 18；ambiguous 2 | 地址 11 |
| 火炬开发区 | 11 | 0 | 10 | 1 | likely_swapped 9；same_text 1；ambiguous 1 | 0 |
| 石岐街道 | 25 | 1 | 25 | 0 | likely_swapped 23；ambiguous 2 | 0 |
| 神湾镇 | 9 | 0 | 9 | 0 | likely_swapped 9 | 0 |
| 翠亨新区 | 22 | 0 | 16 | 6 | likely_swapped 15；ambiguous 7 | 0 |
| 西区街道 | 19 | 0 | 16 | 3 | likely_swapped 16；ambiguous 3 | 0 |
| 阜沙镇 | 18 | 0 | 17 | 1 | likely_swapped 17；ambiguous 1 | exact 2；名称 2；地址 2 |
| 黄圃镇 | 18 | 0 | 17 | 1 | likely_swapped 16；ambiguous 2 | 0 |

重复组中最需要人工确认的例子包括：

- 东区第 72、75 行的源名称相同，候选地址相同，但具体地址和电话不同；
- 三乡第 387、408 行的源名称相同；
- 阜沙第 267、275 行既有相同名称又有相同地址，且是唯一被标成完全重复候选的两行；
- 港口有多组重复地址候选，但场所身份可能不同。

这些都应被当作“身份复核候选”，不能只按字符串去重。

### 3.3 东区第 72、75 行的专项结论

现有 `scripts/01_prepare_pilot_demo.py` 在第 26–31 行检查两行候选地址相同，然后直接删除第 75 行；第 47 行还把“第 75 行被假定为同一场所”写入第 72 行的说明。

实际源数据为：

| 源行 | 名称字段 | 具体地址字段 | 候选地址 | 容量 | 电话 |
|---:|---|---|---|---:|---|
| 72 | 中山市东区新村大街8号 | 新鳌岭社区党群服务中心办公楼室内应急避难场所 | 中山市东区新村大街8号 | 20.0 万人单位换算为 20,000 人 | 0760-88311351 |
| 75 | 中山市东区新村大街8号 | 新鳌岭社区新村经联社办公楼室内应急避难场所 | 中山市东区新村大街8号 | 20,000 | 0760-88319684 |

结论：地址候选相同只能证明两条记录可能位于同一地址，不能证明是同一物理避难场所。名称/具体地址字段分别指向不同服务主体，电话也不同；第 75 行应恢复为“待身份核验”，不能在通用多区域流程中复制当前删除规则。东区 v0.1 的 24 个演示点可以保留，但必须在 manifest/报告中继续注明这一未验证假设。

## 4. 推荐的三个试点区域

### 4.1 推荐结果

| 角色 | 区域 | 源记录 | 候选地址非空 | 主要风险 | 比赛演示判断 |
|---|---|---:|---:|---|---|
| 主试点 | 东区街道 | 31 | 31 | 现有 24 点全部未核验；边界为非正式矩形；72/75 身份假设未解决 | 可继续作为现有功能演示，但只能标 `DEMO_ONLY` |
| 补充试点 1 | 石岐街道 | 25 | 25 | 1 条容量缺失；2 条 `ambiguous_review`；尚无坐标、边界、人口和 OSM 验证 | 条件适合；完成容量补录和坐标审核后适合中心城区对比演示 |
| 补充试点 2 | 小榄镇 | 31 | 31 | 30 条由启发式判为 `likely_swapped`，1 条 `both_address_like_review`；尚无区域 GIS 验证 | 条件适合；源记录数量和候选完整度较好，适合作为第二个独立区域演示 |

选择石岐和小榄而不是单纯选择记录数最多的三乡，原因是当前数据筛选阶段石岐、小榄的候选地址完整度更高，且可以形成东区之外的两个不同镇街场景。这里的“空间差异”和“人口代表性”仍是待验证假设，不能由镇街名称或记录数代替。

### 4.2 候选区域比较

- **三乡镇：** 38 条记录最多，但只有 25 条非空候选地址，13 条未解析，14 条 `ambiguous_review`，另有重复名称候选 2 行。它是高数量备选，不是当前最低风险备选。
- **沙溪镇：** 32 条记录、30 条非空候选地址，容量没有缺失，但有 9 条 `ambiguous_review`、2 条 `same_text_review`。可作为第三候选，但必须先做字段语义和身份审核。
- **五桂山街道：** 10 条记录、9 条非空候选地址，数量偏小，适合做小规模对照或边界测试，不适合作为主比赛场景。
- **神湾镇：** 9 条记录、9 条非空候选地址，字段候选较完整但样本量太小，作为主补充试点的展示信息量不足。

### 4.3 还缺什么数据才能把推荐变成最终决定

每个候选区域至少需要：

1. 权威镇街行政边界 GeoJSON/GeoPackage，包含来源、取得日期、版本和 CRS；
2. 区域内及边界外合理缓冲范围内的场所完整清单；
3. 逐条人工确认的场所身份、WGS84 坐标、坐标证据、容量和当前开放状态；
4. 基于同一边界和 WorldPop 栅格的区域人口统计；
5. 固定检索时间、区域边界哈希和 OSM 网络产物，用于比较步行网络规模、连通分量、吸附距离和道路覆盖；
6. 两个门户附件原文件，用 `scripts/12_reconcile_portal_batches.py` 完成 453/906 的来源对账。

如果拿不到行政边界，替代方案只能是定义“示范分析区域”：使用明确记录来源的人工多边形，或围绕区域场所/人口需求范围加缓冲的分析多边形，并在所有地图、指标和 B 交接中标记 `non_administrative_demo_area`。此替代范围不能被表述为镇街全域覆盖率。

## 5. A1–A7 的单区域限制与文件级建议

### 5.1 配置和 A1–A3

| 文件 | 当前问题 | 可复用部分 | 后续必须改造 |
|---|---|---|---|
| `configs/pilot.yaml` | `pilot_town`、所有输入/输出路径、单一 polygon 和单一 manifest；没有 `region_id` | 项目、列名、网络参数、情景默认值可作为全局默认 | 新增 `configs/regions.yaml`；区域配置必须有 `region_id`、镇街、边界来源、审核输入、输出根目录；保留旧文件供东区 v0.1 兼容 |
| `scripts/01_clean_shelters.py` | 清洗逻辑本身不只针对东区，但固定读取 `pilot.yaml` 并写全局 `shelters_clean.csv`/全局审核报告 | 453 行全量清洗、单位换算、重复标记、语义候选生成可复用 | 先保持全市基准表单一来源；区域流程只筛选，不复制或改写原始清洗表；后续增加配置路径和区域筛选元数据 |
| `scripts/01_prepare_pilot_demo.py` | 固定东区审核工作簿、sheet 名、东区过滤；第 72/75 行同地址即删除第 75 行；硬编码东区名称和说明 | “审核表→区域输入”的基本流程可复用 | 改为 `--region-id`；每个区域独立审核表和逐条处置表；同地址只能产生 `duplicate_review`，删除必须有人工身份证据 |
| `scripts/02_geocode_amap.py` | 读取单一 `pilot_town` 和单一 `shelters_pilot_raw`；第 111–126 行硬编码东区第 64、67、82、85、88、89 行排除及原因；候选、排除和缓存均为全局路径 | Amap 返回字段保留、缓存和人工复核接口可复用 | 排除规则移到区域审核数据，不得按源行号全局套用；输出按区域隔离；本阶段不调用 API |
| `scripts/03_prepare_verified_shelters.py` | demo 模式第 32–34 行固定使用经度 113.30–113.50、纬度 22.43–22.57；没有区域级 boundary；第 57–62 行可围绕候选点自动生成矩形 | WGS84 审核门槛、GeoJSON 属性保留、真实模式的 verified/capacity 检查可复用 | 用区域边界/区域缓冲校验代替固定坐标范围；自动矩形只能标 demo；输出必须带 `region_id`、boundary status 和 coordinate evidence |

`scripts/prepare_east_district_review.py` 和 `scripts/09_check_east_review.py` 也是东区专用辅助脚本，后续应改为通用区域审核生成器，或明确保留为 `legacy_east_v0_1`，不能作为新区域流程的入口。

### 5.2 A4–A7、运行入口和测试

| 文件 | 当前限制 | 后续建议 |
|---|---|---|
| `scripts/04_clip_population.py` | 只从 `pilot.yaml` 读一个 raster、一个 polygon、一个输出；报告固定为 `docs/data_quality_population.md` | 逻辑可直接复用；按区域读取边界并写入 `regions/{region_id}/population.tif`、`demand_nodes.geojson` 和区域报告 |
| `scripts/05_build_walk_graph.py` | 单 polygon；OSM 缓存、GraphML、metadata、road layers 均为全局固定路径；实时 Overpass/OSMnx 查询 | 逻辑可复用但必须把 polygon hash、查询时间、网络缓冲和 graph hash 放入区域目录；多区域不得共用未标识的图文件 |
| `scripts/06_build_travel_matrix.py` | 主输入来自配置，但 snap audit、map layers 和矩阵输出仍为全局；矩阵没有 `region_id` | 按区域运行 Dijkstra 逻辑；输出目录隔离，矩阵和所有外键必须绑定同一 `region_id`/manifest；不允许跨区域拼接 |
| `scripts/07_validate_outputs.py` | 固定检查配置的单一三件套，报告固定为 `docs/data_quality_pilot.md` | 变成对任意区域目录的结构校验；增加 region ID、边界 hash、文件版本和矩阵外键隔离断言 |
| `scripts/08_build_manifest.py` | 第 6–39 行是东区/单区域固定文件清单；第 54–60 行是固定代码清单；缺失文件会被静默跳过；manifest 内容没有 region ID | 改为动态区域 manifest；显式记录缺失/可选文件；保留旧 `pilot_manifest.json` 不覆盖，新增 `regions/{region_id}/manifest.json` |
| `scripts/10_a7_quality_audit.py` | 第 106 行变量名和第 122–125 行警告均写死 East District；第 113–132 行按单一东区源表/候选/排除集合做 reconciliation；所有审计输出全局固定 | 抽象为区域审计；逐区域读取 source→review→geocode→GIS 处置；`DEMO_ONLY`/`PASS` 必须以区域为单位输出 |
| `scripts/11_snap_time_sensitivity.py` | 算法可复用，但 snap audit、敏感性 CSV 和报告均为全局固定 | 对每个 region 运行；敏感性结果写入区域目录并含 `region_id`；不修改当前东区基准矩阵 |
| `scripts/run_stage_a_demo.sh` | 无参数，直接重建全局 A4–A7 产物，可能覆盖当前 v0.1；不支持多个区域 | 后续增加 `run_region_pipeline.py --region-id ...`；先做东区 parity，再逐个运行补充区域；现有入口保留为 legacy |
| `tests/test_gis_data.py` | 第 6–9 行固定全局文件；没有 region fixture、边界隔离、manifest 一致性或跨区域污染测试 | 参数化测试区域；测试每个区域的 CRS、ID、容量、矩阵外键、manifest、空/缺文件错误；增加“区域 A 的矩阵不能引用区域 B 的 ID”测试 |

### 5.3 `scripts/10_a7_quality_audit.py` 的结论

A7 的算术和结构检查本身适合抽象，但当前“东区特定”部分必须保留为区域配置驱动：源记录数量、预处理输入数量、held-out 记录、候选地理编码、排除记录和待核验数量都应从该区域的处置表计算。不能简单把 `east_source` 改名为 `region_source` 就完成改造；必须同时移除全局固定的排除 CSV、报告路径和警告文本。

## 6. 多区域数据架构

### 6.1 最小改动方案

建议新增一个配置入口，但在审核通过前不创建它：

```yaml
# configs/regions.yaml（Stage 1 设计草案）
schema_version: 1
base:
  shelter_clean: data/processed/shelters_clean.csv
  population_raster: data/raw/chn_pop_2025_CN_100m_R2025A_v1.tif
  vector_crs: EPSG:4326
regions:
  - region_id: east_district
    town: 东区街道
    role: primary
    boundary_path: configs/regions/east_district.geojson
    boundary_status: official_or_demo_explicit
    review_path: data/interim/regions/east_district/shelter_review.csv
    output_dir: data/processed/regions/east_district
  - region_id: shiqi
    town: 石岐街道
    role: supplementary
    boundary_path: configs/regions/shiqi.geojson
    review_path: data/interim/regions/shiqi/shelter_review.csv
    output_dir: data/processed/regions/shiqi
  - region_id: xiaolan
    town: 小榄镇
    role: supplementary
    boundary_path: configs/regions/xiaolan.geojson
    review_path: data/interim/regions/xiaolan/shelter_review.csv
    output_dir: data/processed/regions/xiaolan
```

区域配置还应包含：边界来源 URL/文件哈希/取得日期、设施搜索缓冲、人口边界、网络缓冲、最大吸附距离、walking speed、数据版本和审核规则文件。区域的“人口统计范围”和“设施搜索范围”可以不同：边界外合理缓冲内的场所可能服务边界内人口，不能在边界线上机械截断。

### 6.2 输出目录和隔离规则

建议使用以下区域级目录，实际字段以 Stage 1 评审为准：

```text
data/processed/regions/{region_id}/
├── shelters.geojson
├── population.tif
├── demand_nodes.geojson
├── road_walk.graphml
├── road_edges.geojson
├── travel_matrix.parquet
├── demand_snap_audit.csv
├── shelter_snap_audit.csv
├── map_layers/
├── quality_summary.json
└── manifest.json
```

`region_id` 必须出现在 manifest、质量报告、场所、需求节点和矩阵元数据中。建议将 `demand_id` 设计为区域命名空间（例如 `east_district__d_0000001`），或者至少在所有读取 API 中强制校验 `region_id` 与 manifest 一致。`shelter_id` 可以继续由全市源行生成，但跨区域读取时必须用区域过滤和 manifest 校验。

当前全局 `data/processed/*` 产物属于东区 v0.1，不能在 Stage 1 直接移动或覆盖。正确顺序是：先在新区域目录中生成东区副本/重构结果，完成数量、ID、矩阵和指标 parity；确认一致后再决定是否把全局路径标成 legacy。

### 6.3 数据版本和来源边界

每个区域 manifest 至少记录：

- `region_id`、`data_version`、生成时间、代码 commit/hash；
- 区域边界文件 hash、边界来源和 `boundary_status`；
- 场所源表、审核表、地理编码结果及排除表的 hash；
- WorldPop 原始 TIFF hash、裁剪参数和人口统计；
- OSM 查询时间、OSMnx 版本、polygon hash、GraphML hash；
- 需求节点数、场所数、矩阵行数、可达对数、snap 统计；
- `PASS`、`DEMO_ONLY` 或 `FAIL` 及阻断原因；
- 哪些字段是真实来源，哪些是模型估计，哪些是假设情景。

## 7. GIS 空间分析图层方案

第一版应优先做基于现有数据能被审计的栅格/人口节点图层，不把它包装成完整等时圈或容量优化结果。

| 图层 | 数据来源 | 计算方式 | 建议输出 | 是否需新增数据 |
|---|---|---|---|---|
| 人口热力图 | 区域 `population.tif`、`demand_nodes.geojson` | 保留 `pop_est`，按固定分级或连续色带渲染；可按区域/网格汇总人口 | 原始/裁剪 GeoTIFF + `population_heatmap.geojson` 或 GeoParquet；附 `population_year`、`population_status=modelled_estimate` | 不需新数据；需明确是 WorldPop 模型估计，不是实际灾害人口 |
| 场所容量分布 | `shelters.geojson`、审核表 | 点符号大小/分级颜色映射 `capacity_persons`，同时显示 `verified_status`、`capacity_verification_status`、开放状态 | `shelter_capacity.geojson` + 图层说明 | 需要逐条容量、身份和开放状态证据才能进入真实结论；当前只能演示 |
| 10/15/20/30 分钟步行覆盖 | `travel_matrix.parquet`、需求节点人口 | 对每个需求节点取可达场所的最小 `time_min`，分别生成阈值内/外分类；保留 snap 和 `reason` | `coverage_10.geojson`、`coverage_15.geojson`、`coverage_20.geojson`、`coverage_30.geojson`，或一个带多阈值字段的 GeoParquet | 不需重新下载人口/路网；每个区域需先生成自己的 OD |
| 地理可达性缺口 | 同上 | 在阈值下没有任何 `reachable=true` 且 `time_min <= threshold` 的需求节点标为缺口，并带 `pop_est`、最近候选时间、不可达原因 | `access_gaps_15.geojson` 等 | 不需新数据，但需要边界、设施缓冲和异常节点复核 |
| 容量分配后的未服务人口 | B 的容量分配/优化结果 + A 的 OD 和人口 | 在给定场所容量、关闭状态、需求比例和分配规则下，计算未分配需求 | `capacity_unserved_{scenario_id}.geojson`/Parquet | 需要 B 输出；不能用 A 的“无 15 分钟候选”替代 |
| 图层元数据 | 所有层、manifest、情景配置 | 说明来源、时间、CRS、字段、阈值、估计/假设状态和质量阻断 | `layers_manifest.json` | 需要新增元数据文件，不需外部数据 |

要明确区分：

- **地理可达性缺口：** 只问“是否存在满足时间阈值的可达场所”，不考虑容量竞争；
- **容量约束下未服务人口：** 需要 B 根据容量和分配/优化结果计算；
- **新增设施潜在改善：** 需要候选公共设施位置及其 OD，且不能把公共建筑自动说成合格避难场所。

当前 OSM 图是在线 OSMnx 查询结果，不是固定 PBF。它可以支持演示型步行网络分析，但必须把查询时间和图 hash 固定到区域 manifest，不能把网络完整性或灾害时可通行性当作已证明事实。

## 8. B 的情景数据接口

### 8.1 建议的统一情景结构

建议在 Stage 4 新增 `configs/planning_scenarios.yaml`，A 只提供数据和接口约定，不能在本阶段修改 B 算法：

```yaml
schema_version: 1
scenarios:
  - scenario_id: east_base_15m
    region_id: east_district
    scenario_type: assumption_based_demo
    population_demand_ratio: 0.20
    max_walk_time_min: 15
    closed_shelter_ids: []
    capacity_adjustments: {}
    source_note: "0.20 is a planning assumption, not an observed disaster rate."

  - scenario_id: xiaolan_disruption_20m
    region_id: xiaolan
    scenario_type: assumption_based_demo
    population_demand_ratio: 0.30
    max_walk_time_min: 20
    closed_shelter_ids: []
    capacity_adjustments: {}
```

`capacity_adjustments` 应明确是场所 ID 到新容量/容量增量的映射，并注明 `assumption`、单位和证据状态。关闭场所只能引用该 `region_id` 的已存在 shelter ID。

### 8.2 哪些情景只需过滤现有 OD

以下变化原则上不需要重新下载人口栅格或重新构建道路网络：

- `population_demand_ratio` 改变：B 对 `pop_est` 乘比例；
- `max_walk_time_min` 改变：B 过滤同一区域矩阵的 `reachable` 和 `time_min`；
- `closed_shelter_ids` 改变：B 排除关闭场所；
- 已有场所容量的明确情景调整：B 在优化/分配模型中使用新的容量，并保留调整前后值。

以下变化需要 A 重新生成或扩展 GIS 数据：

- 增加新的候选设施位置；
- 修改分析边界到完全不同区域；
- 道路封闭、桥梁不可用、步行网络拓扑改变；
- 设施坐标或身份被更正；
- 需要新的空间搜索范围或新的区域级人口栅格。

特别是“新增候选设施”不能仅靠 B 给现有 shelter 扩容来替代。未来若要支持学校、体育馆、社区活动中心等潜在候选点，应由 A 提供位置、来源、资格状态和候选 OD，由 B 决定容量、成本和投资选择；未经核验的公共建筑不得直接标记为可用避难场所。

## 9. 分阶段实施顺序与验收标准

### Stage 0：本次审计与方案冻结（本文件）

**产出：** 本文件；不改脚本、不改现有数据。  
**验收：** 453 条按镇街统计可复核；候选区域有数量、质量和缺口说明；A1–A7 的固定路径/东区逻辑已定位；WorldPop LFS 状态已检查；没有重跑外部网络或 API。

### Stage 1：源数据、边界和区域审核准备

**工作：** 获取两个官方门户附件并完成 453/906 对账；获取或确认三个区域边界；为每个区域生成独立审核表和源记录处置表；确认 72/75 等重复候选的身份规则。  
**验收：** 每条源记录都有 `included`、`excluded` 或 `needs_review` 去向；附件对账结果可复现；每个区域有 boundary source/hash/status；没有虚构坐标、容量或开放状态。

### Stage 2：最小参数化与东区 parity

**工作：** 新增 `regions.yaml` 设计落地；将 A2–A7 的输入、输出、报告、snap audit、manifest 改为 `--region-id` 驱动；保留旧东区 v0.1 产物不覆盖。  
**验收：** 东区新区域目录能独立运行；与现有 v0.1 的场所数、需求节点数、矩阵键集合、关键 reachability 统计相符；跨区域路径不会读取或覆盖另一区域文件；测试覆盖区域隔离。

### Stage 3：石岐和小榄区域数据流水线

**工作：** 在不调用 Amap/OSM 外部 API 的 Stage 0 约束之外，后续按批准的数据获取流程完成坐标、边界和步行网络；一次只跑一个区域；生成独立 A4–A7 产物。  
**验收：** 每个区域通过结构校验和 A7；每个场所的坐标来源、容量证据、开放状态和排除理由可追溯；报告明确 `DEMO_ONLY` 或真实验收状态；网络查询时间和 hash 写入 manifest。

### Stage 4：空间分析图层

**工作：** 先实现人口热力、容量分布、10/15/20/30 分钟基于 OD 的覆盖和地理缺口；再接入 B 的容量分配缺口。  
**验收：** 每个图层可回溯到区域 manifest；覆盖层明确“不含容量分配”；容量未服务层只能引用 B 的分配结果；GeoJSON/GeoParquet/GeoTIFF 的 CRS 和字段说明完整。

### Stage 5：B 情景接口和端到端交接

**工作：** 冻结 `region_id`、scenario schema、shelter ID、矩阵字段和容量调整语义；验证需求比例、时间阈值、关闭场所和容量假设。  
**验收：** B 能在不重建 A GIS 的情况下运行过滤类情景；关闭场所不会出现在分配结果；不同区域 OD 不混淆；假设值和真实来源值在结果中分开标注。

### Stage 6：比赛演示和限制披露

**工作：** 选择通过数据门槛的区域和情景；整理地图、前端层清单、A→B 说明和异常点人工复核。  
**验收：** 演示页面显示区域、数据版本、来源日期、边界性质、坐标/容量核验状态和 `DEMO_ONLY` 限制；不声称当前应急可用容量、完整行政区覆盖率或实时救援路线能力。

## 10. 风险、依赖和需要决策的事项

### 10.1 风险和依赖

| 风险/依赖 | 影响 | 处理建议 |
|---|---|---|
| 453/906 口径未对账 | 无法证明源表完整性 | 先获取两批官方 XLS 并运行已有 reconciliation 脚本 |
| 缺少权威镇街边界 | 人口、场所和覆盖率范围不严谨 | 获取官方边界；否则统一使用非行政示范分析区域表述 |
| 字段语义反转/歧义 | 地理编码可能把名称或地址当错 | 按区域人工确认字段含义，保存原始字段和确认字段，不直接覆盖 |
| 坐标为 demo 近似值 | 路网吸附和 OD 可能受坐标误差影响 | 逐条记录坐标证据、坐标系、方法和核验日期 |
| 容量为 2024 登记值 | 不能代表当前可用容量 | 增加当前容量/开放状态证据；未知就保持 `unknown` |
| OSM 在线查询无固定 PBF | 不同时间重跑可能变化 | 记录查询时间和 hash；需要稳定比较时保存合规快照或固定来源 |
| 边界边缘效应 | 可能人为制造缺口 | 设施搜索范围增加合理缓冲，人口统计范围与设施范围分开 |
| OD 矩阵规模和区域混淆 | B 读取错误数据或内存压力 | 每区域独立目录、manifest 和命名空间 ID；拒绝跨 region join |
| 选中的补充区域尚无 GIS 结果 | 不能提前声称空间差异或网络可用 | 把石岐/小榄标为 provisional，Stage 3 通过后再冻结 |

### 10.2 需要用户确认的事项

1. 是否同意暂定 `east_district + shiqi + xiaolan`，还是优先接受记录数更多但字段风险更高的 `sanxiang` 作为第三候选？
2. 是否能提供两个官方门户附件（906 口径）和三个区域的权威边界文件？
3. 对第 72/75 行，是否要求保留为两条待核验记录，直到拿到场所身份或官方确认，而不是延续当前 demo 删除逻辑？
4. 比赛展示要坚持“非行政示范分析区域”还是必须等待正式边界后再展示区域覆盖指标？
5. 是否接受将所有 2024 容量和未核验坐标继续标为演示假设，直到取得当前容量、开放状态和坐标证据？

## 11. 本阶段实际修改范围

本次 Stage 0 审计未修改 Python、YAML、CSV、GeoJSON、TIFF、Parquet、GraphML 或现有文档；未调用 Amap/OSM；未运行可能覆盖产物的完整流水线；未提交或推送 Git。唯一应新增的交付物是本文件：`docs/multi_region_expansion_plan.md`。
