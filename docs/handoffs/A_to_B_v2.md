# Module A v2 → Module B 交接

Module A v2 为 B 提供三个独立区域的数据包。B 必须按 `region_id` 选择一个区域，不能把不同区域的 OD 矩阵拼接使用。

## 输入位置

| region_id | shelters | demand | OD | B adapter |
|---|---|---|---|---|
| `east_district` | `data/processed/regions/east_district/shelters.geojson` | `data/processed/regions/east_district/demand_nodes.geojson` | `data/processed/regions/east_district/travel_matrix.parquet` | `configs/module_a_v2_b/east_district.yaml` |
| `shiqi` | `data/processed/regions/shiqi/shelters.geojson` | `data/processed/regions/shiqi/demand_nodes.geojson` | 待网络恢复后生成 | `configs/module_a_v2_b/shiqi.yaml` |
| `xiaolan` | `data/processed/regions/xiaolan/shelters.geojson` | `data/processed/regions/xiaolan/demand_nodes.geojson` | 待网络恢复后生成 | `configs/module_a_v2_b/xiaolan.yaml` |

`data_status` 当前为 `DEMO_ONLY`。东区道路图是保留的 v0.1 图，不代表已针对 v2 分析范围重建；石岐、小榄因公共 Overpass 超时没有生成道路和 OD。

## 关键语义

- `shelters.geojson.capacity_persons` 是供 B 读取的有效演示容量；`capacity_persons_original` 和 `capacity_field_status` 保留原始容量与是否插补的信息。
- 原始登记容量为空时，演示容量可使用区域中位数，但这不是人工核验容量，也不能回写源表。
- `verified_status=demo_unverified`、`active=true` 仅表示本阶段的可计算演示状态，不表示当前开放或官方确认。
- 情景配置在 `configs/planning_scenarios.yaml`，每个情景携带 `region_id`、`data_version`、需求比例、步行阈值、关闭场所和容量调整。

## 使用约束

B 可对已有 OD 做需求比例、时间阈值、关闭场所和容量假设的过滤/重算。改变人口栅格、行政范围、道路网络或坐标后，必须回到 A 重新生成相应数据；不得用另一地区的 OD 代替缺失矩阵。
