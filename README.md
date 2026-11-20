# ShelterPlan-ICT

面向城市应急避难场所规划的多模块项目，以中山市为示例，整合 GIS 空间数据、容量约束规划优化和自然语言 Agent 交互。

> **当前状态：PASS_DEMO / DEMO_ONLY。** Module A v2 已完成三个区域的数据准备、固定 PBF 步行网络、OD 和空间分析；Module B、C 已在独立功能分支开发，但尚未完成与 A v2 的端到端集成。官方行政边界、门户附件和场所人工核验仍缺失。所有结果仅用于软件演示与算法测试，不代表经过现场核验的应急规划建议。

## 1. 项目架构

ShelterPlan 由四个相互协作的模块组成。

| 模块 | 主要职责 | 代码位置 |
|---|---|---|
| A — GIS & Spatial Analysis | 场所数据、人口、道路网络、OD、空间覆盖和规划情景 | main |
| B — Planning Engine | 避难需求分配、容量约束、扩容优化、压力测试和方案比较 | feature/module-b |
| C — Agent Orchestration | 自然语言理解、参数澄清、会话管理、MCP、API 和结果解释 | feature/module-c |
| D — Frontend | 地图展示、图层切换、规划交互和结果可视化 | 前端交接文档 |

### System Architecture

~~~mermaid
flowchart TD
    U["User / Planner"] --> D["D: Frontend"]
    D --> C["C: Agent / API / MCP"]
    C --> B["B: Planning Engine"]
    A["A: GIS Data & Spatial Analysis"] --> B
    A --> D
    B --> C
    C --> D
~~~

模块职责：

- A 提供空间数据和空间分析结果。
- B 负责真正的规划计算与优化。
- C 理解用户意图，调用 B 并解释结果。
- D 展示地图、场所、空间分析和规划结果。

## 2. Module A — GIS Data & Spatial Analysis

### 2.1 Multi-region Coverage

目前 A v2 选择三个中山市区域：

| 区域 | 场所记录 | 人口需求节点 | OD 矩阵 |
|---|---:|---:|---:|
| 东区街道 | 31 | 38,469 | 1,192,539 |
| 石岐街道 | 25 | 7,336 | 183,400 |
| 小榄镇 | 31 | 60,406 | 1,872,586 |
| Total | 87 | 106,211 | 3,248,525 |

人口数据来自 WorldPop 2025 100m 栅格。

### 2.2 Spatial Analysis Layers

目前规划并实现了以下空间图层：

1. Population Heatmap — 人口热力图。
2. Shelter Capacity — 避难场所容量分布。
3. Walking Accessibility — 10/15/20/30 分钟步行覆盖。
4. Access Gaps — 潜在服务缺口。

当前完成状态：

| 图层 | 东区 | 石岐 | 小榄 |
|---|---|---|---|
| 人口热力图 | 已完成 | 已完成 | 已完成 |
| 场所容量分布 | 已完成 | 已完成 | 已完成 |
| 步行覆盖 | 已完成 | 已完成 | 已完成 |
| 服务缺口 | 已完成 | 已完成 | 已完成 |

覆盖和缺口目前基于人口网格点及步行网络进行分类，不等同于完整等时圈多边形，也没有考虑场所容量分配。

### Spatial Analysis Workflow

~~~mermaid
flowchart TD
    P["WorldPop Raster"] --> D["Demand Nodes"]
    S["Shelter Candidates"] --> C["Capacity Layer"]
    R["OSM Walking Network"] --> O["OD Matrix"]
    D --> O
    O --> V["Accessibility Coverage"]
    O --> G["Potential Access Gaps"]
    P --> H["Population Heatmap"]
~~~

### 2.3 Planning Scenarios

当前生成 19 个配置情景：

- Base
- High Demand
- Strict Access
- Relaxed Access
- Facility Disruption
- Capacity Expansion
- Cross-region Comparison

情景参数包括人口需求比例、最大步行时间、关闭场所和容量调整。

这些参数是演示假设，不代表实际灾害预测。

### 2.4 Module A Data Structure

~~~text
data/processed/regions/
├── east_district/
├── shiqi/
└── xiaolan/

configs/
├── regions.yaml
├── planning_scenarios.yaml
└── module_a_v2_b/

scripts/
├── run_module_a.py
├── run_region_pipeline.py
├── module_a_v2_lib.py
├── spatial_analysis.py
└── validate_module_a_release.py
~~~

每个区域独立保存：

- shelters.geojson
- demand_nodes.geojson
- demand_nodes.parquet
- population.tif
- road_walk.graphml（网络生成成功时）
- travel_matrix.parquet
- map_layers/
- manifest.json
- quality_summary.json
- scenario_catalog.json

## 3. Module B — Planning Engine

