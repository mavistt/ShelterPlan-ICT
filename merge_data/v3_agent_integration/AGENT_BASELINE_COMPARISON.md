# Agent 基线对照

## 运行矩阵

| 组别 | 状态 | 实际覆盖 |
|---|---|---|
| A. 普通 Tool Agent | NOT_RUN | 没有真实 LLM；未调用收费 API |
| B. C Harness + B 任务接口 | 部分运行但不可独立归因 | C Harness 与直接 B TaskRequest 路径均有实测；公开 B TaskRequest 内部仍调用 Skill Graph，与 C 组无法分离 |
| C. C Harness + B Skill Graph | RUN | T01/T02 及其他任务通过 C Harness 调用真实本地 B 应用；Skill Graph trace 可见 |
| D. C Harness + Graph + 增强 Verifier | NOT_RUN（独立架构臂） | Verifier 门禁做了单项注入测试，但没有可切换的独立 D 实现或相同任务受控臂 |

目前无法完成用户要求的同一模型 A/B/C/D 性能对照。T01/T02 是本地确定性 Harness 与 B 的成对集成测量，不存在 LLM 推理差异，也不应当被解释为 Agent 架构排名。

## 可报告的配对结果

| 任务 | 路径 | Oracle 结果 | 端到端耗时 | B Skill Graph |
|---|---|---|---:|---|
| REAL-000 | 直接 B | 匹配 | 8041.65 ms | 执行 |
| REAL-000 | C Harness | 匹配 | 7697.07 ms | 执行 |
| REAL-081 | 直接 B | 匹配 | 8054.66 ms | 执行 |
| REAL-081 | C Harness | 匹配 | 8669.10 ms | 执行 |

只有两组配对，且 C Harness 包含 Session/轮询等开销；延迟结果上下波动，不能据此推断稳定差异。两条路径都能得到与独立 Oracle 一致的数学结果。

## 尚未测得的指标

真实 LLM 的 Verified Task Success Rate、普通 Tool Agent 工具调用次数、不同提示/上下文开销和真实模型失败恢复率均为 NOT_RUN。RuleLLM 只用于本地集成回归与字段解析，不作为真实 LLM Agent 性能数据。Skill Graph 的因果效应也无法从直接 B 对照中剥离，因为公开 B TaskRequest 没有 graph-off 接口。
