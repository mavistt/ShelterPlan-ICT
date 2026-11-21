# 02 — A GIS 锚定的困难实例搜索

## 边界与搜索预算

固定随机种子 20261114，生成并完成 100 个便宜结构筛选（上限 100），其中 100 个通过；随后执行 9 次完整 MIP（上限 30，每例 10 秒）。组合大小覆盖定向 3 项案以及 8、16、24、32 项。全部新增扩容量和成本标为假设值；原始 A 节点、人口、场所容量和 OD 边不变。

筛选阶段只用每个候选场所的可达人口和扩容量构造乐观上界、增益成本排序投影及预算可行子集上界；它不是 MIP，也不据此宣称贪心或 MIP性能。筛选通过/拒绝的 100 条完整记录在 `scenarios/real_gis_search/screening_records.json`。

## 完整优化结果

|候选|数量|预算|Greedy 服务量|MIP 服务量|差值|MIP 状态|Verifier|Oracle|
|---|---:|---:|---:|---:|---:|---|---|---|
|REAL-000-TARGETED-TRAP|3|20.00|6,516.103|6,556.103|40.000|OPTIMAL|PASS_DEMO|ENUMERATED_MAXFLOW_PROVEN|
|REAL-041|8|26.10|6,826.103|6,826.103|0.000|OPTIMAL|PASS_DEMO|not run|
|REAL-081|8|26.64|6,826.103|6,846.103|20.000|OPTIMAL|PASS_DEMO|ENUMERATED_MAXFLOW_PROVEN|
|REAL-034|16|94.32|7,366.103|7,386.103|20.000|OPTIMAL|PASS_DEMO|NOT_RUN_CANDIDATE_SET_OVER_12|
|REAL-046|16|66.96|7,386.103|7,456.103|70.000|OPTIMAL|PASS_DEMO|NOT_RUN_CANDIDATE_SET_OVER_12|
|REAL-023|24|98.64|7,496.103|7,646.103|150.000|OPTIMAL|PASS_DEMO|NOT_RUN_CANDIDATE_SET_OVER_12|
|REAL-059|24|112.32|7,416.103|7,486.103|70.000|OPTIMAL|PASS_DEMO|NOT_RUN_CANDIDATE_SET_OVER_12|
|REAL-036|32|134.64|7,746.103|7,806.103|60.000|OPTIMAL|PASS_DEMO|NOT_RUN_CANDIDATE_SET_OVER_12|
|REAL-048|32|151.92|8,036.103|8,096.103|60.000|OPTIMAL|PASS_DEMO|NOT_RUN_CANDIDATE_SET_OVER_12|

## 已独立确认的 A 锚定差距

- **REAL-000-TARGETED-TRAP：**Greedy 6516.103027，MIP 6556.103027，独立枚举最大流 Oracle 6556.103034；差距 40.000000 人，Oracle 取整误差上界 0.003994 人，MIP `OPTIMAL`，Verifier `PASS_DEMO`。Oracle 将原 A 图中不可达点移除、将度为 1 的需求点按唯一可达场所聚合；所有多边需求、边、场所容量、选项及预算保留，最大服务流不变。完整投影说明和枚举子集数在 JSON 记录内。
- **REAL-081：**Greedy 6826.103027，MIP 6846.103027，独立枚举最大流 Oracle 6846.103034；差距 20.000000 人，Oracle 取整误差上界 0.003994 人，MIP `OPTIMAL`，Verifier `PASS_DEMO`。Oracle 将原 A 图中不可达点移除、将度为 1 的需求点按唯一可达场所聚合；所有多边需求、边、场所容量、选项及预算保留，最大服务流不变。完整投影说明和枚举子集数在 JSON 记录内。

定向案例的首要增量来自 Phase A 实测单项容量增益：A=60、B=50、C=50 人；唯一改变的是明确假设成本 A=11、B=C=10 和预算 20。Greedy 因 60/11 大于 50/10 先选 A，预算不足以再选 B 或 C；MIP 与 Oracle 选 B+C。总服务量对应原基线 6,456.103 人加各自增量。该结果是**不改 A GIS 图的假设投资反例**，不是经核实的真实工程报价或实施建议。

8 项的 `REAL-081` 也满足严格证据条件，并完成独立枚举。16/24/32 项案例中可见 MIP 相对 Greedy 的更大差值，但本轮没有对超过 12 项的全集合做独立枚举，故都标记为未由独立 Oracle 复核，不纳入“已证明失败”计数。

A 数据文件哈希执行前后完全一致：`True`。A 数据状态仍为 `DEMO_ONLY`，全部输出只能用于 DEMO。

## 判断

搜索不止一次随机抽样：100 个结构筛选全部保留，按候选数量分层选出 9 个完整优化。当前结果证明同一 A 图上存在由假设预算/成本产生的可辨识 Greedy 差距；它不意味着真实项目成本存在该关系。想验证现场难度，还需经核实的工程成本、可实施容量和正式 A 发布。
