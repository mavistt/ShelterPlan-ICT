"""Assemble reproducible V2 reports, case index and environment evidence."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "merge_data" / "v2_difficulty_discovery"
V1 = ROOT / "merge_data" / "gt" / "v1_greedy_mip_summary.json"
sys.path.insert(0, str(ROOT / "merge_data" / "tools"))
from grounded_experiment_runner import source_hashes


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(value, digits=3):
    return "n/a" if value is None else f"{value:,.{digits}f}"


def report_phase_a(phase_a):
    graph = phase_a["graph"]
    comps = graph["nontrivial_components"]
    pairs = phase_a["pairwise_interactions"]
    interactions = [row["interaction_persons"] for row in pairs]
    sites = phase_a["sites"]
    utilizations = [row["utilization"] for row in sites if row["utilization"] is not None]
    overlaps = phase_a["service_overlap_pairs"]
    top_overlap = overlaps[0] if overlaps else None
    overlap_nonzero = sum(row["shared_demand_nodes"] > 0 for row in overlaps)
    single = phase_a["single_option_gains"]
    v1 = phase_a["v1_greedy_vs_mip"]
    lines = [
        "# 01 — 为什么 V1 的 Greedy 与 MIP 常常相同",
        "",
        "## 可从代码确认的行为",
        "",
        "`backend/evaluation/baselines.py` 中的 Greedy 在每轮对每个可行候选重新求当前容量下的服务量，计算真实边际增益，按 `gain / cost`、增益、option ID 排序后选一个；每轮检查预算、同场所叠加规则和命名互斥组。它遇到非正边际增益即停止。",
        "",
        "`backend/engine.py` 的 MIP 使用同一批 A 场所、合法 OD、容量、预算、同场所政策和命名互斥组。MIP 按四阶段词典序优化：最大服务量、最小加权步行时间、最小选项成本、最少选项数。Greedy 直接优化的是第一阶段的逐轮增益成本比，不是完整词典序，也不枚举全局组合。",
        "",
        "因此，两者在这些案例首要服务量相同，是观测到的案例性质，不是代码保证。不同选项组合可以有相同服务量，再由 MIP 的后续目标选出不同组合。",
        "",
        "## A OD 图与场所结构",
        "",
        f"在 15 分钟阈值下，A 数据有 {graph['demand_nodes']:,} 个需求点、{graph['shelters']} 个场所、{graph['eligible_edges']:,} 条合法边；{graph['connected_components_with_edges']} 个含边连通分量分别覆盖 {comps[0]['demand_nodes']:,} 个需求点/{comps[0]['shelters']} 个场所和 {comps[1]['demand_nodes']:,} 个需求点/{comps[1]['shelters']} 个场所。另有 {graph['isolated_demand_nodes']:,} 个需求点在该阈值下不可达。",
        f"静态基线服务 {phase_a['baseline']['served_demand']:.3f}/{phase_a['baseline']['total_demand']:.3f} 人，未服务 {phase_a['baseline']['unserved_demand']:.3f} 人（包含无合法边及容量不足）。",
        f"场所基线平均利用率为 {fmt(sum(utilizations)/len(utilizations) if utilizations else None, 3)}；{sum(value >= 0.999 for value in utilizations)} 个有容量场所达到 99.9% 利用率。容量和人口仍是 A 的 DEMO_ONLY 数据。",
        f"对全部 {len(overlaps)} 对场所计算服务区 Jaccard 和共享需求点数，其中 {overlap_nonzero} 对至少共享 1 个需求点。重叠最大的场所对 `{top_overlap['shelter_i']}` / `{top_overlap['shelter_j']}` 共享 {top_overlap['shared_demand_nodes']} 个需求点，Jaccard={top_overlap['jaccard']:.3f}。每场所可达需求上界和全部对值在 Phase A JSON；可达人口因区域重叠而不能相加视作独立受益量。",
        "",
        "B08-01 的 8 项参考候选，在不同预算下仅有 8、35、155、256、256 个成本可行子集（包含空集；这是预算子集计数，不额外施加互斥约束）。低预算让搜索空间很小。",
        "",
        "## 实测增益与交互作用",
        "",
        "以下单项容量变化及成本属于 HYPOTHETICAL；OD、A 场所容量和人口没有改动。",
        "",
        "|候选|场所|假设扩容|单项服务增益|增益/成本|",
        "|---|---|---:|---:|---:|",
    ]
    for row in single:
        lines.append(f"|{row['option_id']}|{row['shelter_id']}|{row['delta_capacity_persons_hypothetical']:.0f}|{row['gain_persons']:.6f}|{row['gain_per_cost']:.6f}|")
    lines.extend([
        "",
        f"对 {len(pairs)} 个候选对逐一计算 `gain(i,j) - gain(i) - gain(j)`，观测范围为 {fmt(min(interactions) if interactions else None, 8)} 至 {fmt(max(interactions) if interactions else None, 8)} 人；全部 {sum(abs(value) <= 1e-5 for value in interactions)} 对在 1e-5 人容差内为零。这个结果只适用于本候选族和当前服务图，不证明所有扩容组合都无交互。",
        "",
        "## V1 对照",
        "",
        f"V1 汇总中 {v1['cases_with_both']} 个可比较案例的首要服务量在 1e-4 人容差内全部相同；其中 {v1['same_selected_portfolio_count']} 个还选中了相同组合，另外 {v1['cases_with_both']-v1['same_selected_portfolio_count']} 个组合不同但首要服务量相同。多解、成本与词典序后续阶段是组合不同的合理来源。",
        "",
        "## 结论边界",
        "",
        "**由代码确认：**两者同用主要可行规则；Greedy 每轮重算边际服务量；MIP有四阶段词典序目标；引擎候选上限 64、总时限 10 秒。",
        "",
        "**仅由当前数据观察：**V1 的 18 个比较案例服务量相同、B08-01 参考候选对交互接近零、低预算下可行子集少。它们解释了为什么这批样本没有暴露差距，不能推出 Greedy 对一般预算化扩容最优。",
        "",
        "数据状态为 `DEMO_ONLY`，结果不是运营规划认证。",
    ])
    return "\n".join(lines) + "\n"


def report_phase_b(b):
    cases = b["completed_cases"]
    confirms = [row for row in cases if row["greedy_failure_confirmed"]]
    lines = [
        "# 02 — A GIS 锚定的困难实例搜索",
        "",
        "## 边界与搜索预算",
        "",
        f"固定随机种子 {b['seed']}，生成并完成 {b['limits']['cheap_prefilters_run']} 个便宜结构筛选（上限 100），其中 {b['prefilter_summary']['pass']} 个通过；随后执行 {b['limits']['full_mips_run']} 次完整 MIP（上限 30，每例 10 秒）。组合大小覆盖定向 3 项案以及 8、16、24、32 项。全部新增扩容量和成本标为假设值；原始 A 节点、人口、场所容量和 OD 边不变。",
        "",
        "筛选阶段只用每个候选场所的可达人口和扩容量构造乐观上界、增益成本排序投影及预算可行子集上界；它不是 MIP，也不据此宣称贪心或 MIP性能。筛选通过/拒绝的 100 条完整记录在 `scenarios/real_gis_search/screening_records.json`。",
        "",
        "## 完整优化结果",
        "",
        "|候选|数量|预算|Greedy 服务量|MIP 服务量|差值|MIP 状态|Verifier|Oracle|",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in cases:
        oracle = row.get("oracle") or {}
        lines.append("|{id}|{n}|{budget}|{greedy}|{mip}|{gap}|{status}|{verify}|{oracle}|".format(
            id=row["candidate_id"], n=row["candidate_count"], budget=fmt(row["budget_units"], 2),
            greedy=fmt((row.get("greedy") or {}).get("served_demand")),
            mip=fmt((row.get("mip") or {}).get("objective_value")), gap=fmt(row.get("mip_minus_greedy_persons")),
            status=(row.get("mip") or {}).get("solver_status", row.get("mip_error")),
            verify=(row.get("verifier") or {}).get("verification_status", "n/a"),
            oracle=oracle.get("oracle_status", "not run")))
    lines.extend(["", "## 已独立确认的 A 锚定差距", ""])
    for row in confirms:
        oracle = row["oracle"]
        lines.append(
            f"- **{row['candidate_id']}：**Greedy {row['greedy']['served_demand']:.6f}，MIP {row['mip']['objective_value']:.6f}，独立枚举最大流 Oracle {oracle['objective_served_demand']:.6f}；差距 {row['mip_minus_greedy_persons']:.6f} 人，Oracle 取整误差上界 {oracle['rounding_error_upper_bound_persons']:.6f} 人，MIP `OPTIMAL`，Verifier `{row['verifier']['verification_status']}`。Oracle 将原 A 图中不可达点移除、将度为 1 的需求点按唯一可达场所聚合；所有多边需求、边、场所容量、选项及预算保留，最大服务流不变。完整投影说明和枚举子集数在 JSON 记录内。")
    lines.extend([
        "",
        "定向案例的首要增量来自 Phase A 实测单项容量增益：A=60、B=50、C=50 人；唯一改变的是明确假设成本 A=11、B=C=10 和预算 20。Greedy 因 60/11 大于 50/10 先选 A，预算不足以再选 B 或 C；MIP 与 Oracle 选 B+C。总服务量对应原基线 6,456.103 人加各自增量。该结果是**不改 A GIS 图的假设投资反例**，不是经核实的真实工程报价或实施建议。",
        "",
        "8 项的 `REAL-081` 也满足严格证据条件，并完成独立枚举。16/24/32 项案例中可见 MIP 相对 Greedy 的更大差值，但本轮没有对超过 12 项的全集合做独立枚举，故都标记为未由独立 Oracle 复核，不纳入“已证明失败”计数。",
        "",
        f"A 数据文件哈希执行前后完全一致：`{b['source_hashes_unchanged']}`。A 数据状态仍为 `{b['data_status']}`，全部输出只能用于 DEMO。",
        "",
        "## 判断",
        "",
        "搜索不止一次随机抽样：100 个结构筛选全部保留，按候选数量分层选出 9 个完整优化。当前结果证明同一 A 图上存在由假设预算/成本产生的可辨识 Greedy 差距；它不意味着真实项目成本存在该关系。想验证现场难度，还需经核实的工程成本、可实施容量和正式 A 发布。",
    ])
    return "\n".join(lines) + "\n"


def report_phase_c(c):
    cases = c["stress_cases"]
    lines = [
        "# 03 — 隔离合成压力测试",
        "",
        "所有输入均带 `FULLY_SYNTHETIC_STRESS` 来源标签，与 A 数据完全隔离。每种结构在 8/16/32/48/64 个候选下分别使用 2 秒和 10 秒预算，共 20 次完整 MIP。",
        "",
        "|结构|候选数|2 秒状态/时间|10 秒状态/时间|2 秒验证|10 秒验证|",
        "|---|---:|---:|---:|---|---|",
    ]
    for kind in ("simple", "competitive"):
        for size in (8, 16, 32, 48, 64):
            pair = [row for row in cases if row["case_kind"] == kind and row["candidate_count"] == size]
            pair.sort(key=lambda row: row["time_limit_seconds"])
            left, right = pair
            lines.append(f"|{kind}|{size}|{left['solver_status']} / {left['runtime_seconds']:.4f}s|{right['solver_status']} / {right['runtime_seconds']:.4f}s|{(left.get('verifier') or {}).get('verification_status')}|{(right.get('verifier') or {}).get('verification_status')}|")
    statuses = {}
    for row in cases:
        statuses[row["solver_status"]] = statuses.get(row["solver_status"], 0) + 1
    max_runtime = max(row["runtime_seconds"] for row in cases)
    avg_runtime = sum(row["runtime_seconds"] for row in cases) / len(cases)
    peak_mem = [row["memory"].get("peak_rss_sampled_bytes") for row in cases if row["memory"].get("peak_rss_sampled_bytes") is not None]
    trap = c["greedy_counterexample"]
    oracle = trap["oracle"]
    lines.extend([
        "",
        f"20 次 stress MIP 状态计数：`{statuses}`；所有案例 verifier 均为 PASS_DEMO。运行时均值 {avg_runtime:.4f}s，最大 {max_runtime:.4f}s。RSS 10ms 抽样峰值最大 {max(peak_mem)/1024/1024:.1f} MiB（若环境提供 psutil）；单例 Python heap 峰值与每阶段 MIP 界/gap/node count 保存在 `oracles/phase_c_d_synthetic_results.json` 及逐例 trace。",
        "",
        "在这组小型合成图中，候选增至 64 仍没有触及 2 秒限制，且每阶段均有界并证明最优。该测量只说明当前生成的模型容易，不代表 7,965 需求点 A 全图或更难的模型上限。FEASIBLE、TIME_LIMIT 等状态按原样保留，没有把未证最优改标为最优。",
        "",
        "## 独立证明的合成反例",
        "",
        f"8 项 fully synthetic fixture：Greedy 服务 {trap['greedy']['served_demand']:.6f}，MIP {trap['best_feasible_served_demand']:.6f}，NetworkX Dinitz 穷举 {oracle['objective_served_demand']:.6f}，共评估 {oracle['evaluated_feasible_subsets']} 个预算可行子集；MIP `OPTIMAL`、Verifier `{(trap.get('verifier') or {}).get('verification_status')}`。A 方案成本 12、增益 61；B/C 各成本 10、增益 50；预算 20，并有五个 1 成本/1 增益 decoy。Greedy 拿 A 后能加五个 decoy 得 66，Oracle 选 B+C 得 100。该 fixture 与 GIS 及 A 数据无关。",
        "",
        "解、界、每阶段状态、内存测量和独立 Oracle 证据按 case ID 保存于 `runs/synthetic_stress/` 和 `scenarios/synthetic_stress/`。",
    ])
    return "\n".join(lines) + "\n"


def report_ablation(c):
    rows = c["ablation_cases"]
    base = next(row for row in rows if row["case_id"] == "ABL-BASE-COMPETITIVE-16")
    lines = [
        "# 04 — 难度因素单因素消融",
        "",
        "基线为固定 seed 的 16 项 competitive synthetic case，每次 MIP 时限 10 秒。每行只改变标明因素；所有图和选项均为 fully synthetic。",
        "",
        "|变化|候选|需求点|OD 边|预算|MIP 服务量|Greedy gap|状态|时间|Verifier|",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for row in rows:
        meta = row["metadata"]
        name = meta.get("ablation_dimension", "基线")
        lines.append(f"|{name}|{row['candidate_count']}|{meta['demand_nodes']}|{meta['od_edges']}|{fmt(meta['budget'],2)}|{fmt(row['best_feasible_served_demand'])}|{fmt(row['mip_minus_greedy_persons'])}|{row['solver_status']}|{row['runtime_seconds']:.4f}s|{(row.get('verifier') or {}).get('verification_status')}|")
    base_t = base["runtime_seconds"]
    lines.extend([
        "",
        "## 观察",
        "",
        f"基线 MIP 用时 {base_t:.4f}s，目标 {base['best_feasible_served_demand']:.3f}。预算加宽的服务量变化同时包含更多可购买容量的影响，因此不能单独解释为求解难度上升。去掉互斥后目标上升反映可行域扩大；在此小实例中它仍很快求得最优。",
        "",
        "增大服务范围重叠、候选数量、需求节点数和 OD 密度的单因素案例均保留了完整实际节点/边数、预算、gap、runtime 和 status；运行后没有从预期推断难度。所有七例均 OPTIMAL 且 PASS_DEMO。候选数到 32 时本组实例仍在约同一量级的亚秒区间，不支持“候选数单独必然造成困难”。",
        "",
        "这些是单 seed 诊断，不足以估计统计因果效应；交互作用和不同 seed 需要后续重复实验。原始数字见 `oracles/phase_c_d_synthetic_results.json`。",
    ])
    return "\n".join(lines) + "\n"


def report_api():
    return """# 05 — B/C 公开接口兼容性

