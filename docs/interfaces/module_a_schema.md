# Module A v2 数据接口

## 标识与范围

- 所有区域数据必须包含 `region_id`；`demand_id` 和 `shelter_id` 在区域内唯一，并在 OD 中作为外键。
- 矢量输出使用 EPSG:4326；人口 TIFF 保留源栅格 CRS，并在 `population_stats.json` 记录。
- `data_version` 必须在区域清单、OD、B adapter 和情景中一致。

## 场所

`shelters_all.csv` 保留 Stage 1A 原始字段和清洗字段，不能用演示值覆盖。`shelters.geojson` 是空间/B 适配层，至少包含：`region_id`、`shelter_id`、`shelter_name_candidate`、`address_candidate`、`capacity_persons`、`capacity_persons_original`、`capacity_field_status`、`capacity_source_status`、`verified_status`、`active`、`data_status`、`coordinate_status`。

## 需求与 OD

需求节点至少包含 `demand_id`、`region_id`、`pop_est`、`population_status`。OD 采用区域内需求节点 × 空间场所的完整笛卡尔键，至少包含 `demand_id`、`shelter_id`、`time_min`、`distance_m`、`reachable`、`reason`、`data_version`。不可达记录保留在矩阵中，时间和距离可为空，并用 `reason` 区分 snap 距离过远、无路径等情况。

## 状态

`original`、`modelled_estimate`、`demo_unverified`、`assumed_open`、`demo_analysis_boundary` 和 `DEMO_ONLY` 必须与真实来源、模型估计和人为情景假设区分。当前没有自动确认当前开放状态、容量或行政边界。
