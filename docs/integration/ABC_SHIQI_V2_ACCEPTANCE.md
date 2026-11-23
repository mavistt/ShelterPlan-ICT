# ShelterPlan-ICT ABC Shiqi v2 集成验收

## 结论

当前集成分支的 A → B → C 主链路已通过真实 HTTP 验收。默认源是 Module A v2 石岐数据；B 使用真实 HiGHS Solver 和独立 Verifier，C 通过真实 HTTP/MCP 调用 B，并返回解释、版本、区域、Verifier 和 Trace 证据。

本次验收的输入数据状态是 `DEMO_ONLY`，因此结论是 `PASS_DEMO`，不是现场可执行或 `PASS_VERIFIED`。

## 集成基线

- 集成分支：`integration/abc-shiqi-v2`
- 基础分支：`origin/feature/module-c` @ `a798760`
- 合并的 A 分支：`origin/feature/module-a-stage1-research` @ `138d06c`
- A/B 合并提交：`5071e71`
- 本轮集成提交：`68d59094c3d7376c6a17c6fca22669177cb33712`
- PR #1：<https://github.com/mavistt/ShelterPlan-ICT/pull/1>，验收时仍为 OPEN；本分支没有合并 `main`
- 本轮 Draft PR #6：<https://github.com/mavistt/ShelterPlan-ICT/pull/6>，base 为 `feature/module-a-stage1-research`
- 默认 B 源：`configs/module_a_v2_b/shiqi.yaml`
- 区域：`shiqi`
- 数据版本：`shelterplan_module_a_v2.0-demo__shiqi`
- 数据规模：25 个场所、7336 个需求节点、183400 条 OD

## 已实施修改

### A → B 数据契约

- B 默认从 `configs/module_a_v2_b/shiqi.yaml` 读取 A v2，不再默认使用 A v1。
- 修复两级配置目录的 `repo_root` 推断。
- 对 `region_id`、`data_version`、Manifest、quality summary、OD 外键和区域行归属执行绑定校验。
- 兼容 A v2 的 `quality_summary.blockers`，同时保留旧字段兼容。
- 读取并执行 shelter `active` 状态；停用场所不会进入可达边或容量。
- 使用 B 配置中的默认情景参数；A v2 缺少 `scenario_defaults` 时安全使用 `0.2 / 15.0`，不回退到旧数据。
- 增加按配置/Manifest 版本隔离的 A bundle cache，并复用 Manifest hash，避免重复全量序列化。
- Windows `core.autocrlf` 只对声明的文本制品做 LF 规范化校验；Parquet、TIFF、PBF 仍严格按二进制校验。
- B capabilities 现在暴露 `region_id`、`data_version` 和 `data_status`。

### B → C 链路

- `TaskRequest`、场景、输入、结果和 provenance 正式携带 `region_id`。
- C 生产默认开启 `SHELTERPLAN_B_SEND_REGION_ID=true`。
- `assess`、`optimize`、`stress_test`、`explain_result` 均走真实 B HTTP；无 Mock B、monkeypatch 或伪造求解结果。
- Verifier 新增 `REGION_ID` 检查，并对 inactive shelter 执行容量/合法边约束。
- B 在石岐 v2 optimize 结果中生成真实 Greedy baseline，C 只读取并解释该证据，修复 T04 的 `greedy_comparison` 路径。
- 对显式声明旧 `data_version=zhongshan_pilot_v0.1` 的历史客户端保留兼容；这不是默认回退，未知版本会以 `B_SCENARIO_INVALID/VERSION_MISMATCH` 失败。

## Issue 对照

| Issue | 验收结论 |
|---|---|
| #2 | 石岐作为默认演示区域；A v2 处理产物和图层补全可读取；质量字段已兼容。大体积原始/衍生数据的仓库瘦身未在本轮扩大范围。 |
| #3 | B-7/B-9 已处理：默认源、缓存、repo_root、region/version、quality、active、Parquet OD 契约均通过。B-8 的结果投影和证据分页通过；B-10 的版本错误、显式旧源兼容和在线 Verifier 通过。 |
| #4 | A v2 默认源、默认情景参数、石岐主区域和 PR #1 依赖均已固化；没有自动合并 main。 |
| #5 | 石岐 v2 正式接入通过；T04 的 Greedy/MIP 证据、`explain_result`、版本/区域绑定和 Trace 通过。真实 LLM 15 题回归不在本次重测范围。 |