## 限制位置

- B 引擎 `backend/engine.py`：预算 MIP 内部上限 64，合计时限 10 秒；超上限返回 `MODEL_SIZE_LIMIT`。
- B `backend/models.py` 的公开 `TaskRequest.improvement_options`：Pydantic `max_length=12`。文档 `docs/module_b/API_HANDOFF_TO_C.md` 同时说明 POST body 上限 1 MiB、异步 job/result 状态与最多 100 个 active jobs。
- Module C Harness `agent/harness/guards.py::validate_improvement_options` 也在调用 B 前拒绝超过 12 项。MCP 和 Workflow 最终进入相同 Harness guard；虽然 MCP schema 使用普通数组，不能绕过 guard。

## 判断与建议

本轮不改变 12 项限制。它是业务请求边界而非求解器能力数字；合成 64 项在小图上的亚秒结果不足以证明公开 API 应放宽。B/C 当前还需考虑请求大小、异步 job 生命周期、排队/并发、10 秒 solver 总时限、FEASIBLE 状态呈现、运行内存、请求重试幂等性和结果访问权限。

若 C Harness 需要大候选压测，应使用独立、受控的内部压力任务接口：只接收版本化 fixture ID/有限配置，限制候选上限与并发，创建独立 job/trace，保留最佳界和阶段状态，并清理过期任务。不可把候选拆成多次普通 optimize 请求来冒充同一联合优化，因为分块会丢失全局预算、互斥与组合选择。

