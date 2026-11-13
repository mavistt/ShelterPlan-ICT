# ShelterPlan-ICT

ShelterPlan：面向城市应急避难场所规划的多模块项目，按模块分支协作开发。

> **数据状态：`DEMO_ONLY`。** 当前输入为 `data/raw/zhongshan_shelters.xls`
> （453 条 2024 年记录），试点流程使用 24 个未核验场所点，A7 判定为 `DEMO_ONLY`。
> 任何模块的输出都不得表述为现场核验的应急建议。

## 模块与分支

| 模块 | 内容 | 分支 |
|---|---|---|
| A | 避难场所数据清洗、试点 GIS 数据、步行时间矩阵与独立质量审计（详见 [`docs/module_a.md`](docs/module_a.md)） | `main` |
| B | 规划求解服务：覆盖评估、扩容优化、压力测试、方案比较（`backend/`） | `feature/module-b` |
| C | Agent 执行层：MCP 工具、结构化意图、会话守卫、Web API 与全量评测（`agent/`） | `feature/module-c`（本分支） |

## Module C 快速开始（本分支）

依赖：Python 3.11/3.12，安装 Module B 运行时 + Module C 增量：

```bash
pip install -r requirements-b.txt -r requirements-c.txt
```

启动 Module B（终端 1，仓库根目录；求解器仅限本机访问）：

```bash
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

启动 Module C 的 Web API（终端 2，给 Module D 对接）：

```bash
SHELTERPLAN_B_BASE_URL=http://127.0.0.1:8000 \
python -m uvicorn agent.api:app --host 127.0.0.1 --port 8080
```

MCP 入口（可选，stdio / Streamable HTTP）：

```bash
python -m agent.mcp_server --transport stdio
python -m agent.mcp_server --transport http --host 127.0.0.1 --port 8010
python -m agent.mcp_server --list-tools        # 恰好 4 个粗粒度工具
```

端到端业务演示（脚本自动拉起 B + C 两个真实进程，跑通
现状评估 → 修改条件 → 扩容优化 → 方案比较，并演示澄清续跑）：

```bash
python examples/c_to_d_business_flow.py             # 确定性模型替身（无需模型服务）
python examples/c_to_d_business_flow.py --real-llm  # 本地 Ollama 真实模型
```

Module C 的实现细节、目录结构与硬规则见 [`agent/README.md`](agent/README.md)；
D 侧对接的完整接口契约（字段、错误码、curl 示例）见
[`docs/module_c/C_TO_D_API_HANDOFF.md`](docs/module_c/C_TO_D_API_HANDOFF.md)。

## 运行测试

```bash
python -m pytest tests/module_c -q    # Module C 全量（基线 260 passed）
python -m pytest tests/module_b -q    # Module B（1 条已知失败，见下）
```

评测与历史报告复现（V0–V2.3 报告在 `docs/module_c/`，证据哈希清单在
[`docs/module_c/evidence/MANIFEST.md`](docs/module_c/evidence/MANIFEST.md)）：

```bash
python -m tests.module_c.recompute_v2    # 历史 V2 指标只读复核
```

## 已知限制

- **`DEMO_ONLY`**：数据未核验，不能解释为完整真实试点覆盖率；`DEMO_ONLY`
  由程序化声明保证，不接受对话修改。
- **B 的浮点问题**：`backend/engine.py` 第二阶段下界使用浮点容差，最优性证明
  可能降级为 `FEASIBLE`（≠ `OPTIMAL`），详见
  [`docs/module_c/upstream/MODULE_B_FLOAT_TOLERANCE_ISSUE.md`](docs/module_c/upstream/MODULE_B_FLOAT_TOLERANCE_ISSUE.md)。
- **单进程状态**：Module C 的会话/作业状态为单进程内保存（含文件恢复）；
  服务重启后未完成作业标记 `INTERRUPTED_BY_RESTART`。
- **AgentArts 未部署**：云侧节点映射与联调仍在准备阶段，验收清单见
  [`docs/module_c/AGENTARTS_DEPLOYMENT_CHECKLIST.md`](docs/module_c/AGENTARTS_DEPLOYMENT_CHECKLIST.md)。
- **非作者独立验证 PENDING**：≥20 条外部自然语言输入尚未收到；
  冻结工具 `freeze_holdout.py` 与模板已就绪，收到前不得声称完成独立验证。

## Module A（原 GIS 数据模块）

当前输入为 `data/raw/zhongshan_shelters.xls`，包含 453 条 2024 年记录。东区演示
流程当前使用 24 个未核验场所点，因此 A7 判定为 `DEMO_ONLY`，不能解释为完整真实
试点覆盖率。

在仓库根目录运行 A 的流水线与测试：

```bash
bash scripts/run_stage_a_demo.sh      # 需先安装 requirements.txt（A 的 GIS 栈）
python -m pytest tests/ -q            # 全部模块；或只跑 tests/module_b、tests/module_c
```

详细阶段状态、文件用途和交接方式见 [`docs/module_a.md`](docs/module_a.md)。
