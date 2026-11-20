# 上游协作建议单（Module A v2 / PR #1）

- **来源**：Module C 侧联调与源码对照审查（2026-11-21）
- **针对分支**：`feature/module-a-stage1-research` @ `138d06c8`（PR #1，基准 `main` @ `9a2b9279`）
- **原则说明**：本文档仅作为跨模块协作建议提交给 Module A 负责人评估，Module C **未改动**任何 `scripts/`、`configs/` 或 `data/` 文件；C 侧已在自身代码中尽量做了兼容兜底。

---

## 1. [P0] 三区域派生大文件未进 Git LFS，已触发 PR Diff 超时与前端加载风险

### 现状与证据
- `.gitattributes` 当前仅将 `data/raw/*.tif` 与 `data/raw/*.pbf` 纳入 Git LFS；
- PR #1 将三个区域生成的 `map_layers/*.geojson`、`*.graphml`、`*.parquet` 直接作为普通 Git Blob 提交（合计 ~900+ MB，仅 `xiaolan` 约 484 MB、`east_district` 约 352 MB；单文件如 `east_district/map_layers/road_edges.geojson` 达 46.1 MB、`xiaolan/coverage_*.geojson` 单文件 40.9 MB、`xiaolan/travel_matrix.parquet` 42.5 MB）；
- 调用 GitHub API 查询 PR #1 文件列表已返回 `HTTP 422: Sorry, this diff is taking too long to generate`；后续拉取与评审成本极高，且 D 侧浏览器直接加载 40 MB 点 GeoJSON 极易卡死。

### 建议修法
1. 将 `data/processed/regions/**/map_layers/*.geojson`、`data/processed/regions/**/*.graphml`、`data/processed/regions/**/*.parquet` 迁移至 Git LFS（或由可复现流水线按需生成后放发布包，不直接进普通 Git 历史）；
2. 为 Module D 补充轻量化前端发布层（如坐标抽稀/网格聚合/简化路网或 GeoParquet/矢量切片），保留原始分析层用于 B/审计。

---

## 2. [P0] `configs/module_a_v2_b/*.yaml` 缺少 `scenario_defaults` 字段

### 现状与证据
- `configs/module_a_v2_b/{east_district,shiqi,xiaolan}.yaml` 包含 `project`、`paths`、`network`，但未包含 `scenario_defaults`；
- Module B 的在线加载入口 `backend/graph/executor.py:83`（`load_task_source()`）强依赖 `pilot["scenario_defaults"]["population_demand_ratio"]` 和 `["max_walk_time_min"]`（Stage 2 验收脚本直接调用了 `load_a_bundle()`，未经过 `load_task_source()`）；
- 一旦 B 切换配置指向 `configs/module_a_v2_b/*.yaml`，将在运行期抛出 `KeyError` → `DataValidationError`。

### 建议修法
在 `configs/module_a_v2_b/{east_district,shiqi,xiaolan}.yaml` 中补齐与区域基线一致的默认值：

```yaml
scenario_defaults:
  population_demand_ratio: 0.2
  max_walk_time_min: 15
```

（同时建议 Module B 侧对缺省 `scenario_defaults` 增加安全回退。）

---

## 3. [P1] `layers_manifest.json` 漏列 4 个底图图层，`A_to_D.md` 文件名笔误

### 现状与证据
- `docs/handoffs/A_to_C.md` 与 `docs/handoffs/A_to_D.md` 均明确要求下游通过 `layers_manifest.json` 发现图层（包含 `road_edges`、`network_buffer`、`boundary`、`analysis_area`），不得靠文件名猜测；
- 实查 `data/processed/regions/east_district/layers_manifest.json` 仅包含 11 个图层（人口、密度、容量、4 档覆盖、4 档缺口），**遗漏了上述 4 个底图/边界图层**（它们在 `manifest.json` 的 `files` 中存在）；
- `docs/handoffs/A_to_D.md` 文中写 `population_heatmap.tif`，而实际文件和 `layers_manifest.json` 均命名为 `population_density.tif`。

### 建议修法
1. 在 `scripts/module_a_v2_lib.py` 生成 `layers_manifest.json` 时补全 `road_edges`、`network_buffer`、`boundary`、`analysis_area` 四个图层声明及 CRS/状态；
2. 将 `docs/handoffs/A_to_D.md` 中的 `population_heatmap.tif` 更正为 `population_density.tif`。
3. **C 侧已做临时兼容**：`agent/regions.py` 会在读取 `layers_manifest.json` 时自动从 `manifest.json` 补齐这 4 个底图图层，待 A 侧补全后即无缝一致。

---

## 4. [P1] `quality_summary.json` 字段名变更与 B `a_reader.py` 未同步

### 现状与证据
- A v0.1 `data/processed/a7_quality_summary.json` 使用字段名 `real_pilot_blockers`；
- A v2 各区域 `quality_summary.json` 改用了 `blockers`；
- Module B `backend/adapters/a_reader.py:271` 仍只读 `a7_summary.get('real_pilot_blockers', [])`，导致升级后 blocker 数量静默读成 `0`。

### 建议修法
在 `docs/handoffs/A_to_B_v2.md` 和 `docs/interfaces/module_a_schema.md` 中明确 `blockers` 为规范字段（或同时保留兼容别名），并通知 B 侧兼容读取 `blockers or real_pilot_blockers`。

---

## 5. [P1] 演示口径与联调默认区域建议（人口膨胀与 `xiaolan` snap 异常）

### 现状与证据
- 三个区域 `quality_summary.json` 统计的人口合计 **4,496,021**（东区 167.6 万 + 石岐 60.2 万 + 小榄 221.8 万），源于候选场所凸包 + 1 km 缓冲跨越并重叠覆盖周边人口密集区，导致 15 分钟候选可达率偏低（东区 14.17%、小榄 9.93%、石岐 38.49%）；
- 小榄 `travel_matrix.reason_counts` 中 `shelter_snap_too_far` 达 **50,129** 条（说明存在距离步行路网超过 200 m 的场所点）。

### 建议修法
1. 在交接文档与前端展示提示中进一步强调“凸包分析范围之间存在重叠与外溢，人口加和不可等同于行政区人口之和”；
2. 复查小榄 `shelter_snap_audit.csv` 中超 200 m 的场所坐标；
3. 在 A/B/C/D 联调和现场演示阶段，优先推荐 **石岐街道（`shiqi`，7,336 需求点 × 25 场所 = 18.3 万 OD，15 分钟可达率 38.5%）** 作为默认快响应示范区，待 B 性能优化后再全开东区与小榄。
