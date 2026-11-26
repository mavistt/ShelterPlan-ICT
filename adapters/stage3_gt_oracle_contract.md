# Stage 3 GT / Oracle 输入契约

Stage 2 Pilot 只生成结构化 DEMO_ONLY 场景，不生成 GT，也不把 B Solver 结果当作 GT。Stage 3 在 A v2 Manifest 阻塞解除或明确标记不可评分后，才能冻结 GT。

## GT 最小字段

每个 GT 记录必须绑定：

- scenario_id；
- region_id；
- data_version；
- source_manifest_sha256；
- a_v2_manifest_sha256；
- expected_behavior：EXECUTE、CLARIFY、REJECT、SAFE_STOP 或 RECOVER；
- feasibility_constraints；
- expected_state_transition；
- oracle_source；
- independent_oracle_version；
- reproducible_seed；
- provenance 和 verification_status。

## Oracle 约束

1. Oracle 必须独立于 Module B Solver，不能直接复制 Solver 输出作为唯一正确答案；
2. 计算必须绑定 region_id、data_version、两个 Manifest hash 和 scenario_seed；
3. 必须记录容量、预算、步行时间、disabled shelter 和恢复项目约束；
4. Manifest 校验失败时只能输出 UNCHECKABLE/UNSCORABLE 及原因，不能输出 VERIFIED GT；
5. 计算结果、输入快照和版本必须可重放；
6. 对 CLARIFY、REJECT、SAFE_STOP 类型，GT 应描述允许的安全行为和禁止的静默回退；
7. 任何人工修订必须保留 reviewer、时间、理由和原始候选。

## Stage 3 不可接受的替代

- 用 historical merge_data 冒充当前 A v2 GT；
- 用 Greedy 或 B Solver 单次结果冒充独立 Oracle；
- 因 Manifest mismatch 而重写 Manifest；
- 把 DEMO_ONLY、approximate 或 needs_review 记录升级为 VERIFIED；
- 仅保留接受集而删除 reject log。
