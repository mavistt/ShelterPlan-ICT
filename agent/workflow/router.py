"""Deterministic routing: parsed intent -> one executable plan.

C decides *which business task* to run, whether the parameters are sufficient
and whether the session may run at all. B still decides how to compute it.
There is deliberately no skill DAG here: C never re-plans B's internal steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.config import HarnessConfig
from agent.contracts import SCENARIO_CHANGE_KEYS, ScenarioParams
from agent.intent.normalizer import build_compare_rows, build_sensitivity, expand_options
from agent.intent.schema import TASK_BY_INTENT, ParsedIntent

CLARIFY_QUESTIONS = {
    "budget_units": "请给出预算上限（预算单位，≥0），例如“预算 2”。",
    "improvement_options": "请提供至少一个扩容候选：场所 ID、可增加容量、成本，例如“zs_xxx 扩容 300 人，成本 1”。",
    "disabled_shelter_ids": "请指定要模拟停用的场所 ID（例如 zs_1b4df7920b）。",
    "compare_values": "请给出要对比的 2 个以上取值，例如“需求比例 0.1 和 0.3”。",
    "sensitivity_spec": "请指定敏感性分析的参数和取值档位，例如“预算 0、1、2、3”。",
    "result_id": "当前会话还没有可解释的结果，请先运行一次评估或优化。",
    "task": "请说明要执行哪类分析：现状评估、预算优化、情景比较、压力测试或敏感性分析。",
}

# Chinese labels for the fields, so a question can name the thing the user used.
CLARIFY_FIELD_NAMES = {
    "budget_units": "预算",
    "improvement_options": "扩容候选",
    "disabled_shelter_ids": "要停用的场所",
    "compare_values": "对比取值",
    "sensitivity_spec": "敏感性档位",
    "result_id": "要解释的结果",
    "population_demand_ratio": "需求比例",
    "max_walk_time_min": "步行阈值",
    "task": "分析类型",
}


@dataclass
class TurnMemory:
    """Conversation-level facts that are NOT scenario parameters.

    `improvement_options` belong to a request, not to a scenario, so Module B
    never stores them. Without this, "预算减半" after an optimize would re-run
    with an empty candidate table and silently return a useless plan.
    """

    last_task_type: str | None = None
    last_options: list[dict[str, Any]] = field(default_factory=list)
    last_result_id: str | None = None
    last_changes: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)

    def remember_success(self, plan: "RoutePlan", result_id: str | None) -> None:
        if plan.task_type:
            self.last_task_type = plan.task_type
        if plan.options:
            self.last_options = list(plan.options)
        if result_id:
            self.last_result_id = result_id
        if plan.changes:
            self.last_changes = dict(plan.changes)
        self.actions.append(f"{plan.task_type or plan.action}:ok")

    def remember_failure(self, plan: "RoutePlan", code: str | None) -> None:
        self.actions.append(f"{plan.task_type or plan.action}:{code or 'error'}")


@dataclass
class RoutePlan:
    action: str  # task | clarify | refuse
    task_type: str | None = None
    changes: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    options: list[dict[str, Any]] = field(default_factory=list)
    result_id: str | None = None
    clarify_fields: list[str] = field(default_factory=list)
    question: str | None = None
    reason_codes: list[str] = field(default_factory=list)
    claim: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "task_type": self.task_type,
            "changes": self.changes,
            "parameters": self.parameters,
            "options_count": len(self.options),
            "result_id": self.result_id,
            "clarify_fields": self.clarify_fields,
            "reason_codes": self.reason_codes,
        }


def route(intent: ParsedIntent, *, params: ScenarioParams, changes: dict[str, Any],
          memory: TurnMemory, config: HarnessConfig,
          known_result_ids: list[str] | None = None) -> RoutePlan:
    codes: list[str] = []

    if params.analysis_mode == "verified" and config.expected_data_status != "VERIFIED":
        return RoutePlan(action="refuse", reason_codes=["data_status_not_verified"],
                         question=("当前数据源不是经过验收的 Module A 发布版本（状态 "
                                   f"{config.expected_data_status}），不能执行 verified 模式规划。"))

    if intent.name == "clarify":
        return _clarify(intent.clarify or ["task"], codes + ["intent_unclear"])

    task_type = TASK_BY_INTENT.get(intent.name)
    if intent.name == "modify_scenario":
        task_type = memory.last_task_type
        if task_type is None:
            task_type = "assess"
            codes.append("default_assess_after_modify")
        else:
            codes.append("reuse_last_task")
    if task_type is None:
        return _clarify(intent.clarify or ["task"], codes + ["no_task_mapped"])

    options = expand_options(intent.options)
    if intent.options and not options:
        partial = RoutePlan(action="task", task_type=task_type, changes=dict(changes),
                            options=options, reason_codes=codes)
        return _clarify(["improvement_options"], codes + ["options_unusable"],
                        task_type=task_type, carry=partial)
    plan = RoutePlan(action="task", task_type=task_type, changes=dict(changes),
                     options=options, reason_codes=codes)

    if task_type == "optimize":
        budget = plan.changes.get("budget_units", params.budget_units)
        if budget is None:
            return _clarify(["budget_units"], codes + ["budget_missing"],
                            task_type=task_type, carry=plan)
        if not plan.options:
            plan.options = list(memory.last_options)
            if plan.options:
                plan.reason_codes.append("options_reused_from_memory")
        if not plan.options:
            return _clarify(["improvement_options"], codes + ["options_missing"],
                            task_type=task_type, carry=plan)

    elif task_type == "stress_test":
        disabled = plan.changes.get("disabled_shelter_ids", list(params.disabled_shelter_ids))
        if not disabled:
            return _clarify(["disabled_shelter_ids"], codes + ["stress_target_missing"],
                            task_type=task_type, carry=plan)
        plan.changes["disabled_shelter_ids"] = list(disabled)

    elif task_type == "compare":
        if intent.compare and intent.compare.get("field") in plan.changes:
            # The comparison axis is carried by the rows, not by a base change.
            plan.changes.pop(intent.compare["field"], None)
        rows = build_compare_rows(params=params, changes=plan.changes, spec=intent.compare)
        if len(rows) < 2:
            return _clarify(["compare_values"], codes + ["compare_axis_missing"],
                            task_type=task_type, carry=plan)
        plan.parameters = {"scenarios": rows}
        plan.reason_codes.append("compare_rows_from_" + ("explicit_values" if intent.compare else "before_after"))

    elif task_type == "sensitivity":
        spec = build_sensitivity(intent.sensitivity)
        if spec is None:
            return _clarify(["sensitivity_spec"], codes + ["sensitivity_spec_missing"],
                            task_type=task_type, carry=plan)
        plan.parameters = spec
        plan.changes.pop(spec["parameter"], None)
        if not plan.options:
            plan.options = list(memory.last_options)
            if plan.options:
                plan.reason_codes.append("options_reused_from_memory")

    elif task_type == "explain_result":
        # A resumed clarification can name the result explicitly; that id wins
        # over "the last one in this session".
        result_id = (intent.raw or {}).get("result_id") or memory.last_result_id
        if result_id is None and known_result_ids:
            result_id = known_result_ids[-1]
        if not result_id:
            return _clarify(["result_id"], codes + ["no_result_to_explain"],
                            task_type=task_type, carry=plan)
        plan.result_id = result_id
        plan.reason_codes.append("explain_bound_to_last_result")

    return plan


def _clarify(fields: list[str], codes: list[str],
             task_type: str | None = None,
             carry: RoutePlan | None = None) -> RoutePlan:
    """`task_type` records what the user was trying to do.

    It is what lets the next turn *resume* the original task instead of
    re-guessing the intent from a one-word answer. `carry` keeps the slots the
    user already supplied (candidate table, comparison spec, result id and
    parameter changes) on the pending task: a clarification must not throw away
    information that was already confirmed, or the resumed turn would ask for
    the same thing again.
    """
    fields = [field for field in dict.fromkeys(fields) if field]
    question = " ".join(CLARIFY_QUESTIONS.get(field, f"请补充 {field}。") for field in fields) \
        if fields else CLARIFY_QUESTIONS["task"]
    plan = RoutePlan(action="clarify", task_type=task_type, clarify_fields=fields,
                     question=question, reason_codes=codes + ["clarify"])
    if carry is not None:
        plan.changes = dict(carry.changes)
        plan.options = list(carry.options)
        plan.parameters = dict(carry.parameters)
        plan.result_id = carry.result_id
    return plan