## 测试结果

状态只使用 `PASS / FAIL / NOT_RUN`：

| 范围 | 状态 | 命令/结果 |
|---|---|---|
| A v2/GIS | PASS | `python -B -m pytest -q tests/test_module_a_v2.py tests/test_gis_data.py tests/test_region_review_packages.py -p no:cacheprovider`；21 passed，23 warnings |
| B 单元测试 | PASS | `python -B -m unittest discover -s tests/module_b -p "test*.py"`；30 tests OK |
| C 全量回归 | PASS | `python -B -m pytest -q tests/module_c -p no:cacheprovider`；297 passed，3 warnings |
| A v2 CLI 读取 | PASS | `python -B -m backend.cli inspect-data --config configs/module_a_v2_b/shiqi.yaml --repo-root .`；25/7336/183400，missing_paths=[] |
| 真实 ABC HTTP | PASS | `python -B scripts/run_abc_shiqi_http.py --mcp-url http://127.0.0.1:8010/mcp --b-url http://127.0.0.1:8000`；B/C capabilities、assess、optimize、stress_test、explain_result、Trace evidence 全部 PASS |
| 真实 LLM 自然语言 | NOT_RUN | 本机未安装且无法访问 Ollama `127.0.0.1:11434`；本次没有把 RuleLLM 冒充真实模型结果 |
| A 原始 TIFF 全量重建 | NOT_RUN | `data/raw/chn_pop_2025_CN_100m_R2025A_v1.tif` 在当前 checkout 仍是 LFS pointer；已验证的 A v2 processed release 已足够完成 B/C 链路 |

## 最近一次真实 HTTP 运行

运行摘要：`outputs/module_c/abc_shiqi_http_run.json`；C Trace：`outputs/module_c/trace/abc-shiqi-http.jsonl`。这些运行产物位于仓库的 ignored `outputs/` 下，不把大量结果 JSON 提交进 Git。

- `assess`：`solver_status=OPTIMAL`，`verification_status=PASS_DEMO`
- `optimize`：真实预算参数继承，`solver_status=OPTIMAL`，`verification_status=PASS_DEMO`
- `stress_test`：`solver_status=OPTIMAL`，`verification_status=PASS_DEMO`
- `explain_result`：`solver_status=OPTIMAL`，`verification_status=PASS_DEMO`
- Trace evidence：8 条可分页证据

## 复现步骤

在仓库根目录执行：

```powershell
python -m pip install -r requirements.txt -r requirements-b.txt -r requirements-c.txt
python -B -m backend.cli inspect-data --config configs/module_a_v2_b/shiqi.yaml --repo-root .
python -B -m unittest discover -s tests/module_b -p "test*.py"
python -B -m pytest -q tests/module_c -p no:cacheprovider
```

真实 HTTP 验收需要先启动 B 和 C MCP HTTP 服务，并设置：

```text
SHELTERPLAN_B_BASE_URL=http://127.0.0.1:8000
SHELTERPLAN_REGION_ID=shiqi
SHELTERPLAN_DATA_VERSION=shelterplan_module_a_v2.0-demo__shiqi
SHELTERPLAN_B_SEND_REGION_ID=true
SHELTERPLAN_MCP_EXPOSE_EVIDENCE=1
```

然后执行：

```powershell
python -B scripts/run_abc_shiqi_http.py
```

## 残留项与阻塞

- 这套数据的正式质量门禁仍是 `DEMO_ONLY`，不能发布为现场核验结论。
- 未安装/未连接真实 LLM，因此自然语言模型路径保持 `NOT_RUN`；结构化 C/MCP HTTP 链路已通过真实 B/A 数据验收。
- 原始人口 TIFF 未拉取 LFS 大对象，A 的全量重建/重新生成没有执行；没有阻塞现有 A v2 processed data 的 B/C 集成。
- PR #1 仍是上游依赖；后续集成 PR 应以 `feature/module-a-stage1-research` 为 base，并在说明中关联 PR #1。不会自动合并 main。
