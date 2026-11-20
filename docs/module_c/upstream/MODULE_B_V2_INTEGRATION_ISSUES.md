# 上游协作问题单（Module B｜A v2 集成与性能清单）

- **来源**：Module C 侧集成审查（2026-11-21）
- **针对分支**：`feature/module-b` @ `5f8bb512`（对照 Module A v2 @ `138d06c8`）
- **已有问题单**：`MODULE_B_FLOAT_TOLERANCE_ISSUE.md`（即 B-1）及 `AGENTARTS_DEPLOYMENT_CHECKLIST.md` §9.1（B-1…B-6）继续有效；本文补充 **A v2 多区域放大 6–10 倍后必须解决的 P0/P1 问题（B-7…B-10）**。
- **原则说明**：Module C **未改动**任何 `backend/` 代码；在 B 侧改造完成前，C 侧默认不开启向 B 透传 `region_id`（需设置 `SHELTERPLAN_B_SEND_REGION_ID=1` 才开启），以免触发 B 的 `StrictModel(extra="forbid")` 422 拒收。

---

## B-7 [P0] `load_task_source()` 与 A v2 数据读取路径在大规模区域下崩溃 / 极慢

### 现状与证据
1. **缺少 `scenario_defaults` 必崩**：`backend/graph/executor.py:83` 的 `load_task_source()` 强行读取 `pilot["scenario_defaults"]`，而 GitHub 最新 `configs/module_a_v2_b/{east_district,shiqi,xiaolan}.yaml` 无该字段，切过去即抛 `KeyError` → `DataValidationError`。
2. **每请求零缓存全量重读**：`backend/graph/executor.py:148` 每次 `execute_task()` 都重新调用 `load_a_bundle()`，无任何内存级 bundle 缓存。
3. **重复 O(187 万) 开销**：在 `xiaolan`（60,406 需求点 × 31 场所 = **1,872,586** 行 OD）或 `east_district`（38,469 × 31 = **1,192,539** 行 OD）下：
   - `backend/adapters/a_reader.py:221` 读取 17–26 MB 的 `demand_nodes.geojson`，未利用已产出的 0.56–0.85 MB `demand_nodes.parquet`；
   - `a_reader.py:126-164` 逐行实例化 187 万个冻结 Pydantic `ODEdge` 对象；
   - `backend/models.py:100-119`（`PlanningInput.validate_relations`）又构建百万级元组 `set` 做全量二次校验；
   - `backend/graph/executor.py:169` 调用 `canonical_hash(data.model_dump())`，把 187 万个 `ODEdge` 序列化为约 250 MB JSON 字符串只为计算 `source_hash`（此时已有 `bundle.manifest.source_manifest_hash`）。

### 建议修法
1. 在 `backend/graph/executor.py` 按 `(region_id, data_version, source_manifest_hash)` 缓存已加载并校验过的 `PlanningInput` / `ADataBundle`（避免每次请求重读 Parquet 与重算 SHA-256）；
2. `source_hash` 直接使用 `bundle.manifest.source_manifest_hash`（或对 `demands`/`shelters`/manifest hash 做轻量摘要），**不要对 187 万条 `ODEdge` 执行 `model_dump()`**；
3. `load_task_source()` 若配置无 `scenario_defaults`，回退到 `configs/module_b.yaml` 的默认参数；
4. 优先读取 `demand_nodes.parquet`，并用轻量化数组/索引或禁用重复全表校验替代百万级 `StrictModel` 对象构造。

---

## B-8 [P0] Verifier 明细与全量结果 JSON 在 6 万节点规模下膨胀至 80–150 MB

### 现状与证据
- `backend/verifier/checks.py:46-72` 对**每一条** `assignment` 和**每一个**需求节点各生成一条 `CheckRecord`（含 4~6 个字符串字段）；
- A v0.1（7,965 点）的单次结果已达 5–11 MB；在 `xiaolan`（60,406 点）运行 `optimize`（含 `certificate` + `optimization_certificate` + 双份 `deficits` + `diagnosis`）将产出 **15 万+ 条 check**，经 `json.dumps(..., indent=2)` 落盘与 HTTP 传输可达 **80–150 MB**；
- `backend/graph/executor.py:349-351` 在 `optimize`、`compare`、`stress_test`、`sensitivity` 任务上也会额外执行一次 `diagnose()`（再扫一遍 187 万 OD 边），只为统计 `len(diagnosis_for_summary)`；
- `backend/engine.py:455` 与 `backend/verifier/checks.py:30` 在百万级边循环内使用 `in scenario.disabled_shelter_ids`（`list` O(K) 查找，未先转 `set`）；
- `backend/engine.py:16-17` 硬编码 `MAX_LP_SOLVE_SECONDS = 10.0`、`MAX_BUDGET_SOLVE_SECONDS = 10.0`，4 阶段串行 MIP 在 6 万节点下极易超时或仅返回非最优可行解。