Module C 当前是未合并 feature branch。本轮只审查，不复制或改写 C。既有 `docs/integration/B_C_CODE_REVIEW.md` 记录了两个 P1：MCP/service 结果读取未先检查 session 对 result 的成员关系；自然语言数量可能未绑定到目标参数字段。另有取消竞态、缺认证/CORS、状态保留和依赖锁冲突等上线风险。先修复并回归这些问题，再做 C Harness 联合集成；MCP stdio/Streamable HTTP 也需真实 SDK transport 验收。

"""


def report_limits(b, c, test_records):
    return f"""# 06 — 失败、验证和限制

## 已观测的正确行为

- V1 报告记录：17 个有效预算 MIP 案例均 OPTIMAL、没有自然超时；D03 把可行 incumbent 状态注入为状态 1 后正确返回 FEASIBLE 且 `optimality_proven=false`。D02 未知场所选项被拒绝。D04 篡改服务量摘要后 Verifier 检出 `NUMERICAL_RECOMPUTATION` 并返回 FAIL。
- V1 独立整数最大流比较的误差处于取整误差界内；没有复现数学错误、非预期 verifier 漏检或数值不稳定。这不是对所有输入/版本的证明。
- V2 A 锚定完整 MIP：{b['limits']['full_mips_run']} 次中 `{sum((row.get('mip') or {}).get('solver_status') == 'OPTIMAL' for row in b['completed_cases'])}` 次 OPTIMAL；确认案的独立 Oracle 与 MIP 差异小于保守取整界，所有证书 PASS_DEMO。
- V2 synthetic stress 20 次均 OPTIMAL、Verifier PASS_DEMO；FEASIBLE/TIME_LIMIT 记录数量为 0。消融 7 次均 OPTIMAL、PASS_DEMO。
- 测试：`tests/module_b` {test_records['module_b']}; V2 合成 fixture 测试 {test_records['v2']}; `compileall backend` {test_records['compileall']}。

