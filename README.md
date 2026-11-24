# ShelterPlan-ICT

ShelterPlan 是面向基层工作人员的社区应急避难服务评估与设施规划助手，整合 GIS 空间数据、容量约束规划优化和自然语言 Agent 交互。

> **当前状态：`PASS_DEMO` / `DEMO_ONLY`。** Module A v2 已完成东区、石岐和小榄三个区域的数据准备、固定 PBF 步行网络、OD 矩阵和空间分析；Module B、C 正在进行正式 A/B/C 集成。所有结果仅用于软件演示与算法测试，不代表经过现场核验的应急规划建议。

## 模块架构

| 模块 | 主要职责 | 分支/位置 |
|---|---|---|
| A — GIS & Spatial Analysis | 场所、人口、道路网络、OD、覆盖图层和规划情景 | `feature/module-a-stage1-research` |
| B — Planning Engine | 需求分配、容量优化、压力测试、方案比较和独立验证 | `feature/module-b` / `backend/` |
| C — Agent Orchestration | 自然语言理解、参数澄清、会话管理、API、MCP、解释和 Trace | `feature/module-c` / `agent/` |
| D — Frontend | 地图展示、规划交互和结果可视化 | [`abcd` 展示分支](https://github.com/mavistt/ShelterPlan-ICT/tree/abcd) / `frontend/` |

目标链路为：

```text
用户自然语言 → C Intent/Harness → B Skill Graph/Solver → A v2 数据
→ B Verifier → C 结果解释 → 完整 Trace
```

## Module A v2 数据

三个区域保持独立的数据版本、Manifest、OD、缓存和结果：

| 区域 | 场所 | 需求节点 | OD | data_version |
|---|---:|---:|---:|---|
| `east_district` | 31 | 38,469 | 1,192,539 | `shelterplan_module_a_v2.0-demo__east_district` |
| `shiqi` | 25 | 7,336 | 183,400 | `shelterplan_module_a_v2.0-demo__shiqi` |
| `xiaolan` | 31 | 60,406 | 1,872,586 | `shelterplan_module_a_v2.0-demo__xiaolan` |

石岐仍是默认区域；`abcd` 分支加入 Shiqi、East District、Xiaolan 的显式 B 数据源白名单。A v2 产物包括 `shelters.geojson`、`demand_nodes.geojson`/`parquet`、`travel_matrix.parquet`、`manifest.json`、`quality_summary.json` 和场景目录。所有区域都必须保持 `DEMO_ONLY` 声明。

## 快速开始

Python 3.11/3.12：

```bash
pip install -r requirements-b.txt -r requirements-c.txt
git lfs install
git lfs pull
```

启动 Module B：

```bash
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

启动 Module C：

```bash
SHELTERPLAN_B_BASE_URL=http://127.0.0.1:8000 \
python -m uvicorn agent.api:app --host 127.0.0.1 --port 8080
```

Module C 的确定性业务演示和真实 Ollama 路径：

```bash
python examples/c_to_d_business_flow.py
python examples/c_to_d_business_flow.py --real-llm
```

## 测试

```bash
python -m pytest tests/module_b -q
python -m pytest tests/module_c -q
python -m pytest tests/ -q
python scripts/validate_module_a_release.py
```

集成验收报告位于 `docs/integration/ABC_SHIQI_V2_ACCEPTANCE.md`。

## 重要边界

- A 的人口是 WorldPop 模型估计，OSM 是固定快照；场所坐标、容量和开放状态尚未完成现场核验。
- B 负责真实数学求解和独立 Verifier，C 不得重新实现或伪造优化结果。
- 不同区域不得混用人口、场所、OD、缓存或结果；请求必须绑定 `region_id` 和 `data_version`。
- 缺失数据、哈希异常或版本不匹配必须显式失败，不能静默回退到其他区域或旧数据。
- `DEMO_ONLY` 不得被解释为现场核验或正式应急规划结论。

## 交接文档

- A → B：`docs/handoffs/A_to_B_v2.md`
- A → C：`docs/handoffs/A_to_C.md`
- A 数据结构：`docs/interfaces/module_a_schema.md`
- C → D：`docs/module_c/C_TO_D_API_HANDOFF.md`
- B → C：`docs/module_b/API_HANDOFF_TO_C.md`

## Module D 展示与多地区 B 数据源

本次工作发布在 [`abcd` 分支](https://github.com/mavistt/ShelterPlan-ICT/tree/abcd)，基于 `integration/abc-shiqi-v2`。该分支包含 Module D 前端和 B 的地区数据源白名单路由；未指定地区时仍使用石岐默认配置。前端通过 C Session/Turn API 运行任务，并读取受限的 A v2 GIS 图层。

- 前端启动、依赖与部署边界：[`frontend/README.md`](frontend/README.md)
- 集成和复现记录：[`docs/module_d/`](docs/module_d/)
- 首页预览：[1440×900](frontend/screenshots/workspace_1440x900.png) · [1920×1080](frontend/screenshots/workspace_1920x1080.png)
- 地区地图：[东区街道](frontend/screenshots/spatial_east_district_1440x900.png) · [小榄镇](frontend/screenshots/spatial_xiaolan_1440x900.png)
- 最近一次东区和小榄评估的 C Trace：[region_analysis_retry_2026-11-24.json](outputs/module_d/region_analysis_retry_2026-11-24.json)

上述分析结果均为 `DEMO_ONLY` / `PASS_DEMO`，仅用于软件演示和算法评估，不代表现场核验的应急规划结论。
