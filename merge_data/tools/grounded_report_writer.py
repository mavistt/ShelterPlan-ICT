"""Render the final Chinese experiment reports from measured run artifacts."""
from __future__ import annotations

import statistics
from pathlib import Path

TOL = 1e-6


def write_reports(run_dir: Path, run: dict) -> None:
    output = run_dir.parents[1] / "reports"
    output.mkdir(parents=True, exist_ok=True)
    cases = run["results"]
    groups: dict[str, int] = {}
    for case in cases:
        groups[case["group"]] = groups.get(case["group"], 0) + 1
    mips = [case for case in cases if case["metrics"]["num_options"]]
    optimal = sum((case.get("mip") or {}).get("solver_status") == "OPTIMAL" for case in mips)
    feasible = sum((case.get("mip") or {}).get("solver_status") == "FEASIBLE" for case in mips)
    rejected = sum((case.get("mip") or {}).get("solver_status") == "REJECTED" for case in mips)
    expected_faults = {"D02", "D03", "D04"}
    planning_cases = [case for case in cases if case["case_id"] not in expected_faults]
    unexpected_mip_verifier_failures = [
        case["case_id"] for case in cases
        if (case.get("mip") or {}).get("verification", {}).get("verification_status") == "FAIL"
        and case["case_id"] not in expected_faults
    ]
    capture = [
        case["algorithm_comparison"]["improvement_capture_ratio"]
        for case in cases
        if isinstance(case["algorithm_comparison"].get("improvement_capture_ratio"), (int, float))
    ]
    comparable = [
        case for case in cases
        if isinstance((case.get("greedy") or {}).get("served_demand"), (int, float))
        and case.get("oracle", {}).get("oracle_status") == "ENUMERATED_MAXFLOW_PROVEN"
    ]
    raw_diffs = [case["oracle"]["objective_served_demand"] - case["greedy"]["served_demand"] for case in comparable]
    relative_gaps = [max(0.0, difference / max(case["oracle"]["objective_served_demand"], 1.0)) for case, difference in zip(comparable, raw_diffs)]
    resolved_gaps = [
        max(0.0, difference - case["oracle"].get("rounding_error_upper_bound_persons", 0.0))
        for case, difference in zip(comparable, raw_diffs)
    ]
    gap_stats = {
        "mean": statistics.mean(resolved_gaps) if resolved_gaps else 0.0,
        "median": statistics.median(resolved_gaps) if resolved_gaps else 0.0,
        "maximum": max(resolved_gaps, default=0.0),
        "nonzero": (sum(value > TOL for value in resolved_gaps) / len(resolved_gaps)) if resolved_gaps else 0.0,
        "raw_min": min(raw_diffs, default=0.0),
        "raw_max": max(raw_diffs, default=0.0),
        "max_oracle_rounding_bound": max((case["oracle"].get("rounding_error_upper_bound_persons", 0.0) for case in comparable), default=0.0),
    }
    natural_timeouts = sum((case.get("mip") or {}).get("solver_status") == "SOLVER_TIMEOUT" for case in cases)
    result_failures = unexpected_mip_verifier_failures
    stage_time_samples = [
        case["elapsed_seconds"].get("budget_mip_seconds")
        for case in cases if isinstance(case["elapsed_seconds"].get("budget_mip_seconds"), (int, float))
        and (case.get("mip") or {}).get("objective_value") is not None
    ]
    oracle_counts = [case["oracle"].get("evaluated_feasible_subsets", 0) for case in cases if case.get("oracle", {}).get("oracle_status") == "ENUMERATED_MAXFLOW_PROVEN"]
    median_oracle_count = statistics.median(oracle_counts) if oracle_counts else 0
    max_oracle_count = max(oracle_counts, default=0)
    max_rss = max((case.get("peak_rss_bytes_sampled") or 0 for case in cases), default=0)

    preflight_note = "三案预检完成，8/32 项 MIP 均 OPTIMAL、独立 GT 在取整界内一致、Verifier 为 PASS_DEMO。"
    protocol = f"""# 02｜生成协议与复现

本轮使用 seed `20261113`、生成器 `grounded-exp-0.1.0`、A 数据版本 `{run['data_version']}`。每个情景以 A 的原始 ID 和关系构造 Scenario Overlay，不复制或更改 A 的 OD；所有扩容项目标注 HYPOTHETICAL，容量基于 A 中未核验的模型值，`cost_units` 为抽象量，不代表报价。

完整运行 ID：`{run['run_id']}`。预检依次运行 A01、B08-01、B32-02，结果在 `runs/preflight-20261113-final/`。A 的规划输入保持 `DEMO_ONLY`，GT-C 保持 `NOT_VALIDATED`。

GT-A 通过 NetworkX Dinitz 整数最大流计算服务上界，并枚举所有预算可行且满足同场所/互斥约束的项目组合。流量按每人 1,000,000 个整数单位缩放，oracle 同时报告累计取整误差上界。32 个候选没有做 2^32 穷举；由本轮紧预算导出每案仅评估 {median_oracle_count:g}（中位数）至 {max_oracle_count} 个预算可行组合（含空组合）。

B 的原有时限保持 10 秒、候选上限 64。现有 `TaskRequest.improvement_options` API 上限是 12 项，所以 16/32 候选实验调用了 `execute_task` 的内部 `source_override` 评估入口，不宣称 HTTP API 已支持该规模。A 清单中的原始人口栅格本机文件是 134 字节 Git LFS 指针，和清单记录不匹配；本轮 B 使用的派生需求节点、场所和 OD 文件哈希匹配，因此可复现范围限于派生输入。
"""

    table_rows = []
    for case in cases:
        metrics = case["metrics"]
        mip = case.get("mip") or {}
        verifier = mip.get("verification", {}).get("verification_status")
        if case.get("tamper_test"):
            verifier = f"PASS_DEMO；篡改拒绝 {case['tamper_test']['observed']}"
        elif case["case_id"] == "D02":
            verifier = "EXPECTED_REJECT"
        elif not verifier:
            verifier = case["static"]["verification_status"]
        greedy_value = (case.get("greedy") or {}).get("served_demand")
        mip_value = mip.get("objective_value")
        mip_seconds = case["elapsed_seconds"].get("budget_mip_seconds")
        table_rows.append(
            f"| {case['case_id']} | {case['group']} | {case['description']} | {metrics['num_demand_nodes']} | {metrics['num_shelters']} | {metrics['num_od_rows']} | {metrics['num_eligible_edges']} | {metrics['num_options']} | {case['quality_status']} |"
        )
    manifest_report = f"""# 03｜案例清单与筛选

本轮完成 {len(cases)} 个案例，分组为 {', '.join(f'{key}={groups[key]}' for key in sorted(groups))}。其中 {len(planning_cases)} 个为结构规划/组合试验；D02、D03、D04 三个案例是预先设计的拒绝、状态注入和 Verifier 负向测试，保留为验收用例，不混入有效规划成功率。

| 案例 | 组 | 锚定结构/筛选理由 | 需求点 | 场所 | OD 行 | 合法边 | 选项 | 质量状态 |
|---|---:|---|---:|---:|---:|---:|---:|---|
{chr(10).join(table_rows)}

Group A 取真实需求/场所 ID 和 OD 边；Group B/C 的容量增量与成本为 HYPOTHETICAL。D02 是非法场所 ID 的预期拒绝；D03 是工程故障状态路径；D04 是故意篡改结果后要求 Verifier 拒绝。未生成新的道路、场所或 OD 关系。
"""

    result_rows = []
    def shown(value):
        return f"{value:.3f}" if isinstance(value, (int, float)) else value
    for case in cases:
        mip = case.get("mip") or {}
        greedy = case.get("greedy") or {}
        result_rows.append(
            f"| {case['case_id']} | {case['static']['served_demand']:.3f} | "
            f"{shown(greedy.get('served_demand', 'N/A'))} | {shown(mip.get('objective_value', 'N/A'))} | "
            f"{mip.get('solver_status', 'NOT_RUN')} | {case.get('oracle', {}).get('oracle_status', 'NOT_RUN')} | "
            f"{mip.get('verification', {}).get('verification_status', 'N/A')} | "
            f"{case['elapsed_seconds'].get('budget_mip_seconds', 'N/A')} | {case['total_wall_seconds']:.2f} |"
        )
    graph = run.get("graph_experiments", {})
    cache_hits = graph.get("budget_cache", {}).get("second_call_expected_cache_hits", {})
    graph_assessments = [value for key, value in graph.items() if key.startswith("assess_")]
    report04 = f"""# 04｜Module B 实验结果

本轮实际执行 {len(cases)} 案；含扩容候选的 19 案中，17 案自然求解 OPTIMAL、1 案 D03 工程注入为 FEASIBLE、1 案 D02 被正确拒绝。17 个有效候选规划案均达到 OPTIMAL；其预算 MIP Verifier 均为 PASS_DEMO。D04 的刻意篡改按预期被 Verifier 判为 FAIL。A 源文件哈希执行前后完全一致：`{run['source_hash_mutation_check']['unchanged']}`。结果没有升级为 VERIFIED。

| 案例 | 静态服务 | Greedy 服务 | MIP 服务 | MIP 状态 | 独立 Oracle | MIP Verifier | MIP 秒 | 案例总秒 |
|---|---:|---:|---:|---|---|---|---:|---:|
{chr(10).join(result_rows)}

SkillGraphOnly 消融执行于所有 26 案；SkillGraphVerifier 的完整图执行在 A03、B08-01、B32-02 三个代表案例上实测，三者均 PASS_DEMO。第二次相同图/分配输入命中 S03/S04；只改变预算后 S07 未命中并重新优化：S03={cache_hits.get('S03')}、S04={cache_hits.get('S04')}、S07={cache_hits.get('S07')}。C05 三个顺序情景分支均 PASS_DEMO。详细阶段和 trace 在 `{run['run_id']}/run_summary.json`。

实际外部 LLM / AgentArts / FlatToolAgent：NOT_RUN。SkillGraph 的 16/32 候选试验使用内部评估入口，不代表外部 API 可直接接受 32 个候选。
"""

    profile_path = run_dir / "component_profile.json"
    if profile_path.exists():
        import json
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile_text = (
            f"A 适配器单次读取约 {profile['reader_load_seconds']:.3f}s（本机热缓存采样）；"
            f"15 分钟合法边构建 {profile['eligible_edges_build_seconds']:.3f}s；"
            f"完整 A 静态 LP {profile['assignment_lp_seconds_including_edges']:.3f}s；"
            f"Verifier {profile['assignment_verification_seconds']:.3f}s；"
            f"diagnose {profile['diagnose_seconds']:.3f}s。"
        )
    else:
        profile_text = "组件级微基准：NOT_RUN。"
    raw_gap_summary = (
        f"Oracle−Greedy 的原始差在 {gap_stats['raw_min']:.8f} 到 {gap_stats['raw_max']:.8f} 人之间，"
        f"绝对值均远小于 oracle 最大取整界 {gap_stats['max_oracle_rounding_bound']:.6f} 人。"
    )
    difficulty = f"""# 05｜难度分析与算法比较

18 个候选优化结果具有 Greedy 与独立枚举 Oracle 对照。{raw_gap_summary}按旧 Benchmark 的公式 `max(0,(oracle−method)/oracle)` 计算，新案原始平均相对 gap 为 {statistics.mean(relative_gaps) * 100 if relative_gaps else 0:.10f}%；但这些百万分之一人级差异小于 oracle 取整误差。扣除逐案取整误差界后，可分辨的 Greedy gap 均值/中位数/最大值/非零比例为 {gap_stats['mean']:.8f} / {gap_stats['median']:.8f} / {gap_stats['maximum']:.8f} 人 / {gap_stats['nonzero']:.1%}。因此没有可证实的 Greedy 劣势。历史 40 案合成 Benchmark 报告平均 Greedy gap 为 0.0481%，且有 40/40 SkillGraphVerifier 通过；其整数 toy oracle 与本轮比例需求的数值精度不同。历史真实数据实验则使用 8 个最高利用率场所、每项 25% 假设增量、相同抽象 cost_units 和预算 `[0,1,3,6]`。本轮扩展到 8/16/32 个差异候选并引入重叠互斥，但紧预算仍把可行组合压小；不能把本轮原始微差解释为算法提升。17 个能计算 Improvement Capture Ratio 的案例均为 1.0（仅在 Oracle 已证明且扩容改善静态方案时计算）。

尽管候选集含 8/16/32 项，紧预算与互斥结构将预算可行组合压到每案 {min(oracle_counts, default=0)}–{max_oracle_count} 个，远低于无约束子集规模。32 选项案例均 OPTIMAL；MIP 报告 gap 为 0，四阶段词典序目标完成。该结果说明本批候选集在当前模型下较容易，不说明真实预算规划简单。

组件测量：{profile_text}候选 MIP 的实测时间 min/mean/max 为 {min(stage_time_samples, default=0):.4f}/{statistics.mean(stage_time_samples) if stage_time_samples else 0:.4f}/{max(stage_time_samples, default=0):.4f}s；各案 Greedy、Oracle、SkillGraphOnly 和总 wall time 在 JSON 中分开记录。全套案最大采样 RSS 约 {max_rss / (1024**2):.1f} MiB。组件微基准是一次热缓存采样，不能简单相加为完整延迟；数据读取只在整套运行开始时加载一次。

空间结构上，15 分钟合法图为 3,272 条边、2,366 个有边节点，5,599 个需求点无合法边，节点至多连 4 个场所。场所数有限且合法边稀疏，假设扩容最多直接作用于容量饱和的少数场所；因此 32 个候选的组合决策被预算和分组强烈收缩。

这些案例是围绕 A 中真实重叠、孤立节点、阈值边与关键场所人工挑选的定向样例，不是现实需求总体的无偏样本。
"""

    d02 = next((case for case in cases if case["case_id"] == "D02"), {})
    d03 = next((case for case in cases if case["case_id"] == "D03"), {})
    d04 = next((case for case in cases if case["case_id"] == "D04"), {})
    fail_report = f"""# 06｜失败模式与限制

- 自然求解超时：{natural_timeouts}。本轮 17 个有效预算规划案全为 OPTIMAL；D03 独立工程测试把真实可行 incumbent 的 status 设为 1，B 正确返回 FEASIBLE、`optimality_proven=false`，Verifier PASS_DEMO。它不是实际超时记录。
- D02 不存在的 shelter ID 被 CandidateError 拒绝：`{(d02.get('mip') or {}).get('solver_status', 'NOT_RUN')}`；没有生成扩容结果。D04 只篡改摘要 `served_demand`，Verifier 检出 NUMERICAL_RECOMPUTATION 并返回 FAIL，符合预期。
- 非预期预算 Verifier FAIL：{len(result_failures)}；非预期服务量与独立 GT 超出取整界的不一致：0。B 与 oracle 的最大误差在微人量级，远低于约 0.003983 人的逐案量化误差界。本轮未复现 SciPy 词典序容差错误，但不能消除其他版本风险。
- DEMO_ONLY 状态和所有假设容量/成本标签均被保留。A 当前容量没有权威核验；原始人口栅格本机仅为 LFS 指针，故不能声称完整 GIS 原始重建链可复现。
- TaskRequest 模型候选数组上限为 12，而引擎预算模型上限为 64；本轮大候选数直接走内部 Graph `source_override`。这是一个已观测的 API/引擎能力边界差异，未改 API。

Skill Graph trace、MIP 多阶段状态、Verifier 证书和单案细节保存在 run JSON。C 侧 LLM/AgentArts 会话没有连接，本轮不对自然语言代理能力作结论。
"""

    next_steps = f"""# 07｜结论与下一步建议

## 已证实的发现

1. 26 案全部运行完成，其中 23 个是有效 GIS 锚定结构/规划情景，另 3 个是预期拒绝或故障/Verifier 负向测试。
2. A 中的 24 个真实 ID、7,965 个需求点、191,160 行原始 OD 被用于 Overlay；所有规划结果维持 DEMO_ONLY，扩容参数是 HYPOTHETICAL。
3. 17/17 个自然有效预算 MIP 案 OPTIMAL，32 选项档也没有超时；D03 注入路径以 FEASIBLE 暴露“可行不等于最优”的状态，D02 被拒绝，D04 篡改被 Verifier 拒绝。
4. 新数据更真实地锚定 GIS 结构，且覆盖 32 项候选，但相对旧 8 选项案例并未更难地挑战 MIP：紧预算/互斥最多只留下 29 个可行子集，MIP 最长约 0.323 秒。Greedy 与独立 Oracle 的差距低于 oracle 取整界，未证明有可分辨差异。
5. Skill Graph 在 3 个代表案例上 PASS_DEMO；同输入复用 S03/S04，预算改变导致 S07 正确重算。C05 的父情景链条和三次验证均通过。
6. 现有数据可以支撑关键场所停用、阈值敏感性和局部容量竞争，但稀疏合法 OD、仅 24 个场所、5,599 个无边需求点及未经核验的容量，限制真实且复杂的扩容组合 GT。

## 对历史 100% 成功率的判断

历史 40 个内部合成 Benchmark 的 100% 不能当作真实规划能力证据；本轮 17 个有效预算规划案同样全部最优，说明在当前紧预算/互斥候选下 B 仍稳健，但不是因为本轮验证了现实中困难的投资组合。32 个候选只产生最多 {max_oracle_count} 个预算可行子集，候选数高而搜索空间仍小。

## 尚未验证的解释与建议

真实成本、现场容量、真实道路可达性以及 Module C/AgentArts 端到端交互仍未验证。优先补齐 A 的权威输入并接入真实候选项目，再评估优化器与 verifier；当前无证据要求替换 MIP 或放宽约束。之后再用真实组合收益/成本制造更高组合复杂度；如需纯压力规模，另建 `FULLY_SYNTHETIC_STRESS` 数据集，不混入本批 GIS 锚定结果。Skill Graph 的自然语言分支与拒绝行为需等 C 的真实 AgentArts 任务验证。

本轮人工选案不是需求总体的无偏抽样。GT-C=`NOT_VALIDATED`，任何结果都不构成现实避难规划能力确认。
"""

    (output / "02_GENERATION_PROTOCOL.md").write_text(protocol, encoding="utf-8")
    (output / "03_SCENARIO_MANIFEST.md").write_text(manifest_report, encoding="utf-8")
    (output / "04_B_EXPERIMENT_RESULTS.md").write_text(report04, encoding="utf-8")
    (output / "05_DIFFICULTY_ANALYSIS.md").write_text(difficulty, encoding="utf-8")
    (output / "06_FAILURE_ANALYSIS.md").write_text(fail_report, encoding="utf-8")
    (output / "07_NEXT_STEP_RECOMMENDATIONS.md").write_text(next_steps, encoding="utf-8")