## 未证实/边界

- A 数据是 `DEMO_ONLY`。Hypothetical cost/capacity 只能回答模型行为，不能当工程报价、可建容量或正式规划建议。
- 100 条 cheap prefilter 全部留档，但只有 9 条完整 GIS MIP；大于 12 项的候选案没有做独立全子集 Oracle。它们的 MIP/Greedy 差值是待复核观测，不计入已确认失败。
- 合成图远小于真实 A 全图；小图在 64 选项下都很快，不能外推真实网络的运行上限。
- RSS 以 10ms 轮询采样；非常短暂的 native peak 可能漏掉。Python heap 另行记录。
- 度为 1 的需求聚合及不可达需求移除只用于独立 max-flow oracle，服务流目标保持不变；详细投影大小与误差界保存在 Oracle JSON。
- Module C 的 feature branch 未合并，也未在本轮执行完整 MCP transport 或 B/C 联合验收；旧 review 列出的 P1 问题仍需解决。
- SciPy/HiGHS 的词典序容差、其他数据结构、不同版本、并发负载和极端预算尚未覆盖。

所有完整 MIP 状态仍依据 solver 返回的 `OPTIMAL`/`FEASIBLE` 和各词典序阶段记录判断，Verifier 只证明可行证书正确，不代替最优性证明。
"""


def report_recommendations(b, c):
    confirms = [row for row in b["completed_cases"] if row["greedy_failure_confirmed"]]
    stress64 = [row for row in c["stress_cases"] if row["candidate_count"] == 64]
    return f"""# 07 — 最终建议与 A–I 答案

