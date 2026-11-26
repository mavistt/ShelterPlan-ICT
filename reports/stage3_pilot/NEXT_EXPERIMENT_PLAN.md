# ShelterPlan 下一轮实验计划

版本：2026-11-26
目标：把已有评测工程变成可展示、可复核的 Agent 证据

## 当前基线

冻结 8 题的真实 B Solver 结果是 6 PASS、2 UNSCORABLE。它证明了 A v2 显式来源绑定、B 求解、Verifier、Trace 和评分器可以工作；它不是 C Agent 的 VETS，也不是三种 Agent 的对照结果。

A v2 当前数据状态保持 DEMO_ONLY。东区与石岐不能因为 Solver 成功而升级为 VERIFIED。

## 分批执行

### 阶段 A：8 题接口验收

先对原有 8 题逐例经过 C session/turn API：

- 普通现状评估；
- 预算竞争；
- 容量竞争；
- 设施受损；
- 同一 Session 的多轮状态继承；
- 非法 shelter ID；
- 缺预算追问；
- 容量约束安全停止。

每例保留原始用户文本、Session、每个 Turn、最终 session snapshot、Tool/Stage/Trace、结果 ID 和错误状态。没有真实 C 输出的题保持 NOT_RUN 或 UNSCORABLE。

### 阶段 B：24 题 Pilot 评分

联调通过后使用已有 24 题，不重新生成 GIS，不改写历史结果。先冻结：

- task schema 与 user_turns；
- GT assertions；
- evaluator 版本；
- B Solver/Verifier；
- 模型、温度、解码参数；
- 工具权限、最大调用次数、Token 和时间预算；
- 故障模拟和用户模拟器规则。

### 阶段 C：40–60 题核心复合集

只从有独立语义和足够证据的案例中扩充，覆盖普通、预算、容量、灾损、多阶段、追问、拒绝、安全停止。改写措辞、重复运行和同一场景的表达变体不新增独立题数。

### 阶段 D：最终 144 题

最终目标是 3 个地区、6 类任务、每区 48 题，拆为 24 Pilot、72 Dev、48 sealed holdout。数据质量不足的小榄保持 disabled 或仅作拒绝/核验负例，不为凑数进入正常规划。

## 对照臂与公平控制

| 实验臂 | 输入/工具 | 主要问题 |
| --- | --- | --- |
| Direct LLM | 任务文本，无 GIS/Solver 工具 | 语言理解和说明能力的辅助基线 |
| Flat Tool Agent | 同数据、同 Solver 的高层工具 | 普通工具调用是否足够 |
| Fixed Workflow | 固定阶段流程 | 固定流程在条件分支/状态修改上的表现 |
| Composite + Harness | 结果驱动组合、状态契约和核验 | 完整架构的端到端收益 |

主比较是 Flat、Fixed、Composite；Direct LLM 只作辅助参考。所有主臂必须使用相同模型版本、解码参数、A v2 hash、Solver/Verifier、用户任务、工具权限和等价预算。

## 指标与状态

主指标是 VETS：expected_behavior 正确且所有适用硬断言通过的任务比例。另报：

- Cross-Stage Constraint Fidelity；
- Feasible Rate；
- Verified Recovery Quality 与独立 Oracle Gap；
- Trace/证据完整度；
- 运行时间、工具调用、真实 LLM token 和费用。

PASS、FAIL、NOT_RUN、INFRA_ERROR、UNSCORABLE 必须逐例保留。严格 VETS 分母包含计划运行集；conditional VETS 只作为补充，不能用来隐藏未运行项。

## 资源纪律

先在 8 题和 24 题估算每臂运行成本，再决定 40–60 题规模；没有上限的付费 LLM 评测不得启动。每个 Session 独立、每题环境清理、每次重试保留，不能只保留最好一次。

## 展示输出

下一轮应产生：

1. 一张逐例状态与错误分类表；
2. Flat/Fixed/Composite 的分层 VETS 与约束保持率；
3. 一个多阶段恢复案例的完整 Session/Trace 时间线；
4. 一个正确拒绝或安全停止案例；
5. 一个规划质量与 Oracle 证据等级表；
6. 明确列出未运行项和 B/C 团队依赖。

在真实 C Agent、Flat、Fixed、Composite 都完成同条件运行前，对外只能展示 B Pilot 和接口/流程证据，不宣称组合 Agent 已经优于固定流程。

