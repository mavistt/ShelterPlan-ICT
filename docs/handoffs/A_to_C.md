# Module A v2 → Module C 交接

本文面向 Module C 开发者。C 不需要自己计算 GIS、OD 或规划结果，只负责区域/情景选择、会话绑定、任务编排和结果解释。

## 1. A 提供的区域信息

区域目录来自 `configs/regions.yaml`，运行时数据来自 `data/processed/regions/{region_id}/manifest.json`、`quality_summary.json`、`layers_manifest.json` 和 `scenario_catalog.json`。

| region_id | data_version | 场所 | 需求节点 | OD | 状态 |
|---|---|---:|---:|---:|---|
| `east_district` | `shelterplan_module_a_v2.0-demo__east_district` | 31 | 38,469 | 1,192,539 | `PASS_DEMO` / `DEMO_ONLY` |
| `shiqi` | `shelterplan_module_a_v2.0-demo__shiqi` | 25 | 7,336 | 183,400 | `PASS_DEMO` / `DEMO_ONLY` |
| `xiaolan` | `shelterplan_module_a_v2.0-demo__xiaolan` | 31 | 60,406 | 1,872,586 | `PASS_DEMO` / `DEMO_ONLY` |

`PASS_DEMO` 是工程数据包状态；`DEMO_ONLY` 是数据真实性状态。三块区域的边界都是 `demo_analysis_boundary`，不是官方行政边界。

## 2. A 提供的图层和情景

通过 `layers_manifest.json` 发现图层，不要由文件名猜测 CRS 或状态。每个区域当前包含：

- 人口热力图和按真实栅格像元面积计算的人口密度 GeoTIFF；
- 避难场所容量分布；
- 10/15/20/30 分钟网络候选覆盖；
- 对应的潜在 access gaps；
- 固定 PBF 生成的道路边图层和 network buffer；
- 边界和分析范围。

`configs/planning_scenarios.yaml` 和每区 `scenario_catalog.json` 提供六类区域情景：`base`、`high_demand`、`strict_access`、`relaxed_access`、`facility_disruption`、`capacity_expansion`。每个区域情景都携带该区域精确 `data_version`、`region_id`、需求比例、步行阈值、关闭场所和容量调整参数。

`capacity_expansion` 目前只是演示参数，标记为 `parameter_only_not_optimization`；成本、预算、优化动作和 hypothetical/human-confirmed 语义由 B 负责。

跨区域比较情景没有单一 `data_version`，而是提供 `region_data_versions` 映射；C 不应把多个区域的 OD 合并成一个规划输入。

## 3. C 需要配合实现的功能

| 能力 | 建议行为 | 验收标准 |
|---|---|---|
| 区域列表 | 暴露区域 ID、名称、版本和状态 | 能查询三块区域及其 `PASS_DEMO/DEMO_ONLY` |
| 图层清单 | 根据 `region_id` 读取 `layers_manifest.json` | 不跨区读取路径，返回图层 CRS/格式/状态 |
| 情景查询 | 根据区域读取 `scenario_catalog.json` | 六类情景的版本必须匹配区域 |
| 会话绑定 | session 创建时绑定 `region_id`、`data_version`、`analysis_mode` | 后续 turn 不得静默换区或换版本 |
| 调用 B | 发送完整区域版本和情景参数 | B 任务不会回退到 `configs/pilot.yaml` |
| 状态提示 | 保留 `DEMO_ONLY`、`PARTIAL`、OD 缺失等状态 | 用户能看到结果不是现场核验建议 |
| 会话隔离 | 结果、缓存和历史记录键包含区域和版本 | 不出现跨区域 shelter/OD 混用 |

当前 C 的 `agent/config.py` 仍从 `configs/pilot.yaml` 获取 v0.1 默认版本，`agent/contracts.py` 的场景值对象还没有 `region_id`。这些是 C 侧配合事项，本次不修改 C 代码。

## 4. 建议接口形态

区域查询：

```http
GET /api/regions
```

返回至少包含：

```json
{
  "region_id": "east_district",
  "data_version": "shelterplan_module_a_v2.0-demo__east_district",
  "release_status": "PASS_DEMO",
  "data_status": "DEMO_ONLY",
  "demand_nodes": 38469,
  "shelters": 31,
  "od_available": true
}
```

图层和情景查询都应要求 `region_id`：

```http
GET /api/regions/east_district/layers
GET /api/regions/east_district/scenarios
```

创建规划会话或调用 B 时，应发送完整场景：

```json
{
  "region_id": "east_district",
  "data_version": "shelterplan_module_a_v2.0-demo__east_district",
  "analysis_mode": "demo",
  "scenario_id": "east_district__strict_access",
  "population_demand_ratio": 0.2,
  "max_walk_time_min": 10,
  "disabled_shelter_ids": []
}
```

## 5. 职责边界

A 负责数据生产、空间分析、版本和质量验证；B 负责规划分配/优化；C 负责 Agent、会话和任务编排。C 不应重新计算 A 的 GIS 指标，也不应绕过 B 自己执行容量分配。D 只消费经 A/B/C 标注过来源和状态的结果。