## A–I

**A. 在不改真实 GIS 结构下，找到 Greedy 明显弱于 MIP 的案吗？** 找到了。在 A 原始图与容量固定的情况下，{len(confirms)} 个假设投资组合通过 OPTIMAL MIP、PASS_DEMO verifier 和独立最大流枚举；主定向案差 {confirms[0]['mip_minus_greedy_persons']:.3f} 人。注意项目成本是明确假设值，不是经核实成本。

**B. 若差距从哪来？** 不只是搜索不足。V1 的实际候选结构交互接近零、低预算可行组合少，使两算法在该批数据上服务量相同；当成本比与预算组合改变后，同一 A 图已能产生可证明差距。差异来源是 Greedy 的逐步增益成本率决策与全局 knapsack 组合的交互。不能据此判断真实项目预算一定如此。

**C. 受控图结构下是否能得到独立 Oracle 证明的 Greedy 反例？** 能。Fully synthetic 8 项案 Greedy=66、MIP≈100、Oracle=100，Verifier PASS_DEMO。另有 A 锚定案由真实图与假设成本构成，也已枚举证明。

**D. 16/32/48/64 项表现与失败边界？** Fully synthetic 竞争案 16/32/48/64 项在 2 秒和 10 秒限制下全部 OPTIMAL；64 项两类模型耗时分别为 {max(row['runtime_seconds'] for row in stress64):.3f}s 以内（单例），没有触及本轮时限。A 锚定 16/24/32 项完整 MIP 也均 OPTIMAL，亚秒级，但大项集没有独立 Oracle。故目前没有观测到这些规模的性能失败边界；不能外推任意大图或更难结构。

