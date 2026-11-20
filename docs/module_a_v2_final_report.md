# ShelterPlan Module A v2 阶段报告

## 结论

发布状态：`PASS_DEMO`。三个区域的数据包、固定 PBF 路网、snap、OD、覆盖和缺口图层均已生成并通过工程校验；结果仍是 DEMO_ONLY，不代表现场核验或官方行政边界分析。没有用虚构道路、坐标、容量或边界填补缺口。

## 区域结果

| 区域 | 场所源记录 | 空间场所 | 需求节点 | OD | 状态 |
|---|---:|---:|---:|---:|---|
| 东区街道 | 31 | 31 | 38,469 | 1,192,539 | PASS_DEMO / DEMO_ONLY |
| 石岐街道 | 25 | 25 | 7,336 | 183,400 | PASS_DEMO / DEMO_ONLY |
| 小榄镇 | 31 | 31 | 60,406 | 1,872,586 | PASS_DEMO / DEMO_ONLY |

三个区域均使用 Geofabrik 广东 OSM PBF 固定快照构建独立步行网络；东区不再使用 v0.1 旧图作为本轮主网络。三个区域的边界均为候选点凸包加缓冲的 `demo_analysis_boundary`，不是官方行政边界。

## 数据语义与风险

场所源表和 Stage 1A 字段保存在各区 `shelters_all.csv`；GeoJSON 的 `capacity_persons` 是空间计算用的有效演示容量，并通过 `capacity_persons_original`、`capacity_field_status` 保留原始空值/来源。石岐有 1 条容量缺失记录，使用演示插补但未修改源数据；这不等于人工容量核验。当前坐标为候选地理编码结果，`coordinate_status` 保持 approximate，场所身份和开放状态仍未人工确认。

## 外部依赖

`data/raw/portal_shelters_batch_1.xls`、`portal_shelters_batch_2.xls` 未在仓库中发现，因此不宣称 453 条是完整名录。东区、石岐、小榄官方行政边界文件也未发现。当前道路源为 Geofabrik/OSM PBF，文件哈希和来源已写入网络元数据；下一阶段需要补充可审计的行政边界 GeoJSON/GPKG（含来源、CRS、版本）。

## 后续事项

1. 补齐并核验三个区域官方边界和场所人工确认结果；
2. B/C 负责人完成区域选择、版本绑定、缓存隔离和情景执行语义；
3. 继续优化大 GeoJSON 的前端发布层，但不改变分析源数据；
4. B/C/D 继续只消费各自 `region_id` 的数据包，不合并不同区域 OD。
