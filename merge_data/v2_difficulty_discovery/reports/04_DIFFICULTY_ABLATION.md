# 04 — 难度因素单因素消融

基线为固定 seed 的 16 项 competitive synthetic case，每次 MIP 时限 10 秒。每行只改变标明因素；所有图和选项均为 fully synthetic。

|变化|候选|需求点|OD 边|预算|MIP 服务量|Greedy gap|状态|时间|Verifier|
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
|基线|16|32|96|60.00|721.000|49.000|OPTIMAL|0.0483s|PASS_DEMO|
|loosen_budget|16|32|96|108.00|723.000|51.000|OPTIMAL|0.0536s|PASS_DEMO|
|reduce_exclusivity|16|32|96|60.00|766.000|5.000|OPTIMAL|0.0661s|PASS_DEMO|
|increase_overlap|16|32|96|60.00|721.000|49.000|OPTIMAL|0.0449s|PASS_DEMO|
|increase_candidates|32|32|96|60.00|721.000|61.000|OPTIMAL|0.0732s|PASS_DEMO|
|increase_demand_nodes|16|64|192|60.00|721.000|49.000|OPTIMAL|0.0729s|PASS_DEMO|
|increase_od_density|16|32|192|60.00|721.000|49.000|OPTIMAL|0.0860s|PASS_DEMO|

## 观察

基线 MIP 用时 0.0483s，目标 721.000。预算加宽的服务量变化同时包含更多可购买容量的影响，因此不能单独解释为求解难度上升。去掉互斥后目标上升反映可行域扩大；在此小实例中它仍很快求得最优。

增大服务范围重叠、候选数量、需求节点数和 OD 密度的单因素案例均保留了完整实际节点/边数、预算、gap、runtime 和 status；运行后没有从预期推断难度。所有七例均 OPTIMAL 且 PASS_DEMO。候选数到 32 时本组实例仍在约同一量级的亚秒区间，不支持“候选数单独必然造成困难”。

这些是单 seed 诊断，不足以估计统计因果效应；交互作用和不同 seed 需要后续重复实验。原始数字见 `oracles/phase_c_d_synthetic_results.json`。
