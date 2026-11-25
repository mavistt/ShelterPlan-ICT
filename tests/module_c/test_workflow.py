"""Offline tests for routing, execution control and deterministic answers."""
from __future__ import annotations

import pytest

from agent.config import HarnessConfig
from agent.contracts import ScenarioParams, ScenarioState
from agent.errors import GuardInvalidParam, GuardNotVerifiedSource
from agent.intent.schema import ParamOp, ParsedIntent
from agent.workflow.answer import DEMO_LINE, compose_answer, compose_clarification, compose_error
from agent.workflow.controller import ControlResult, TaskController, bind_outcome, classify_error
from agent.workflow.router import RoutePlan, TurnMemory, route

PARAMS = ScenarioParams(0.2, 15.0, None, (), "zhongshan_pilot_v0.1", "demo")
CONFIG = HarnessConfig()


def _state(params: ScenarioParams, turn: int, result_id: str | None = None) -> ScenarioState:
    return ScenarioState(scenario_id=f"s{turn}", parent_scenario_id=None, params=params,
                         changes={}, turn=turn, result_id=result_id)


def _summary(**overrides) -> dict:
    summary = {
        "result_id": "r-1",
        "task_type": "assess",
        "scenario_id": "c-1",
        "data_status": "DEMO_ONLY",
        "solver_status": "OPTIMAL",
        "optimality_proven": True,
        "verification_status": "PASS_DEMO",
        "metrics": {"served_demand": 6456.103, "total_demand": 110205.74,
                    "weighted_walk_time_min": 9.0376, "optimized_served_demand": None,
                    "diagnosed_unserved_nodes": 7634},
        "scenario_params": PARAMS.business_fields(),
        "detail": {"diagnosis": {"unserved_node_count": 7634,
                                 "by_cause": {"NO_LEGAL_CONNECTION": 5599},
                                 "unserved_persons_by_cause": {"NO_LEGAL_CONNECTION": 67097.311}}},
        "warnings": [],
    }
    summary.update(overrides)
    return summary


def _outcome(summary: dict | None = None, *, ok: bool = True, error: dict | None = None,
             duration_ms: float = 12.0) -> dict:
    return {
        "ok": ok, "task_type": (summary or {}).get("task_type", "assess"),
        "result_id": (summary or {}).get("result_id"), "summary": summary, "error": error,
        "scenario": {"scenario_id": "c-1", "params": PARAMS.to_dict()},
        "data_status": (summary or {}).get("data_status", "DEMO_ONLY"),
        "solver_status": (summary or {}).get("solver_status", "OPTIMAL"),
        "optimality_proven": (summary or {}).get("optimality_proven", True),
        "verification_status": (summary or {}).get("verification_status", "PASS_DEMO"),
        "duration_ms": duration_ms, "attempts": 1,
        "tool_calls": [],
    }


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
def test_route_assess():
    plan = route(ParsedIntent("assess"), params=PARAMS, changes={}, memory=TurnMemory(),
                 config=CONFIG)
    assert plan.action == "task" and plan.task_type == "assess"


def test_route_optimize_without_budget_asks():
    plan = route(ParsedIntent("optimize"), params=PARAMS, changes={}, memory=TurnMemory(),
                 config=CONFIG)
    assert plan.action == "clarify"
    assert plan.clarify_fields == ["budget_units"]
    assert "预算" in (plan.question or "")


def test_route_optimize_without_options_asks():
    params = ScenarioParams(0.2, 15.0, 2.0, (), "zhongshan_pilot_v0.1", "demo")
    plan = route(ParsedIntent("optimize"), params=params, changes={}, memory=TurnMemory(),
                 config=CONFIG)
    assert plan.clarify_fields == ["improvement_options"]


def test_route_optimize_reuses_options_from_memory():
    params = ScenarioParams(0.2, 15.0, 2.0, (), "zhongshan_pilot_v0.1", "demo")
    memory = TurnMemory(last_task_type="optimize", last_options=[{"option_id": "CAP_1"}])
    plan = route(ParsedIntent("optimize"), params=params, changes={}, memory=memory, config=CONFIG)
    assert plan.action == "task"
    assert plan.options == [{"option_id": "CAP_1"}]
    assert "options_reused_from_memory" in plan.reason_codes


def test_route_stress_without_target_asks():
    plan = route(ParsedIntent("stress_test"), params=PARAMS, changes={}, memory=TurnMemory(),
                 config=CONFIG)
    assert plan.clarify_fields == ["disabled_shelter_ids"]


def test_route_compare_builds_rows_and_drops_axis_change():
    params = ScenarioParams(0.2, 15.0, None, (), "zhongshan_pilot_v0.1", "demo")
    intent = ParsedIntent("compare", compare={"field": "population_demand_ratio",
                                              "values": [0.2, 0.4]})
    plan = route(intent, params=params, changes={"population_demand_ratio": 0.4},
                 memory=TurnMemory(), config=CONFIG)
    assert plan.action == "task"
    assert plan.parameters["scenarios"] == [
        {"max_walk_time_min": 15.0, "budget_units": None,
         "disabled_shelter_ids": [], "population_demand_ratio": 0.2},
        {"max_walk_time_min": 15.0, "budget_units": None,
         "disabled_shelter_ids": [], "population_demand_ratio": 0.4},
    ]
    assert plan.changes == {}


def test_route_sensitivity_requires_spec():
    plan = route(ParsedIntent("sensitivity"), params=PARAMS, changes={}, memory=TurnMemory(),
                 config=CONFIG)
    assert plan.clarify_fields == ["sensitivity_spec"]


