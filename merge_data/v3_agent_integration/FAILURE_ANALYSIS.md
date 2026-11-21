# 失败与恢复分析

| 任务/事件 | 失败或缺口 | 分类 | trace | 恢复/结果 |
|---|---|---|---|---|
| T04 Greedy/MIP 解释 | `explain_result` 只返回未服务原因诊断；Greedy/MIP 目标、选项及差值均缺失 | B 任务输出契约/规划能力失败；不是求解器失败 | `traces/V3-04-GREEDY-MIP-EXPLAIN.json` | 未恢复；标为 FAIL，数学对照由独立 V2 Oracle保留 |
| T06 开发期解析问题 | `预算上限` 表达未被预算词形识别，隔离版补上别名并用 fresh Session 重跑 | C 意图解析失败 | `traces/V3-06-BUDGET-WALK-BIND.json`（修复后完整 trace） | 预算 30、步行 15 正确绑定；开发期首个失败没有单独保留完整 API trace，不把它计入任务分母 |
| T12 初始披露门禁 | 初始声明门禁漏拦“现场核验/可执行”近义表述 | C Verifier/声明门禁失败 | `traces/triage_initial/V3-12-DEMO-ONLY-DISCLOSURE.json` | 隔离补丁后拒绝，最终 trace `traces/V3-12-DEMO-ONLY-DISCLOSURE.json` PASS |
| T14 注入 Verifier FAIL | 结果验证状态 FAIL | Verifier 拒绝行为通过；不属于系统故障 | `traces/V3-14-VERIFIER-RECOVERY.json` | 拒绝发布；测试脚本之后人工发起全新优化，结果匹配 Oracle。不是 Harness 自动恢复 |
| T03 合成 8 项 trap | 公开 TaskRequest 不允许注入任意合成 demand/shelter/OD 图 | API 能力边界 | `traces/V3-03-SYN8-TRAP.json` | NOT_RUN，不放宽 12 项限制 |
| T17 普通 Agent 基线 | 无真实 LLM、无收费 API | 运行条件限制 | `traces/V3-17-ORDINARY-AGENT-BASELINE.json` | NOT_RUN；RuleLLM 不替代真实 LLM 性能数据 |
| MCP 传输 | 当前软件包索引未提供 `mcp>=1.2,<2` 可安装版本 | 工具传输/依赖可用性 | `reports/regression_test_summary.json` | MCP stdio/HTTP NOT_RUN；本轮通过本地 API 应用路径集成 |

## 端到端定位

- C Session/状态：本次明确验证的多轮预算、撤销恢复、方案修改、跨 Session 拒绝均符合 GT。跨 Session membership 检查是在 V3 隔离副本的修复。
- C 意图解析：预算/步行字段绑定最终通过；`预算上限` 解析别名在 V3 隔离副本修复。正式 C 未自动合并此补丁。
- B 任务规划/解释：T04 是当前最明确的功能缺口；解释接口未回答明确的 Greedy-vs-MIP 问题。
- B 数学求解：已运行的独立 Oracle 优化用例全部匹配；没有观察到求解器性能瓶颈。
- Verifier：能拒绝错误状态、Session/Scenario 不匹配和不允许的声明；它不是独立数学 Oracle，仍依赖 B 的证书/状态。
- 工具传输：本地 Python API 路径成功；MCP SDK 传输因 SDK 不可用未验证。

记录限制：T06 首次开发期失败没有单独的完整 API trace，修复后的 fresh-session trace 已保留。其余执行中的 FAIL、拒绝、NOT_RUN 和恢复路径均链接到对应 JSON trace。此限制不纳入通过率。
