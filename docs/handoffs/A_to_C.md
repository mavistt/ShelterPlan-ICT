# Module A v2 → Module C 交接

C 读取 `configs/planning_scenarios.yaml` 选择场景，并从对应 `data/processed/regions/{region_id}/` 读取输入。场景最小接口如下：

```yaml
scenario_id: east_district__strict_access
region_id: east_district
population_demand_ratio: 0.2
max_walk_time_min: 10
closed_shelter_ids: []
capacity_adjustments: {}
data_version: shelterplan_module_a_v2.0-demo
assumption_type: illustrative_scenario
```

需求比例、最大步行时间、关闭场所和容量调整只改变情景假设或筛选时，可复用已有 OD。改变边界、人口栅格、道路网络、场所坐标或新增场所时，需要重新运行 A 的对应步骤。当前所有情景均为演示假设，不是应急管理部门的正式方案。