def test_route_explain_uses_last_result():
    memory = TurnMemory(last_result_id="r-9")
    plan = route(ParsedIntent("explain"), params=PARAMS, changes={}, memory=memory, config=CONFIG)
    assert plan.result_id == "r-9"
    assert plan.task_type == "explain_result"


def test_route_modify_reuses_last_task():
    memory = TurnMemory(last_task_type="assess")
    plan = route(ParsedIntent("modify_scenario"), params=PARAMS,
                 changes={"max_walk_time_min": 20}, memory=memory, config=CONFIG)
    assert plan.task_type == "assess"
    assert "reuse_last_task" in plan.reason_codes


def test_route_modify_without_history_defaults_to_assess():
    plan = route(ParsedIntent("modify_scenario"), params=PARAMS, changes={},
                 memory=TurnMemory(), config=CONFIG)
    assert plan.task_type == "assess"
    assert "default_assess_after_modify" in plan.reason_codes


def test_route_refuses_verified_mode_on_demo_data():
    params = ScenarioParams(0.2, 15.0, None, (), "zhongshan_pilot_v0.1", "verified")
    plan = route(ParsedIntent("assess"), params=params, changes={}, memory=TurnMemory(),
                 config=CONFIG)
    assert plan.action == "refuse"


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
class FakeService:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def submit(self, task_type, **kwargs):
        self.calls.append({"task_type": task_type, **kwargs})
        return self.outcomes.pop(0)


def test_controller_ok_binds_numbers():
    service = FakeService([_outcome(_summary())])
    result = TaskController(service, CONFIG, session_id="s").run(
        RoutePlan(action="task", task_type="assess"))
    assert result.ok
    assert result.bindings["coverage_ratio"] == pytest.approx(6456.103 / 110205.74)
    assert result.bindings["result_id"] == "r-1"
    assert service.calls[0]["session_id"] == "s"


def test_controller_retries_transient_error():
    service = FakeService([
        _outcome(None, ok=False, error={"code": "TRANSPORT_ERROR", "message": "boom"}),
        _outcome(_summary()),
    ])
    controller = TaskController(service, CONFIG, max_attempts=2, backoff_s=0)
    result = controller.run(RoutePlan(action="task", task_type="assess"))
    assert result.ok and result.attempts == 2


def test_controller_does_not_retry_guard_error():
    service = FakeService([
        _outcome(None, ok=False, error={"code": "GUARD_MISSING_PARAM", "message": "no budget"}),
    ])
    result = TaskController(service, CONFIG, max_attempts=2).run(
        RoutePlan(action="task", task_type="optimize"))
    assert not result.ok
    assert result.attempts == 1
    assert result.error_class == "guard"


def test_controller_passes_route_parameters():
    service = FakeService([_outcome(_summary())])
    TaskController(service, CONFIG).run(RoutePlan(
        action="task", task_type="compare", parameters={"scenarios": [{"a": 1}]},
        changes={"budget_units": 2}))
    assert service.calls[0]["parameters"] == {"scenarios": [{"a": 1}]}
    assert service.calls[0]["changes"] == {"budget_units": 2}


@pytest.mark.parametrize("code,expected", [
    ("GUARD_MISSING_PARAM", "guard"),
    ("TRANSPORT_ERROR", "upstream_transient"),
    ("B_SCENARIO_INVALID", "upstream_rejected"),
    ("RESULT_MISMATCH", "integrity"),
    ("INTERNAL_ERROR", "internal"),
    (None, "internal"),
])
def test_classify_error(code, expected):
    assert classify_error(code) == expected


# --------------------------------------------------------------------------- #
# Deterministic answers
# --------------------------------------------------------------------------- #
def _ok_control(summary: dict | None = None) -> ControlResult:
    outcome = _outcome(summary or _summary())
    return ControlResult(status="ok", plan=RoutePlan(action="task", task_type="assess"),
                         outcome=outcome, summary=outcome["summary"],
                         bindings=bind_outcome(outcome), b_api_ms=12.0)


def test_answer_binds_numbers_and_demo_line():
    text = compose_answer(_ok_control(), params=PARAMS)
    assert "6,456.10" in text
    assert "110,205.74" in text
    assert "5.9%" in text
    assert DEMO_LINE in text
    assert "可执行建议" not in text and "已核验" not in text


def test_answer_marks_feasible_as_not_proven():
    summary = _summary(solver_status="FEASIBLE", optimality_proven=False)
    text = compose_answer(_ok_control(summary), params=PARAMS)
    assert "FEASIBLE" in text
    assert "未证明最优" in text
    assert "且最优性已证明" not in text


def test_answer_optimize_separates_before_and_after():
    summary = _summary(task_type="optimize")
    summary["detail"] = {"optimization": {
        "selected_option_ids": ["CAP_1"], "total_cost_units": 1, "budget_units": 2,
        "served_demand": 7000.0, "total_demand": 110205.74, "weighted_walk_time_min": 9.1}}
    text = compose_answer(_ok_control(summary), params=PARAMS)
    assert "优化前基线已服务 6,456.10 人" in text
    assert "优化后 7,000.00 人" in text


def test_clarification_names_the_missing_field():
    plan = RoutePlan(action="clarify", clarify_fields=["budget_units"],
                     question="请给出预算上限（预算单位，≥0），例如“预算 2”。")
    text = compose_clarification(plan, PARAMS)
    assert "预算" in text
    assert "未执行任何规划任务" in text


def test_error_answer_is_classified():
    control = ControlResult(status="error", plan=RoutePlan(action="task"),
                            error={"code": "GUARD_MISSING_PARAM", "message": "budget required",
                                   "user_action": "Ask the user"},
                            error_class="guard", attempts=1)
    text = compose_error(control)
    assert "未执行" in text and "GUARD_MISSING_PARAM" in text
    assert "没有产生可引用的规划结果" in text