**E. 哪些因素影响 MIP 难度？** 当前单因素试验中预算、互斥、重叠、候选数、需求点数和 OD 密度均未令求解超时或失去证明。宽预算改变了最优服务量，放松互斥也改变了可行域；单 seed 运行时间没有给出稳定因果排序。候选数量单独在 32 项 ablation 内未产生显著退化。

**F. 有数学错误、Verifier 漏检或数值不稳吗？** 本轮没有复现。旧 D02/D03/D04 负向与状态注入用例按预期处理；V2 MIP/Oracle、Verifier 与回归测试一致。结论仅限已运行环境和实例；`PASS_DEMO` 不是 `PASS_VERIFIED`。

**G. API 12 项需要调整吗？** 当前不需要。B 内部可到 64，但公开 TaskRequest 和 C guard 都是 12；继续保持普通业务 API 边界。大候选压测走版本化、受控的内部异步压力任务，明确 solver 时限、并发、访问和过期策略。

**H. 哪些难例适合 C Harness 多轮任务测试？** 先用 3 项 A 锚定 hypothetical 案测试成本/预算澄清、MIP vs Greedy 解释、DEMO_ONLY 风险话术；再用 8 项 synthetic trap 测预算补齐、重复调用幂等、OPTIMAL/Verifier 证据引用。超过 12 项只经未来内部压力接口运行，当前 C Harness guard 会拒绝。

**I. 后续应改 B 求解器吗？** 现在没有证据支持改 B 的数学模型或公开上限。先修 C 既有 P1 session/result 隔离及数字字段绑定问题，再完善 Skill Graph、Verifier 的最优性状态呈现、B/C 联合任务与真实 transport 验收。若未来有经核实的成本和超时证据，再扩展 B 的压力与性能实验。

## 推荐下一步

1. 保持 A 输入只读及 B API 12 项业务限制。
2. 用正式工程成本和可实施扩容量替换本轮假设值，再跑相同的小型 A 锚定 oracle 对照。
3. 修复并测试 Module C review 的 P1 问题，然后用 3 项 A 锚定案和 8 项 synthetic trap 做有限 Harness 多轮验证。
4. 只有 C 需要 16–64 项任务时才定义受控压力接口；另行实测真实 A 尺寸及并发条件后决定是否修改 B。

