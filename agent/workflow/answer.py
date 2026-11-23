"""Deterministic answer composition from bound result values.

Arm D never asks the model to rewrite a paragraph of prose: numbers, status
lines and the DEMO_ONLY declaration are formatted from the projected summary.
That removes the two largest failure classes measured in V1 (fabricated numbers
and missing/incorrect DEMO_ONLY wording) without spending a single token.
"""
from __future__ import annotations

from typing import Any

from agent.contracts import ScenarioParams
from agent.workflow.controller import ControlResult
from agent.workflow.router import RoutePlan

DEMO_LINE = ("【数据声明】DEMO_ONLY — 基于演示数据，未经真实现场核验，仅供演示与算法评估。")
VERIFIED_LINE = "【数据声明】数据源为 VERIFIED 版本。"


def _num(value: Any, digits: int = 2) -> str:
    if isinstance(value, bool) or value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def _count(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{int(value):,d}"
    return "n/a"


def _flag(value: Any) -> str:
    if value is None:
        return "unknown"
    return "true" if value else "false"


def _format_monotonicity_line(mono: dict[str, Any]) -> str:
    status = mono.get("status") or "INCONCLUSIVE"
    param = mono.get("parameter") or "n/a"
    relation = mono.get("relation") or "n/a"
    reason = mono.get("reason") or "n/a"
    base = (
        f"【单调性验证】状态={status}（参数={param}；关系={relation}；结论={reason}；"
        "仅模型同口径数值对比，非真实因果证据）。"
    )
    if status == "FAIL":
        violations = mono.get("violations") or []
        parts = [
            f"{v.get('left_value')}→{v.get('right_value')}"
            for v in violations[:3]
            if isinstance(v, dict)
        ]
        if parts:
            return base + f" 违规区间：{'、'.join(parts)}。"
    return base


def _solver_sentence(status: Any, proven: Any) -> str:
    if status == "OPTIMAL" and proven:
        return "求解状态 OPTIMAL，且最优性已证明。"
    if status == "OPTIMAL" and not proven:
        return "求解状态 OPTIMAL，但最优性未证明，不得表述为“已证明最优”。"
    if status == "FEASIBLE":
        return "求解状态 FEASIBLE：得到可行解，但未证明最优，不得表述为“已证明最优”。"
    return f"求解状态 {status or 'unknown'}。"


def _params_line(params: dict[str, Any]) -> str:
    ratio = params.get("population_demand_ratio")
    ratio_text = f"{float(ratio) * 100:.0f}%" if isinstance(ratio, (int, float)) else "n/a"
    budget = params.get("budget_units")
    return (
        f"需求比例 {ratio_text}；步行阈值 {_num(params.get('max_walk_time_min'), 1)} 分钟；"
        f"预算 {('未设置' if budget is None else _num(budget, 2))}；"
        f"停用场所 {len(params.get('disabled_shelter_ids') or [])} 个"
    )


def _has_hypothetical_option(bindings: dict[str, Any], optimization: dict[str, Any]) -> bool:
    if any(isinstance(w, str) and "HYPOTHETICAL" in w for w in (bindings.get("warnings") or [])):
        return True
    for row in optimization.get("selected_options") or []:
        if isinstance(row, dict) and row.get("hypothetical"):
            return True
    return False


def compose_answer(control: ControlResult, *, params: ScenarioParams,
                   memory: Any | None = None) -> str:
    if control.status == "cancelled":
        return ("已取消这次未完成的规划请求。没有执行任何规划任务，也没有产生新的结果；"
                "已有参数保持不变。")
    if control.status == "clarify":
        return compose_clarification(control.plan, params)
    if control.status == "refuse":
        return control.plan.question or "当前请求被数据准入策略拒绝。"
    if control.status != "ok":
        return compose_error(control)

    bindings = control.bindings
    task_type = bindings.get("task_type") or control.plan.task_type
    metrics = {key: value for key, value in bindings.items()
               if isinstance(value, (int, float)) and not isinstance(value, bool)}
    detail = bindings.get("detail") or {}
    lines: list[str] = []

    if task_type == "optimize":
        optimization = (detail.get("optimization") or {})
        lines.append("【结论】预算约束下的扩容优化已完成。")
        selected = optimization.get("selected_option_ids") or []
        lines.append(
            "【优化结果】选中措施：" + ("、".join(selected) if selected else "无（预算内没有可带来收益的措施）")
            + f"。总成本 {_num(optimization.get('total_cost_units'), 2)} / "
              f"预算 {_num(optimization.get('budget_units'), 2)}。")
        before = metrics.get("served_demand")
        after = optimization.get("served_demand")
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            lines.append(
                f"【覆盖变化】优化前基线已服务 {_num(before)} 人；优化后 {_num(after)} 人；"
                f"变化 {_num(after - before)} 人。")
        else:
            lines.append(f"【覆盖】优化后已服务 {_num(after)} 人。")
        greedy = optimization.get("greedy_comparison") or {}
        if greedy.get("status") == "available":
            lines.append(
                f"【Greedy对照】Greedy 已服务 {_num(greedy.get('greedy_served_demand'))} 人；"
                f"MIP 差值 {_num(greedy.get('served_demand_gap'))} 人。")
        if _has_hypothetical_option(bindings, optimization):
            lines.append("【假设说明】HYPOTHETICAL — 扩容容量与成本为假设参数，非真实工程报价。")
    elif task_type == "compare":
        comparison = detail.get("comparison") or {}
        rows = comparison.get("scenarios") or []
        lines.append(f"【结论】情景比较完成，共 {len(rows)} 个情景。")
        body = []
        for index, row in enumerate(rows, 1):
            changes = row.get("changes") or {}
            label = "、".join(f"{key}={value}" for key, value in changes.items()) or "基线"
            body.append(f"  {index}. {label} → 已服务 {_num(row.get('served_demand'))} 人，"
                        f"加权步行 {_num(row.get('weighted_walk_time_min'), 2)} 分钟，"
                        f"验证 {row.get('verification_status') or 'n/a'}")
        lines.append("【对比明细】\n" + "\n".join(body))
        lines.append("【说明】各情景为并列结果，服务端不给出唯一推荐方案。")
    elif task_type == "sensitivity":
        sensitivity = detail.get("sensitivity") or {}
        points = sensitivity.get("points") or []
        lines.append(f"【结论】敏感性分析完成，参数 {sensitivity.get('parameter') or 'n/a'}，"
                     f"共 {len(points)} 个取值点。")
        body = []
        for point in points:
            body.append(f"  - {sensitivity.get('parameter')}={point.get('value')} → "
                        f"已服务 {_num(point.get('served_demand'))} 人，"
                        f"加权步行 {_num(point.get('weighted_walk_time_min'), 2)} 分钟")
        lines.append("【取值明细】\n" + "\n".join(body))
    elif task_type == "stress_test":
        stress = detail.get("stress") or {}
        fixed = stress.get("fixed_plan") or {}
        adaptive = stress.get("adaptive_replan") or {}
        disabled = stress.get("disabled_shelter_ids") or []
        lines.append(f"【结论】停用 {len(disabled)} 个场所的压力测试完成。")
        lines.append(f"【停用场所】{'、'.join(disabled) if disabled else 'n/a'}")
        lines.append(f"【固定方案】已服务 {_num(fixed.get('served_demand'))} 人。")
        if adaptive:
            lines.append(f"【自适应重规划】已服务 {_num(adaptive.get('served_demand'))} 人。")
    elif task_type == "diagnose_intervene":
        diagnosis = detail.get("diagnosis") or {}
        optimization = detail.get("optimization") or {}
        intervention = detail.get("intervention")
        lines.append("【结论】瓶颈诊断完成。")
        if diagnosis:
            lines.append(f"【未服务节点】{_count(diagnosis.get('unserved_node_count'))} 个；"
                         f"按原因分组 {diagnosis.get('by_cause') or {}}。")
            persons = diagnosis.get("unserved_persons_by_cause") or {}
            if persons:
                lines.append("【未服务人数（按原因）】" +
                             "；".join(f"{key}: {_num(value)} 人" for key, value in persons.items()))
        if optimization:
            selected = optimization.get("selected_option_ids") or []
            lines.append(
                "【干预优化】选中措施：" + ("、".join(selected) if selected else "无（预算内没有可带来收益的措施）")
                + f"。总成本 {_num(optimization.get('total_cost_units'), 2)} / "
                  f"预算 {_num(optimization.get('budget_units'), 2)}。")
            before = metrics.get("served_demand")
            after = optimization.get("served_demand")
            delta = optimization.get("served_demand_delta")
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                d_val = delta if isinstance(delta, (int, float)) else (after - before)
                lines.append(
                    f"【覆盖变化】优化前基线已服务 {_num(before)} 人；优化后 {_num(after)} 人；"
                    f"变化 {_num(d_val)} 人。")
            if _has_hypothetical_option(bindings, optimization):
                lines.append("【假设说明】HYPOTHETICAL — 扩容容量与成本为假设参数，非真实工程报价。")
        elif intervention:
            selected = intervention.get("selected_option_ids")
            if isinstance(selected, list):
                lines.append("【干预建议】" + ("、".join(selected) if selected else "无"))
            elif intervention.get("status") == "NOT_APPLICABLE":
                lines.append("【干预建议】未提供可用扩容候选，本次仅输出瓶颈诊断证据。")
    elif task_type == "explain_result":
        lines.append("【结论】对上一条结果的解释如下（全部来自该结果的投影，不含推断）。")
        lines.append(
            f"【指标来源】served_demand={_num(metrics.get('served_demand'))}、"
            f"total_demand={_num(metrics.get('total_demand'))}、"
            f"weighted_walk_time_min={_num(metrics.get('weighted_walk_time_min'), 2)} "
            "均由 Module B 的分配求解与业务 Verifier 计算，C 只做投影，不做二次计算。")
        explanation = detail.get("explanation") or {}
        target = explanation.get("target_summary") or {}
        t_detail = target.get("detail") if isinstance(target.get("detail"), dict) else detail
        t_opt = t_detail.get("optimization") if isinstance(t_detail, dict) else None
        t_diag = t_detail.get("diagnosis") if isinstance(t_detail, dict) else None
        if isinstance(t_opt, dict):
            sel = t_opt.get("selected_option_ids") or []
            opt_after = t_opt.get("served_demand")
            opt_delta = t_opt.get("served_demand_delta")
            lines.append(
                "【优化方案解释】选中措施：" + ("、".join(sel) if sel else "无")
                + f"。总成本 {_num(t_opt.get('total_cost_units'), 2)} / "
                  f"预算 {_num(t_opt.get('budget_units'), 2)}；"
                  f"优化后已服务 {_num(opt_after)} 人（相对基线变化 {_num(opt_delta)} 人）。")
            p_status = t_opt.get("primary_stage_optimal") or "UNKNOWN"
            g_comp = t_opt.get("greedy_comparison") or {}
            g_status = g_comp.get("status") or "unavailable"
            if g_status == "available":
                lines.append(
                    f"【阶段与对照证据】主目标阶段最优核验={p_status}；"
                    f"Greedy对照=available（Greedy 已服务 {_num(g_comp.get('greedy_served_demand'))} 人，"
                    f"MIP 差值 {_num(g_comp.get('served_demand_gap'))} 人）；"
                    "HYPOTHETICAL 假设参数不代表真实工程报价。")
            else:
                lines.append(
                    f"【阶段与对照证据】主目标阶段最优核验={p_status}；"
                    "Greedy对照=unavailable（B 在线结果未提供 Greedy 基线，C 不自行重算）；"
                    "HYPOTHETICAL 假设参数不代表真实工程报价。")
        if isinstance(t_diag, dict) and t_diag.get("by_cause"):
            lines.append(
                f"【诊断证据】未服务节点 {_count(t_diag.get('unserved_node_count'))} 个；"
                f"按原因分组 {t_diag.get('by_cause')}。")
    else:
        lines.append("【结论】现状评估完成。")
        diagnosis = detail.get("diagnosis") or {}
        if diagnosis:
            lines.append(f"【未服务节点】{_count(diagnosis.get('unserved_node_count'))} 个。")

    if task_type in {"optimize", "diagnose_intervene"} and isinstance(
        bindings.get("optimized_coverage_ratio"), (int, float)
    ):
        opt_served = bindings.get("optimized_served_demand")
        if opt_served is None:
            opt_served = (detail.get("optimization") or {}).get("served_demand")
        opt_walk = (
            bindings["optimized_weighted_walk_time_min"]
            if "optimized_weighted_walk_time_min" in bindings
            else metrics.get("weighted_walk_time_min")
        )
        lines.append(
            f"【关键指标】优化前基线覆盖率 {_num((bindings.get('coverage_ratio') or 0) * 100, 1)}%；"
            f"优化后已服务 {_num(opt_served)} 人 / "
            f"总需求 {_num(metrics.get('total_demand'))} 人，"
            f"覆盖率 {_num(bindings['optimized_coverage_ratio'] * 100, 1)}%；"
            f"优化后加权步行时间 {_num(opt_walk, 2)} 分钟。"
        )
    elif task_type not in {"compare", "sensitivity"}:
        lines.append(
            f"【关键指标】已服务 {_num(metrics.get('served_demand'))} 人 / "
            f"总需求 {_num(metrics.get('total_demand'))} 人，"
            f"覆盖率 {_num((bindings.get('coverage_ratio') or 0) * 100, 1)}%；"
            f"加权步行时间 {_num(metrics.get('weighted_walk_time_min'), 2)} 分钟。")

    lines.append(f"【参数】{_params_line(bindings.get('scenario_params') or params.to_dict())}")
    solver_text = _solver_sentence(bindings.get("solver_status"), bindings.get("optimality_proven"))
    if bindings.get("solver_status") == "FEASIBLE" and bindings.get("primary_stage_optimal") == "TRUE":
        solver_text += "（主目标阶段最优核验=TRUE，次级词典序阶段未全部完成最优证明）"
    lines.append(f"【求解与验证】{solver_text}"
                 f" verification_status={bindings.get('verification_status') or 'n/a'}。")
    mono = bindings.get("monotonicity_check")
    if isinstance(mono, dict) and mono.get("status"):
        lines.append(_format_monotonicity_line(mono))
    lines.append(DEMO_LINE if bindings.get("data_status") == "DEMO_ONLY" else VERIFIED_LINE)
    lines.append(f"【结果编号】result_id={bindings.get('result_id') or 'n/a'}；"
                 f"scenario_id={bindings.get('scenario_id') or 'n/a'}")
    return "\n".join(lines)


def compose_clarification(plan: RoutePlan, params: ScenarioParams) -> str:
    question = plan.question or "请补充必要参数后我再执行。"
    return (f"{question}\n当前参数：{_params_line(params.to_dict())}。\n"
            "（未执行任何规划任务，因此没有新增结果。）")


def compose_refusal(plan: RoutePlan, *, data_status: str,
                    disclosure: str | None = None) -> str:
    """Fixed wording for a request that would rewrite a trust constraint."""
    body = plan.question or "这项请求改的是系统的可信性声明，我不会执行。"
    lines = [body, f"（本次没有执行任何规划任务，现有会话状态未改变。）"]
    return "\n".join(lines)


def compose_error(control: ControlResult) -> str:
    error = control.error or {}
    code = error.get("code") or "UNKNOWN"
    message = (error.get("message") or "").strip()
    action = error.get("user_action") or ""
    if control.error_class == "guard":
        head = f"【未执行】请求未通过执行前校验（{code}）。"
    elif control.error_class == "upstream_transient":
        head = f"【暂时失败】规划服务暂时不可用（{code}），已尝试 {control.attempts} 次。"
    elif control.error_class == "upstream_rejected":
        head = f"【未执行】规划服务拒绝了该请求（{code}）。"
    elif control.error_class == "integrity":
        head = f"【已拒绝】结果未通过一致性校验（{code}），不会使用该结果。"
    else:
        head = f"【失败】执行过程中出现错误（{code}）。"
    parts = [head, f"【原因】{message[:300]}" if message else "【原因】未提供"]
    if action:
        parts.append(f"【下一步】{action}")
    parts.append("（本次没有产生可引用的规划结果。）")
    return "\n".join(parts)
