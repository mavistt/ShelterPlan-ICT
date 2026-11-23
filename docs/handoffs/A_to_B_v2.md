# Module A v2 → Module B 交接

本文面向 Module B 开发者。A v2 已为三个区域生成独立数据包；当前均为 `DEMO_ONLY`，工程验收状态为 `PASS_DEMO`，不代表现场核验或官方行政边界验证。

## 1. 区域和版本

| region_id | data_version | 场所 | 需求 GeoJSON/Parquet | OD | adapter |
|---|---|---|---|---|---|
| `east_district` | `shelterplan_module_a_v2.0-demo__east_district` | `data/processed/regions/east_district/shelters.geojson` | `demand_nodes.geojson` / `demand_nodes.parquet` | `travel_matrix.parquet`，1,192,539 行 | `configs/module_a_v2_b/east_district.yaml` |
| `shiqi` | `shelterplan_module_a_v2.0-demo__shiqi` | `data/processed/regions/shiqi/shelters.geojson` | `demand_nodes.geojson` / `demand_nodes.parquet` | `travel_matrix.parquet`，183,400 行 | `configs/module_a_v2_b/shiqi.yaml` |
| `xiaolan` | `shelterplan_module_a_v2.0-demo__xiaolan` | `data/processed/regions/xiaolan/shelters.geojson` | `demand_nodes.geojson` / `demand_nodes.parquet` | `travel_matrix.parquet`，1,872,586 行 | `configs/module_a_v2_b/xiaolan.yaml` |

三个区域的 OD 均由固定的 Geofabrik Guangdong OSM PBF 独立构建；不能跨区域合并 OD、路网、缓存或结果。区域 manifest、quality、adapter、scenario catalog 和 OD 必须使用同一个区域版本。

## 2. 文件和字段契约

### shelters.geojson

EPSG:4326，Point；主键为 `shelter_id`。B 至少读取：

- `shelter_id`：区域内唯一字符串；
- `capacity_persons`：非负的空间/B 有效演示容量；
- `verified_status`：当前为 `demo_unverified`；
- `active`：当前为演示计算开关；
- `region_id`、`data_status`；
- `capacity_persons_original`、`capacity_field_status`、`capacity_source_status`：追溯原始/插补容量。

`current_open_status=assumed_open` 不是当前开放事实。石岐有 1 条容量使用 `demo_imputed`，不能解释成人工核验容量。

### demand_nodes.geojson / demand_nodes.parquet

GeoJSON 为 EPSG:4326 Point；Parquet 为同一需求表的非几何分析版本。主键为 `demand_id`。核心字段：`region_id`、`demand_id`、`row`、`col`、`pop_est`、`cell_area_km2`、`population_year`、`population_source`、`population_status`。`pop_est` 是 WorldPop 模型估计，不是观测人口。

### travel_matrix.parquet

完整 demand × shelter 笛卡尔关系，主键为 `(demand_id, shelter_id)`，不得重复或缺行。字段：

`region_id`、`demand_id`、`shelter_id`、`distance_m`、`travel_time_min`、`time_min`、`reachable`、`reason`、`demand_snap_m`、`shelter_snap_m`、`walking_speed_m_per_s`、`data_version`。

可达行必须为 `reachable=true`、`reason=ok` 且时间/距离非负；不可达行保留在矩阵中，时间/距离为空，`reason` 为 `demand_snap_too_far`、`shelter_snap_too_far` 或 `no_path`。当前 walking speed 为 1.1m/s，snap 规则为 200m。

### manifest 和 quality

每个区域的 `manifest.json` 包含：

- `region_id`、区域 `data_version`、`release_status`；
- `quality_summary`；
- `b_adapter.path`、大小和 SHA-256；
- `files` 中所有区域产物和 B adapter 的相对路径、字节数和 SHA-256；
- `hash_policy`：排除 `manifest.json` 本身和运行结束后才写入的 `run_status.json`，避免自循环。

`quality_summary.json` 顶层包含 `region_id`、`data_version`、`release_status`、`verdict=DEMO_ONLY`、网络/人口/OD统计和阻塞说明。`PASS_DEMO` 只表示工程数据包可用，不表示真实场所核验通过。

## 3. B 读取和验证

B 读取单个 adapter 时应：

1. 先确定一个 `region_id` 和精确 `data_version`；
2. 读取 manifest，验证每个 required artifact 的路径、大小和 SHA-256；
3. 验证 adapter 自身的 manifest hash；
4. 检查场所/需求唯一性和 OD 外键；
5. 检查完整笛卡尔关系、不可达语义和版本一致性；
6. 读取 `data_status=DEMO_ONLY` 并将状态传递到结果。

B 现有 `backend/adapters/a_reader.py` 的 `load_a_bundle(config, repo_root)` 已能完成这些逐行检查，并已在东区实际成功加载 38,469 个需求、31 个场所和 1,192,539 条 OD。B CLI 的 `inspect-data` 当前从嵌套 adapter 路径推断 repo root，会把 `configs/module_a_v2_b/east_district.yaml` 的 root 错判为 `configs/`；B 侧需要增加显式 `--repo-root` 或正确的项目根推断。本次不修改 B 代码。

## 4. B 侧需要配合实现的功能

| 需求 | 建议位置 | 验收标准 |
|---|---|---|
| `region_id` 选择 adapter | `configs/module_b.yaml`、planning service | 任务只能绑定一个区域和版本 |
| 区域级加载和缓存 | adapter/executor | 缓存键含 `region_id` + `data_version` |
| manifest/hash 验证 | `backend/adapters/a_reader.py` | 任一文件改动都返回结构化校验错误 |
| 缺失 OD 处理 | adapter/API | 不可用区域返回明确 `DATA_UNAVAILABLE`，不回退其他区域 |
| provenance | models/API result | 结果保留 `region_id`、`data_version`、`data_status`、manifest hash |
| capacity_expansion 语义 | planner/models | 明确预算、成本、可叠加/互斥和 hypothetical 状态 |
| 大 OD 读取 | adapter | 使用 row-group 流式读取，避免复制整个 Parquet |

A 只提供容量调整参数和来源状态，不在 A 中实现 B 的容量优化算法。

## 5. B 侧验收示例

在能显式传入项目根目录的读取入口中执行：

```python
from pathlib import Path
from backend.adapters.a_reader import load_a_bundle

root = Path("/data2/liyutong/ShelterPlan_DataGIS_Starter")
config = root / "configs/module_a_v2_b/east_district.yaml"
bundle = load_a_bundle(config, root)
assert len(bundle.demands) == 38469
assert len(bundle.shelters) == 31
assert len(bundle.od_edges) == 1192539
assert bundle.manifest.data_version.endswith("__east_district")
```

石岐和小榄的预期分别为 7,336×25 和 60,406×31；所有情景和规划结果仍须保留 `DEMO_ONLY` 声明。
