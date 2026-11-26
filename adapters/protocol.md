# ShelterPlan-Eval Adapter Protocol Draft

本文件是 A/Eval 与 B/C 的公开交接草案。本轮只定义数据和证据合同，不修改 B Solver、C Harness 或 D 前端。

## 1. 输入快照合同

每个 scenario 必须绑定：

~~~json
{
  "scenario_id": "SP-SCN-shiqi-001",
  "region_id": "shiqi",
  "data_version": "shelterplan_module_a_v2.0-demo__shiqi",
  "source_manifest_sha256": "actual-source-manifest-hash",
  "provenance": "GIS_GROUNDED_DEMO",
  "eligibility_status": "PASS_DEMO",
  "validator_version": "commit-or-semver"
}
~~~

不能把 verified、PASS_DEMO、Solver OPTIMAL 当成同一个概念。

## 2. B 输入要求

B adapter 至少应能接收并回显：

- region_id；
- data_version；
- source_manifest_sha256；
- scenario_id；
- eligibility_status；
- provenance；
- population_demand_ratio；
- max_walk_time_min；
- disabled_shelter_ids；
- hypothetical_options；
- budget_units；
- request_id / parent_scenario_id。

B 的 Solver 和 Verifier 结果只能作为执行证据，不能在独立评测中自动充当唯一 GT。

## 3. C 输出要求

C Harness 需要保留：

- 原始用户请求；
- 解析后的 region_id、scenario_id 和参数；
- Session 与 parent_scenario_id；
- 每个 stage 的输入、调用工具、响应和状态；
- B Solver/Verifier 的 result_id；
- explain_result；
- Trace 文件位置；
- PASS、FAIL、NOT_RUN、INFRA_ERROR、UNSCORABLE 的原因。

缺少真实 LLM、在线服务或关键数据时必须返回 NOT_RUN，不能用规则模拟结果冒充真实模型结果。

## 4. 准入和拒绝语义

- eligible_for_demo=true 只允许在 DEMO_ONLY 警告和已知假设被披露时使用；
- eligible_for_verified=true 需要独立证据，不能由脚本自动升级；
- disabled region 只能用于安全拒绝、人工核验或专门负例；
- needs_review 记录不进入要求高可信性的正常规划任务；
- excluded 记录保留源数据和原因，但不进入正常求解集合；
- region_id、data_version 或 source manifest 不一致时必须拒绝，不得静默回退旧数据。

## 5. 独立评测边界

Stage 2 以后可以增加：

- scenario factory；
- task factory；
- schema/state/feasibility/evidence checker；
- 独立最大流或枚举 oracle；
- VETS strict 与 conditional。


GT 必须在 Agent 执行前冻结。允许任意合法执行路径，不能把某个固定 DAG 的顺序当成唯一正确答案。
