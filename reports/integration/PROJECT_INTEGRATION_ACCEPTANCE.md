# ShelterPlan 项目整合验收报告

日期：2026-11-26  
验收结论：**集成候选可运行，但最终目标尚未完成（NOT_READY）**

## 结论先行

这轮已经把 B/C/UI 候选和 Eval 适配器放进同一可运行验证环境，并修复了两个真实契约问题：

1. C Session 创建现在可显式绑定 `region_id` 和 A v2 `data_version`；版本不匹配会拒绝，不能静默回退到 legacy 或其他区域。
2. C `RegionInfo` 公共字段包含 `enabled` 与 `exclusion_reason`；B 结果已有的 `provenance.execution_trace_id` 会透传到 C Turn。

真实 HTTP 联调结果如下：

- 使用现有冻结 8 题，不生成新场景；8/8 题都创建了真实 C Session，并完成了至少一个真实 C Turn。
- RuleLLM Smoke 结果为 5 题 `SUCCEEDED`、3 题 `NEEDS_CLARIFICATION`。
- 所有 Session/Turn 都核对了显式 A v2 `region_id`、`data_version` 和 `DEMO_ONLY`。
- 这不是正式 LLM Agent 成绩；本轮没有使用外部真实 LLM，也没有将 RuleLLM 结果写入 VETS。

## 逐项验收

| 问题 | 当前结论 | 证据或缺口 |
|---|---|---|
| 1. 所有模块是否真正集成？ | 部分完成 | A v2、B Solver/Verifier、C Session/Turn、D 前端已在同一候选工作树；C 到 B 的复合恢复自然语言正式评测仍未完成。 |
| 2. 自然语言→Solver→Verifier 是否真实跑通？ | 单轮真实跑通 | `scripts/run_session_demo.py` 启动两个真实 HTTP 进程，C 真实解析并调用 B；返回 `result_id`、`verification_status=PASS_DEMO` 和 Trace ID。六阶段 Composite 的 C 端正式验收仍缺。 |
| 3. D 前端是否能展示真实结果？ | 代码和构建通过，端到端展示未完全验收 | 前端 Vitest 4/4、Vite production build 通过；完整浏览器点击链路和地图结果展示仍需在可用浏览器环境复验。 |
| 4. 真实 LLM Agent 是否运行？ | 否 | 只有 RuleLLM 确定性 Smoke；没有可核验的真实模型配置，不能冒充真实 Agent。 |
| 5. Flat / Fixed / Composite 是否公平对照？ | 否 | 三组尚未在相同模型、数据、Solver、Verifier、工具权限和预算下完成冻结 8 题。 |
| 6. VETS 是否有真实数据支持？ | 否 | Stage 3 B 结果有 6 PASS、2 UNSCORABLE；正式 Agent VETS 仍为 `NOT_READY`。 |
| 7. 还有哪些阻塞？ | 有 | 真实 LLM 凭据/配置、C Composite 六阶段自然语言验收、Flat/Fixed/Composite 对照、浏览器端到端展示复验、Windows pytest 临时目录 ACL。 |
| 8. GitHub 是否收敛到两个长期分支？ | 尚未 | 远端审计时仍有 14 个分支；目标是只保留 `main` 与 `integration/abc-shiqi-v2`。删除必须等 PR 合并和独立提交保留确认。 |
| 9. PR 是否已合并/关闭？ | 尚未完成 | 旧 PR #1/#6/#7/#8/#11/#12 仍需在替代 PR 创建并确认依赖后关闭；候选 PR 需要 GitHub 权限或人工创建/审核。 |
| 10. 干净环境能否重复演示？ | B/C RuleLLM 演示可以 | `pip install -r requirements-b.txt -r requirements-c.txt` 后运行 `python scripts/run_session_demo.py`；真实 LLM 和前端浏览器展示需要额外配置。 |

## 已验证证据

### A/B/Eval

- Stage 3 冻结 8 题的真实 B 结果：6 `PASS`、2 `UNSCORABLE`；没有把缺预算、容量探针能力不足或未运行 Agent 写成成功。
- Eval 当前回归：43 tests；A/GIS 回归：21 tests；B 回归：32 tests。
- A v2 原始数据、Manifest、GT 和 `configs/module_a_v2_b/` 未为通过测试而修改。
- 三个区域继续使用独立 `region_id`、`data_version` 和 `DEMO_ONLY` 声明。

### B/C HTTP

- 实际启动 B `uvicorn backend.app:app` 与 C `tests.module_c.api_http_launcher`，通过 localhost 真实 socket 联调。
- C Session 创建请求包含：

  ```json
  {
    "region_id": "shiqi",
    "data_version": "shelterplan_module_a_v2.0-demo__shiqi"
  }
  ```

- C Turn 终态会携带 `result_id`、`verification_status`、`data_status`、`execution_trace_id` 和结果解释；完整结果和证据仍可通过 Session 绑定的结果接口查询。
- 当前候选分支中的永久修复提交：`c42e967`（B/C merge：区域版本绑定、RegionInfo 公共字段）；前端和桥接历史来自候选分支，未把旧 Module C 分支整体覆盖进来。

### D 前端

- `npm run test`：2 个 test files、4 tests passed。
- `npm run build`：Vite production build passed；只留下 chunk size warning，不是构建失败。
- 前端依赖安装使用 `frontend/package-lock.json`，没有把 `node_modules` 纳入提交。

## 运行方式

安装 Python 依赖后执行确定性链路演示：

```bash
pip install -r requirements-b.txt -r requirements-c.txt
python scripts/run_session_demo.py
```

真实模型只在凭据和端点已经配置、并且输出可保存时执行：

```bash
python scripts/run_session_demo.py --llm-mode real
```

前端：

```bash
cd frontend
npm install
npm run test
npm run build
npm run dev
```

## Git 与分支治理

当前工作按两个候选交付方向整理：

- `feature/shelterplan-eval`：Stage 0–3.1 Eval、冻结任务、GT、Oracle、Trace 和 C Agent 适配器。
- `feat/composite-recovery-ui-bridge`：B/C Composite、Session/Turn、Harness、Guards、Workflow 和 D 前端整合候选。

两者都应以 `integration/abc-shiqi-v2` 为 base 建立 PR；只有在 PR 审查、回归和独立提交保留确认完成后，才允许合并并删除临时分支。禁止 force push。旧 Stage、Module A/B/C、Harness、UI 分支不能因为“看起来已包含”就先删，必须逐项核对提交图和 PR 依赖。

因此，本报告不把“候选代码已可运行”写成“GitHub 已收敛”。如果 GitHub Connector 没有创建/关闭/删除权限，应使用 PR compare 页面由维护者完成，并保留 API 返回的具体 403 作为阻塞证据。

## 最终判断

ShelterPlan 已从多个孤立模块推进到一个**可真实启动、可真实执行 B Solver、可验证结果和可保存 Trace 的集成候选**；但用户要求的“真实 LLM + 三组公平 Agent 对照 +正式 VETS + PR 合并后只保留两个长期分支 + 干净环境完整 D 展示”尚未全部发生。

在真实 LLM 凭据和 GitHub 合并/删除权限到位前，正确状态是 `INTEGRATION_CANDIDATE_READY / FORMAL_AGENT_EVAL_NOT_READY`，不是项目最终完成。