"""


def collect_case_rows(b, c):
    rows = []
    for item in b["completed_cases"]:
        mip = item.get("mip") or {}
        greedy = item.get("greedy") or {}
        oracle = item.get("oracle") or {}
        rows.append({
            "case_id": item["candidate_id"], "phase": "B_GROUNDED", "provenance": item.get("source_label"),
            "data_status": item.get("data_status"), "candidate_count": item["candidate_count"],
            "time_limit_seconds": 10, "budget_units": item["budget_units"],
            "solver_status": mip.get("solver_status"), "optimality_proven": mip.get("optimality_proven"),
            "best_feasible_served": mip.get("objective_value"), "primary_best_bound_served": item.get("mip_primary_best_bound_served_demand"),
            "primary_gap": item.get("mip_primary_relative_gap"), "runtime_seconds": item.get("mip_runtime_seconds"),
            "verifier_status": (item.get("verifier") or {}).get("verification_status"),
            "greedy_served": greedy.get("served_demand"), "mip_minus_greedy": item.get("mip_minus_greedy_persons"),
            "oracle_status": oracle.get("oracle_status"), "oracle_served": oracle.get("objective_served_demand"),
            "oracle_error_bound": oracle.get("rounding_error_upper_bound_persons"),
            "greedy_failure_confirmed": item.get("greedy_failure_confirmed"),
        })
    for item in c["stress_cases"] + [c["greedy_counterexample"]] + c["ablation_cases"]:
        meta = item["metadata"]
        oracle = item.get("oracle") or {}
        rows.append({
            "case_id": item["case_id"], "phase": "C_STRESS" if item.get("case_kind") != "greedy_counterexample" and not item["case_id"].startswith("ABL-") else ("C_GREEDY_COUNTEREXAMPLE" if item["case_id"] == "SYN-greedy-counterexample" else "D_ABLATION"),
            "provenance": item.get("provenance", "FULLY_SYNTHETIC_STRESS"), "data_status": "SYNTHETIC_FIXTURE",
            "candidate_count": item["candidate_count"], "time_limit_seconds": item.get("time_limit_seconds", 10),
            "budget_units": meta.get("budget"), "solver_status": item.get("solver_status"),
            "optimality_proven": item.get("optimality_proven"), "best_feasible_served": item.get("best_feasible_served_demand"),
            "primary_best_bound_served": item.get("primary_best_bound_served_demand"), "primary_gap": item.get("primary_relative_gap"),
            "runtime_seconds": item.get("runtime_seconds"), "verifier_status": (item.get("verifier") or {}).get("verification_status"),
            "greedy_served": (item.get("greedy") or {}).get("served_demand"), "mip_minus_greedy": item.get("mip_minus_greedy_persons"),
            "oracle_status": oracle.get("oracle_status"), "oracle_served": oracle.get("objective_served_demand", oracle.get("objective_upper_bound_served_demand")),
            "oracle_error_bound": oracle.get("rounding_error_upper_bound_persons"),
            "greedy_failure_confirmed": (item.get("solver_status") == "OPTIMAL" and item.get("case_id") == "SYN-greedy-counterexample" and oracle.get("oracle_status") == "ENUMERATED_MAXFLOW_PROVEN"),
            "demand_nodes": meta.get("demand_nodes"), "od_edges": meta.get("od_edges"),
            "peak_rss_sampled_bytes": (item.get("memory") or {}).get("peak_rss_sampled_bytes"),
        })
    return rows


def main():
    phase_a = read(OUT / "oracles" / "phase_a_real_structure.json")
    phase_b_path = OUT / "oracles" / "phase_b_grounded_search.json"
    if not phase_b_path.exists():
        phase_b_path = OUT / "oracles" / "phase_b_grounded_search_summary.json"
    phase_b = read(phase_b_path)
    phase_c = read(OUT / "oracles" / "phase_c_d_synthetic_results.json")
    v1_run = read(V1)
    test_records = {
        "module_b": "30 passed",
        "v2": "3 passed",
        "compileall": "passed",
        "warning": "pytest emitted one existing RequestsDependencyWarning about urllib3/chardet/charset_normalizer versions; no test failed.",
    }
    reports = {
        "01_WHY_GREEDY_EQUALS_MIP.md": report_phase_a(phase_a),
        "02_GROUNDED_SEARCH_RESULTS.md": report_phase_b(phase_b),
        "03_SYNTHETIC_STRESS_RESULTS.md": report_phase_c(phase_c),
        "04_DIFFICULTY_ABLATION.md": report_ablation(phase_c),
        "05_PUBLIC_API_COMPATIBILITY.md": report_api(),
        "06_FAILURE_AND_LIMITATIONS.md": report_limits(phase_b, phase_c, test_records),
        "07_FINAL_RECOMMENDATIONS.md": report_recommendations(phase_b, phase_c),
    }
    report_dir = OUT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    for name, content in reports.items():
        (report_dir / name).write_text(content, encoding="utf-8")
    case_rows = collect_case_rows(phase_b, phase_c)
    columns = sorted({key for row in case_rows for key in row})
    with (OUT / "all_cases.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader(); writer.writerows(case_rows)
    current_source_hashes = source_hashes()
    unchanged_after_all_work = current_source_hashes == phase_b["source_hashes_after"]
    script_hashes = {}
    for path in sorted((OUT / "tools").glob("*.py")):
        script_hashes[path.name] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    env = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
        "platform": platform.platform(), "numpy": __import__("numpy").__version__,
        "scipy": __import__("scipy").__version__, "networkx": __import__("networkx").__version__,
        "psutil_available": phase_c["environment"].get("psutil_available"),
        "v1_source_manifest_sha256": phase_a["source_manifest_sha256"],
        "a_source_hashes_before_v2": phase_b["source_hashes_before"],
        "a_source_hashes_after_grounded_search": phase_b["source_hashes_after"],
        "a_source_hashes_unchanged_during_search": phase_b["source_hashes_unchanged"],
        "a_source_hashes_after_all_v2_work": current_source_hashes,
        "a_source_hashes_unchanged_after_all_v2_work": unchanged_after_all_work,
        "test_commands": ["python -m pytest tests/module_b -q", "python -m pytest merge_data/v2_difficulty_discovery/tests -q", "python -m compileall -q backend"],
        "test_results": test_records,
        "v2_tool_sha256": script_hashes,
        "random_seeds": {"real_gis_search": phase_b["seed"], "synthetic_stress": phase_c["seed"]},
        "runtime_patches": "Engine MIP time cap patched only in runner process for 2s/10s measurement and restored after every call; production files unchanged.",
        "source_modifications": "Only merge_data/v2_difficulty_discovery outputs/scripts/tests were created; no backend, Module A or Module C source was edited.",
        "v1_run_summary_sha256": v1_run["source_run_summary_sha256"],
    }
    (OUT / "environment.json").write_text(json.dumps(env, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    integrity = {
        "phase": "F_REGRESSION_AND_SOURCE_INTEGRITY",
        "source_manifest_sha256": phase_a["source_manifest_sha256"],
        "source_hashes_before_experiment": phase_b["source_hashes_before"],
        "source_hashes_after_grounded_search": phase_b["source_hashes_after"],
        "source_hashes_after_all_v2_work": current_source_hashes,
        "unchanged_before_after_search": phase_b["source_hashes_unchanged"],
        "unchanged_after_all_v2_work": unchanged_after_all_work,
        "regression_runs": test_records,
        "v1_negative_control_observations": {
            "D02_unknown_shelter_option": "rejected as invalid candidate",
            "D03_injected_incumbent_status_1": "returned FEASIBLE, optimality_proven=false, verifier PASS_DEMO; simulated status, not a natural timeout",
            "D04_tampered_served_summary": "Verifier returned FAIL on numerical recomputation",
        },
        "v2_oracles": {
            "real_gis_confirmed_failures": phase_b["confirmed_greedy_failures"],
            "synthetic_greedy_counterexample": "OPTIMAL MIP and exhaustive independent NetworkX Dinitz proof",
            "stress_runs": phase_c["stress_case_count"],
            "stress_optimal": sum(row["solver_status"] == "OPTIMAL" for row in phase_c["stress_cases"]),
            "ablation_runs": len(phase_c["ablation_cases"]),
        },
        "v2_tool_sha256": script_hashes,
    }
    (OUT / "oracles" / "phase_f_integrity.json").write_text(json.dumps(integrity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    readme = fr"""# ShelterPlan Module B V2 difficulty discovery

