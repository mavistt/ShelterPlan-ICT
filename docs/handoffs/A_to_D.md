# Module A v2 → Module D 交接

D 使用每个区域的 `layers_manifest.json` 发现图层，不应从文件名猜测区域或 CRS。当前通用图层包括：

- `population_heatmap.geojson` 与 `population_heatmap.tif`：WorldPop 2025 栅格裁剪结果；
- `shelter_capacity.geojson`：场所点与演示有效容量；
- `coverage_10min.geojson`、`coverage_15min.geojson`、`coverage_20min.geojson`、`coverage_30min.geojson`：三个区域均已生成；
- `access_gaps_*.geojson`：网络可达性候选缺口，不等于容量分配后的未服务人口；
- `road_edges.geojson`、`network_buffer.geojson`：固定 Geofabrik Guangdong OSM PBF 生成的网络图层。

图层 CRS 为 EPSG:4326。当前边界文件为 `demo_analysis_boundary` 凸包/缓冲范围，不是官方行政边界；地图必须在界面中显式显示 `PASS_DEMO / DEMO_ONLY` 状态。
