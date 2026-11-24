# Module D 前端接口审计与实施记录

## 基线与约束

- 基线分支：`integration/abc-shiqi-v2`，集成提交 `e9fc9dcd71c7afd04c6afdae616b780819ceeefc`。
- 本地实现分支：`feature/module-d-frontend`，位于独立 worktree。A/B/C 正式业务代码没有修改。
- 所有评估和规划通过 C Session/Turn 工作流执行；浏览器不会直接调用 B Solver。
- C 返回 `RUNNING` 后轮询真实 turn 状态，并区分 `SUCCEEDED`、`NEEDS_CLARIFICATION`、`FAILED`、`CANCELLED`。只有终态结果才会读取，result layers 和 evidence 请求都带当前 `session_id`。
- C 的取消响应保留原始 `cancel_accepted`。若 B 已开始计算且 C 未接受取消，界面说明后台求解可能仍会继续。
- 规划结果使用 `shelter_id` 与 A 场所 GeoJSON 做关联；不按数组顺序或名称推断空间位置。`FEASIBLE` 和 `OPTIMAL` 分别呈现。
- 所有地区明确显示 `DEMO_ONLY`。WorldPop 为模型人口估算；步行可达图层不代表容量约束下的实际服务人口。

## A v2 地理数据读取

- C 区域 API 当前提供 `east_district`、`shiqi`、`xiaolan`。演示默认使用 Shiqi 的 `shelterplan_module_a_v2.0-demo__shiqi` 数据版本。
- Shiqi 的目录统计为 25 个场所、7,336 个需求节点、183,400 条 OD 记录和 16 个 GeoJSON 图层目录项。目录读取以当前 Manifest 和 API 为准。
- C 的 layers 接口只给图层元数据和文件路径描述，并不保证浏览器能访问这些路径。因此本地 Vite 实现受限只读适配器：只接受 Manifest 声明或显式 allowlist 中的 region/layer ID，规范化换行后核对 SHA-256，并返回 GeoJSON MIME。
- 任意文件路径、未知图层、路径遍历均不接受；不会提供原始 OD Parquet。GeoTIFF 可在目录中说明，但当前不渲染。大型路网默认不加载。

## 实施内容

1. 在 `frontend/` 建立 React、TypeScript、Vite 单页应用，提供总览、空间数据、服务评估、情景模拟、预算优化、运行与证据页面。
2. 使用 MapLibre 展示 A v2 真实 GeoJSON，提供图层控制、场所信息和空间结果关联；使用 ECharts 展示真实接口结果。
3. 实现 C API 客户端、区域 Session 生命周期、多轮澄清、Turn 轮询、取消反馈、结果图层读取和分页证据。
4. 提供明确标注为假设值的预算优化输入；候选项遵守最多 12 项限制，不在浏览器重写分配算法。
5. 增加 Vitest、Testing Library、Playwright 检查与可复核的桌面截图。详细结果及限制记录在 [TEST_REPORT.md](TEST_REPORT.md)。

## 已完成的运行时验证

- B、C 健康检查、C capabilities/regions、Shiqi Session 创建：PASS。
- A v2 图层服务、Manifest 哈希、未知图层拒绝和路径遍历拒绝：PASS。
- 真实 C→B 能力评估、显式假设预算优化、同一 Session 的澄清与继续：PASS；结果带 `DEMO_ONLY`、Solver 和 Verifier 状态。
- 跨 Session result ID 请求返回 HTTP 403：PASS。
- 前端单元测试 3 项、Playwright 实际服务 E2E 6 项、TypeScript 检查、生产构建、依赖审计：PASS。
- Rule/fallback 曾将含“比较”字样的停用请求路由到 `compare` 并追问比较参数。前端将提交文本改成明确的压力测试请求，并等待 Session 就绪后开放操作；停用任务现在通过 C/B 完整流程成功。
- 活动 B 求解时 C 返回 `cancel_accepted=false`；前端显示任务可能继续的说明，真实浏览器回归确认 turn 后续完成而未被误报为取消。
- 取消端点在 B 活动求解时返回 `cancel_accepted=false`。界面已如实展示；浏览器内取消按钮交互未完成稳定 E2E 验证。
- 未运行 Python B/C 后端完整测试套件，也没有配置或调用付费 LLM。Rule/fallback 集成不代表真实 LLM 性能。

## 部署边界

当前图层服务由 Vite 开发/预览中间件提供，B/C 本地运行；Module C 没有认证。生产部署需提供等价的 Manifest allowlist 只读 GeoJSON API 和 `/api/c` 同源代理，不能将 Vite 开发服务器暴露给不可信网络。

## ?? / ?????????2026-11-24?

`east_district` ? `xiaolan` ? A v2 ?????Manifest?GIS GeoJSON ? C Session ??????? C ?????? B ?????????? `B_SCENARIO_INVALID` ????? B ???? Shiqi ???????????? Session ? `data_version`??????????? allowlist ????? [REGION_ANALYSIS_BLOCKER.md](REGION_ANALYSIS_BLOCKER.md)????? Module D ???????????????????? A/B/C ?????

??????????????WGS84 ?????????????????????????????????????????????????`npm run e2e` ?? 7/7 ???????/??????????????????????????? Rule/fallback?????? LLM Agent ???
