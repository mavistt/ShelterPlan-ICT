# 03 — 隔离合成压力测试

所有输入均带 `FULLY_SYNTHETIC_STRESS` 来源标签，与 A 数据完全隔离。每种结构在 8/16/32/48/64 个候选下分别使用 2 秒和 10 秒预算，共 20 次完整 MIP。

|结构|候选数|2 秒状态/时间|10 秒状态/时间|2 秒验证|10 秒验证|
|---|---:|---:|---:|---|---|
|simple|8|OPTIMAL / 0.0323s|OPTIMAL / 0.0342s|PASS_DEMO|PASS_DEMO|
|simple|16|OPTIMAL / 0.0255s|OPTIMAL / 0.0349s|PASS_DEMO|PASS_DEMO|
|simple|32|OPTIMAL / 0.1177s|OPTIMAL / 0.1267s|PASS_DEMO|PASS_DEMO|
|simple|48|OPTIMAL / 0.0698s|OPTIMAL / 0.0723s|PASS_DEMO|PASS_DEMO|
|simple|64|OPTIMAL / 0.0869s|OPTIMAL / 0.0844s|PASS_DEMO|PASS_DEMO|
|competitive|8|OPTIMAL / 0.0480s|OPTIMAL / 0.0487s|PASS_DEMO|PASS_DEMO|
|competitive|16|OPTIMAL / 0.0421s|OPTIMAL / 0.0413s|PASS_DEMO|PASS_DEMO|
|competitive|32|OPTIMAL / 0.1105s|OPTIMAL / 0.1108s|PASS_DEMO|PASS_DEMO|
|competitive|48|OPTIMAL / 0.2085s|OPTIMAL / 0.1915s|PASS_DEMO|PASS_DEMO|
|competitive|64|OPTIMAL / 0.2721s|OPTIMAL / 0.2358s|PASS_DEMO|PASS_DEMO|

20 次 stress MIP 状态计数：`{'OPTIMAL': 20}`；所有案例 verifier 均为 PASS_DEMO。运行时均值 0.0997s，最大 0.2721s。RSS 10ms 抽样峰值最大 109.5 MiB（若环境提供 psutil）；单例 Python heap 峰值与每阶段 MIP 界/gap/node count 保存在 `oracles/phase_c_d_synthetic_results.json` 及逐例 trace。

在这组小型合成图中，候选增至 64 仍没有触及 2 秒限制，且每阶段均有界并证明最优。该测量只说明当前生成的模型容易，不代表 7,965 需求点 A 全图或更难的模型上限。FEASIBLE、TIME_LIMIT 等状态按原样保留，没有把未证最优改标为最优。

## 独立证明的合成反例

8 项 fully synthetic fixture：Greedy 服务 66.000000，MIP 100.000000，NetworkX Dinitz 穷举 100.000000，共评估 129 个预算可行子集；MIP `OPTIMAL`、Verifier `PASS_DEMO`。A 方案成本 12、增益 61；B/C 各成本 10、增益 50；预算 20，并有五个 1 成本/1 增益 decoy。Greedy 拿 A 后能加五个 decoy 得 66，Oracle 选 B+C 得 100。该 fixture 与 GIS 及 A 数据无关。

解、界、每阶段状态、内存测量和独立 Oracle 证据按 case ID 保存于 `runs/synthetic_stress/` 和 `scenarios/synthetic_stress/`。
