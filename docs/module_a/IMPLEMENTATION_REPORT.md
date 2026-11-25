# Module A v2 Implementation Report

更新时间：2026-11-20  
分支：`feature/module-a-stage1-research`  
当前发布状态：`PASS_DEMO / DEMO_ONLY`

## 1. 本轮实现

### 数据契约

- `quality_summary.json` 增加区域 `data_version` 和 `release_status`；
- B adapter 在 manifest 写入前生成；
- manifest 记录区域产物和 B adapter 的路径、字节数、SHA-256；
- manifest 明确排除自身和运行结束后写入的 `run_status.json`，避免哈希循环；
- 三个区域的 OD、quality、manifest、B adapter 和 scenario catalog 统一使用区域版本；
- 增加 `demand_nodes.parquet`，与 GeoJSON 需求节点保持同一记录集合。

### 固定道路网络和 OD

固定道路源为 Geofabrik Guangdong OSM PBF：

- 文件：`data/raw/guangdong-latest.osm.pbf`；
- SHA-256：`23e254c3c9e711bf5db6b53d507e23efef9f54deacd90d50fdf6aaba39030fb4`；
- PBF 线路层：`lines`；
- 步行过滤和图构建流程在三个区域统一执行；
- 不再依赖本轮 Overpass 请求；
- 网络元数据记录来源、PBF 哈希、过滤标签、投影 CRS、节点/边和连通分量。

OD 改为按需求节点批次生成并通过 PyArrow ParquetWriter 写入临时文件，完成后原子替换目标文件。不可达关系仍保留，不通过放大 snap 阈值修复。

### 人口密度

人口密度不再把 EPSG:4326 下每个像元固定假设为 0.01 km²。现在按每个纬度行使用 WGS84 Geodesic 计算像元面积，并写入：

- `demand_nodes.geojson.cell_area_km2`；
- `demand_nodes.parquet.cell_area_km2`；
- `map_layers/population_stats.json` 的面积统计；
- `population_density.tif` 和人口热力图的密度字段。

### 文档和交接

- 更新根目录 README 的三区域实际状态；
- 更新 A→B，写明字段、版本、manifest hash、OD 语义、B 侧配合事项和实际读取验收；
- 更新 A→C，写明区域/版本/图层/情景接口、会话绑定和 C 侧配合事项；
- 更新 A→D 的图层名称和固定网络来源；
- 新增本报告，保留 Stage 1 调研报告。

## 2. 实际数据产物

| 区域 | 场所 | 需求 | OD | 网络节点/有向边 | 连通分量 |
|---|---:|---:|---:|---:|---:|
| 东区街道 | 31 | 38,469 | 1,192,539 | 95,398 / 212,128 | 80 |
| 石岐街道 | 25 | 7,336 | 183,400 | 30,703 / 69,732 | 29 |
| 小榄镇 | 31 | 60,406 | 1,872,586 | 82,524 / 182,926 | 109 |

OD 版本分别为：

- `shelterplan_module_a_v2.0-demo__east_district`；
- `shelterplan_module_a_v2.0-demo__shiqi`；
- `shelterplan_module_a_v2.0-demo__xiaolan`。

三个区域的 OD 均无重复组合，外键和版本校验通过。

## 3. 验收结果

```text
Module A / Stage 1A tests: 17 passed
Full test suite: 20 passed
Release validator: PASS_DEMO, failures=0
B load_a_bundle east_district: passed
```

B 的 `load_a_bundle(config, repo_root)` 已实际加载东区 38,469 个需求、31 个场所和 1,192,539 条 OD，并完成 manifest artifact hash 验证。B CLI 直接使用嵌套 adapter 路径时仍存在 repo root 推断问题，已写入 A→B 交接文档，未修改 B 代码。

## 4. 未解决限制

- 三个区域仍没有官方行政边界，当前范围是候选场所凸包加缓冲；
- 场所坐标仍是 approximate，容量和开放状态仍是演示状态；
- 两个门户附件缺失，453 条源记录尚未完成与门户名录的完整对账；
- 网络可达性图层没有做容量分配，容量后的真实未服务人口由 B 负责；
- 当前结果可以作为固定数据源上的软件演示和算法测试，不能作为现场核验的应急规划建议。
