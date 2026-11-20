# Module A v2 开发进度

更新时间：2026-11-20（HPC 本地）

当前状态：`PARTIAL`

已完成：

- `east_district` 31 条、`shiqi` 25 条、`xiaolan` 31 条 Stage 1A 记录均进入独立区域包；
- 三个区域均完成场所空间层、WorldPop 2025 裁剪、需求节点、边界元数据和容量/人口图层；
- 东区完成 38,469 个需求节点、31 个场所、1,192,539 条 OD；道路图复用既有 v0.1 产物并明确标注 DEMO_ONLY；
- 生成 `configs/regions.yaml`、`configs/planning_scenarios.yaml`、每区域情景目录、B/C/D 交接文档和 schema；
- 保留原始 `data/processed` v0.1 产物，未调用 Amap/OSM 新 API 以外的写入流程，也未修改 B/C/D 代码。

阻塞：公共 Overpass 多端点在本次运行中持续超时。石岐和小榄没有伪造道路或 OD，故没有 10/15/20/30 分钟覆盖层。三个区域也缺少仓库内可用的官方行政边界，当前边界只用于 DEMO_ONLY 分析范围。

验收入口：`scripts/validate_module_a_release.py`；验证结果写入 `data/processed/module_a_v2_release_validation.json`。