### 建议修法
1. `verify_assignment` 保留全部数学与约束校验逻辑，但对**通过**的逐点检查改为聚合计数（`passed_count`），仅保留失败明细或前 N 条抽样（完整明细可按需落盘为 Parquet/NDJSON），把单次 JSON 结果控制在数 MB 以内；
2. 新增轻量只读端点：`GET /planning/results/{result_id}/summary` 与 `GET /planning/results/{result_id}/layers`（仅返回 `shelter_loads`/`shelter_utilization`/`deficits` 摘要），避免 C 侧只为取 31 行负载表却下载数十 MB JSON；
3. 非诊断类任务直接统计 `sum(1 for row in base_result.deficits if row["unserved_persons"] > tol)`，不再重复全扫 `diagnose()`；
4. `disabled_shelter_ids` 在循环前先转为 `set`；结合 `shiqi` / `east_district` / `xiaolan` 实测调整或开放 `time_limit_seconds` 配置。

---

## B-9 [P0] 多区域选择、嵌套路径识别与字段契约对齐

### 现状与证据
1. **缺少 `region_id` 字段**：`backend/models.py` 所有模型继承 `StrictModel(extra="forbid")`，`TaskRequest` 与 `PlanningScenario` 尚未声明 `region_id: str | None = None`，传入即返回 HTTP 422；
2. **嵌套路径 root 误判**：`backend/adapters/a_reader.py:182, 207` 默认用 `config_path.resolve().parent.parent` 推断 `repo_root`，对 `configs/module_a_v2_b/east_district.yaml` 会误判为 `configs/`；`backend/cli.py:27, 30` 未透传 `--repo-root`；
3. **A v2 字段读取遗漏**：`a_reader.py:271` 读取 `real_pilot_blockers`（A v2 已改名为 `blockers`）；`a_reader.py:223-227` 未读取 `shelters.geojson` 中的 `active` 字段；
4. **跨区域缓存与结果隔离**：`DependencyCache` 与结果/情景账本需确保缓存键与返回结果均显式包含 `region_id` 与 `data_version`，缺失 OD 的区域返回明确 `DATA_UNAVAILABLE`。

### 建议修法
1. 在 `TaskRequest`、`PlanningScenario`（及结果顶层/provenance）新增可选 `region_id` 字段，并支持按 `region_id` 映射 `configs/module_a_v2_b/{region_id}.yaml` 加载与缓存隔离；
2. 修正在 `a_reader.py` 的项目根探测（支持向上查找 `configs/` 或由 `cli.py` 新增 `--repo-root`），同时兼容读取 `blockers or real_pilot_blockers` 与 `row.get("active", True)`；
3. 在 `configs/module_b.yaml` 支持多区域配置后，与 C 侧联调时由 C 开启 `SHELTERPLAN_B_SEND_REGION_ID=1`。

---

## B-10 [P0] 原有已知问题汇总（来自 B-1…B-6）

请一并在合并前确认修复进度（详见 `MODULE_B_FLOAT_TOLERANCE_ISSUE.md` 与 `AGENTARTS_DEPLOYMENT_CHECKLIST.md` §9.1）：
- **B-1**：`backend/engine.py:368-403` MIP 第 2/3/4 阶段用 `1e-9` 绝对下界导致 `FEASIBLE` 降级（`tests/module_b` 现有 1 条失败）；
- **B-2**：`parent_scenario_id` 不继承参数（C 侧已在会话层兜底每轮重发全量快照，但建议 B 文档与行为一致）；
- **B-3**：`backend/storage.py:72-83` `find_scenario_version()` 线性扫描任意首个匹配 `scenario_id`，同 ID 多快照下不稳定；
- **B-4**：`verify_summary_claims` 尚未接入线上接口；
- **B-5**：无 CORS / 鉴权 / 限流，job 表在进程内存中；
- **B-6**：错误体存在扁平 `{error_code}` 与 `{"detail": {...}}` 两套形状（C 侧 `b_client.py` 已兼容双形状），`executor.py:176` 报错文案误写 `"synthetic fixture data"`。
