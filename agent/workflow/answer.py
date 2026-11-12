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


def compose_answer(control: ControlResult, *, params: ScenarioParams,
                   memory: Any | None = None) -> str:
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
            + f"；总成本 {_num(optimization.get('total_cost_units'), 2)} / "
              f"预算 {_num(optimization.get('budget_units'), 2)}。")
        before = metrics.get("served_demand")
        after = optimization.get("served_demand")
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            lines.append(
                f"【覆盖变化】优化前基线已服务 {_num(before)} 人；优化后 {_num(after)} 人；"
                f"变化 {_num(after - before)} 人。")
        else:
            lines.append(f"【覆盖】优化后已服务 {_num(after)} 人。")
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
        intervention = detail.get("intervention")
        lines.append("【结论】瓶颈诊断完成。")
        if diagnosis:
            lines.append(f"【未服务节点】{_count(diagnosis.get('unserved_node_count'))} 个；"
                         f"按原因分组 {diagnosis.get('by_cause') or {}}。")
            persons = diagnosis.get("unserved_persons_by_cause") or {}
            if persons:
                lines.append("【未服务人数（按原因）】" +
                             "；".join(f"{key}: {_num(value)} 人" for key, value in persons.items()))
        if intervention:
            selected = intervention.get("selected_option_ids") or []
            lines.append("【干预建议】" + ("、".join(selected) if selected else "无"))
    elif task_type == "explain_result":
        lines.append("【结论】对上一条结果的解释如下（全部来自该结果的投影，不含推断）。")
        lines.append(
            f"【指标来源】served_demand={_num(metrics.get('served_demand'))}、"
            f"total_demand={_num(metrics.get('total_demand'))}、"
            f"weighted_walk_time_min={_num(metrics.get('weighted_walk_time_min'), 2)} "
            "均由 Module B 的分配求解与业务 Verifier 计算，C 只做投影，不做二次计算。")
    else:
        lines.append("【结论】现状评估完成。")
        diagnosis = detail.get("diagnosis") or {}
        if diagnosis:
            lines.append(f"【未服务节点】{_count(diagnosis.get('unserved_node_count'))} 个。")

    if task_type not in {"compare", "sensitivity"}:
        lines.append(
            f"【关键指标】已服务 {_num(metrics.get('served_demand'))} 人 / "
            f"总需求 {_num(metrics.get('total_demand'))} 人，"
            f"覆盖率 {_num((bindings.get('coverage_ratio') or 0) * 100, 1)}%；"
            f"加权步行时间 {_num(metrics.get('weighted_walk_time_min'), 2)} 分钟。")

    lines.append(f"【参数】{_params_line(bindings.get('scenario_params') or params.to_dict())}")
    lines.append(f"【求解与验证】{_solver_sentence(bindings.get('solver_status'), bindings.get('optimality_proven'))}"
                 f" verification_status={bindings.get('verification_status') or 'n/a'}。")
    lines.append(DEMO_LINE if bindings.get("data_status") == "DEMO_ONLY" else VERIFIED_LINE)
    lines.append(f"【结果编号】result_id={bindings.get('result_id') or 'n/a'}；"
                 f"scenario_id={bindings.get('scenario_id') or 'n/a'}")
    return "\n".join(lines)


def compose_clarification(plan: RoutePlan, params: ScenarioParams) -> str:
    question = plan.question or "请补充必要参数后我再执行。"
    return (f"{question}\n当前参数：{_params_line(params.to_dict())}。\n"
            "（未执行任何规划任务，因此没有新增结果。）")


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