Branch: [feature/module-b](https://github.com/mavistt/ShelterPlan-ICT/tree/feature/module-b)

主要能力：

- Capacity-aware Assignment
- Shelter Expansion Optimization
- Unserved Demand Diagnosis
- Disruption Stress Testing
- Scenario Comparison
- Sensitivity Analysis
- Independent Verification
- FastAPI Planning Service

B 已有 40 个确定性合成 Benchmark 案例。

当前限制：

- 默认数据配置仍使用 A v0.1。
- A v2 的三个区域 adapter 尚未完成读取验收。
- 合成 Benchmark 不等于真实场景验证。

## 4. Module C — Agent Orchestration

Branch: [feature/module-c](https://github.com/mavistt/ShelterPlan-ICT/tree/feature/module-c)

主要能力：

- Natural Language Intent Recognition
- Parameter Extraction
- Multi-turn Dialogue
- Session State Management
- Clarification and Parameter Inheritance
- Guard and Policy Enforcement
- MCP Tools
- HTTP API
- Trace and Evaluation

C 的职责是将用户自然语言转化为受约束的规划请求，再调用 B。

AgentArts 云端部署和真实联调尚未完成。

## 5. Module D — Frontend

D 的目标展示内容：

- 多区域地图切换；
- 避难场所位置和容量；
- 人口热力图；
- 道路网络；
- 不同步行时间下的服务覆盖；
- 潜在服务缺口；
- B 优化前后的方案对比；
- C 自然语言交互。

目前仓库提供 A→D 和 C→D 的交接说明，前端实现不属于当前 main 的交付范围。

## 6. Quick Start — Module A

Linux 环境：

~~~bash
git lfs install
git lfs pull

source .venv/bin/activate

# Run all regions
python scripts/run_module_a.py --all-regions

# Reproduce the current network fallback mode
python scripts/run_module_a.py \
  --all-regions \
  --reuse-legacy-east \
  --skip-network-supplementary

# Validate release
python scripts/validate_module_a_release.py

# Run tests
python -m pytest tests/ -q
~~~

完整人口栅格和广东 OSM PBF 通过 Git LFS 管理。没有获取 LFS 对象时，无法从原始数据完整重建人口裁剪、路网和 OD 结果。

## 7. Data Limitations

当前所有区域均为 DEMO_ONLY。

- 场所坐标属于候选地理编码结果，尚未实地核验。
- 2024 年登记容量不代表当前实际可用容量。
- 默认开放状态属于 Demo 假设。
- 当前分析边界不等于官方行政边界。
- 三个区域的路网由固定 Geofabrik Guangdong OSM PBF 快照独立生成；覆盖指标仍是网络候选可达性，不是容量分配后的真实覆盖率。
- 三个区域均没有官方行政边界，当前使用 `demo_analysis_boundary` 凸包/缓冲范围。
- 453 条源记录尚未与门户的 906 条记录完成完整对账。

## 8. Module Handoffs

| Handoff | Document |
|---|---|
| A → B | [A_to_B_v2.md](docs/handoffs/A_to_B_v2.md) |
| A → C | [A_to_C.md](docs/handoffs/A_to_C.md) |
| A → D | [A_to_D.md](docs/handoffs/A_to_D.md) |
| A Data Schema | [module_a_schema.md](docs/interfaces/module_a_schema.md) |
| A Development Report | [module_a_v2_final_report.md](docs/module_a_v2_final_report.md) |
| A Development Progress | [development_progress.md](docs/development_progress.md) |
| A Implementation Research | [IMPLEMENTATION_RESEARCH.md](docs/module_a/IMPLEMENTATION_RESEARCH.md) |
| B API → C | [API_HANDOFF_TO_C.md](https://github.com/mavistt/ShelterPlan-ICT/blob/feature/module-b/docs/module_b/API_HANDOFF_TO_C.md) |
| C API → D | [C_TO_D_API_HANDOFF.md](https://github.com/mavistt/ShelterPlan-ICT/blob/feature/module-c/docs/module_c/C_TO_D_API_HANDOFF.md) |

## 9. Development Roadmap

1. Complete road networks and OD matrices for all three regions.
2. Rebuild East District using the same network pipeline as the supplementary regions.
3. Optimize map layer size and frontend rendering.
4. Validate A v2 → B data adapters.
5. Add region-aware scenario selection to B/C interfaces.
6. Complete A/B/C/D integration.
7. Validate the full end-to-end demonstration.

## 10. Project Principles

- Preserve original source data.
- Keep each region's data and OD matrix isolated.
- Clearly distinguish original data, model estimates and Demo assumptions.
- Keep DEMO_ONLY disclosure visible throughout downstream modules.
- Maintain reproducible pipelines and machine-checkable data contracts.
- Do not describe a software demonstration as verified real-world emergency planning.