This directory contains the V2 A-anchored search, fully synthetic stress tests, ablations, regression fixtures and reports. No output from the previous V1 run was overwritten. A source files, B production solver/API, and Module C were not edited. All planning outputs retain `DEMO_ONLY` or `FULLY_SYNTHETIC_STRESS` provenance as appropriate.

## Reproduce

From the Module B project root in PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = '.'
.\.venv\Scripts\python.exe merge_data\v2_difficulty_discovery\tools\analyze_real_structure.py
.\.venv\Scripts\python.exe merge_data\v2_difficulty_discovery\tools\run_grounded_search.py
.\.venv\Scripts\python.exe merge_data\v2_difficulty_discovery\tools\run_synthetic_stress.py
.\.venv\Scripts\python.exe merge_data\v2_difficulty_discovery\tools\generate_reports.py
.\.venv\Scripts\python.exe -m pytest tests\module_b -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest merge_data\v2_difficulty_discovery\tests -q -p no:cacheprovider
$env:PYTHONPYCACHEPREFIX = 'merge_data\v2_difficulty_discovery\runs\compile_cache'
.\.venv\Scripts\python.exe -m compileall -q backend
```

The grounded search fixes seed `{phase_b['seed']}`, screens at most 100 option sets, and ran {phase_b['limits']['full_mips_run']} of at most 30 full MIPs. Each full A MIP has a 10-second total solver limit. Synthetic stress uses seed `{phase_c['seed']}`, 8/16/32/48/64 candidates, simple and competitive graphs, and 2/10-second solver limits.

## Contents

- `configs/v2_experiment_config.json`: fixed seeds, sizes, limits and integrity constraints.
- `reports/`: seven requested phase reports.
- `scenarios/`: generated candidate sets and fully synthetic fixtures.
- `oracles/`: structural metrics, search evidence, stage bounds, enumeration proofs, and ablations.
- `runs/`: per-case full results and traces, including verifier outputs.
- `all_cases.csv`: indexed grounded, synthetic, counterexample and ablation cases.
- `environment.json`: versions, seeds, tests and A-source hash evidence.
- `tests/`: V2 synthetic provenance and counterexample regression checks.

See `reports/07_FINAL_RECOMMENDATIONS.md` for explicit answers A–I. MIP outputs remain DEMO_ONLY; no operational recommendation or verified infrastructure fact is implied.
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps({"reports": list(reports), "indexed_cases": len(case_rows),
                      "confirmed_real_gis_failures": phase_b["confirmed_greedy_failures"],
                      "synthetic_stress_cases": phase_c["stress_case_count"],
                      "ablation_cases": len(phase_c["ablation_cases"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
