# 06 — 失败、验证和限制

## 已观测的正确行为

- V1 报告记录：17 个有效预算 MIP 案例均 OPTIMAL、没有自然超时；D03 把可行 incumbent 状态注入为状态 1 后正确返回 FEASIBLE 且 `optimality_proven=false`。D02 未知场所选项被拒绝。D04 篡改服务量摘要后 Verifier 检出 `NUMERICAL_RECOMPUTATION` 并返回 FAIL。
- V1 独立整数最大流比较的误差处于取整误差界内；没有复现数学错误、非预期 verifier 漏检或数值不稳定。这不是对所有输入/版本的证明。
- V2 A 锚定完整 MIP：9 次中 `9` 次 OPTIMAL；确认案的独立 Oracle 与 MIP 差异小于保守取整界，所有证书 PASS_DEMO。
- V2 synthetic stress 20 次均 OPTIMAL、Verifier PASS_DEMO；FEASIBLE/TIME_LIMIT 记录数量为 0。消融 7 次均 OPTIMAL、PASS_DEMO。
- 测试：`tests/module_b` 30 passed; V2 合成 fixture 测试 3 passed; `compileall backend` passed。

## 未证实/边界

- A 数据是 `DEMO_ONLY`。Hypothetical cost/capacity 只能回答模型行为，不能当工程报价、可建容量或正式规划建议。
- 100 条 cheap prefilter 全部留档，但只有 9 条完整 GIS MIP；大于 12 项的候选案没有做独立全子集 Oracle。它们的 MIP/Greedy 差值是待复核观测，不计入已确认失败。
- 合成图远小于真实 A 全图；小图在 64 选项下都很快，不能外推真实网络的运行上限。
- RSS 以 10ms 轮询采样；非常短暂的 native peak 可能漏掉。Python heap 另行记录。
- 度为 1 的需求聚合及不可达需求移除只用于独立 max-flow oracle，服务流目标保持不变；详细投影大小与误差界保存在 Oracle JSON。
- Module C 的 feature branch 未合并，也未在本轮执行完整 MCP transport 或 B/C 联合验收；旧 review 列出的 P1 问题仍需解决。
- SciPy/HiGHS 的词典序容差、其他数据结构、不同版本、并发负载和极端预算尚未覆盖。

所有完整 MIP 状态仍依据 solver 返回的 `OPTIMAL`/`FEASIBLE` 和各词典序阶段记录判断，Verifier 只证明可行证书正确，不代替最优性证明。
