"""Tests for the fair-grading layer (the V1 defects it fixes are in
docs/module_c/V1_EVAL_AUDIT.md)."""
from __future__ import annotations

import pytest

from tests.module_c.eval_lib import (
    asks_or_refuses,
    demo_flags,
    grade,
    metrics,
    paired,
    params_match,
)


def _case(**expect) -> dict:
    return {"case_id": "T-1", "group": "plan", "turns": [{"user": "x"}],
            "expect": expect}


def _record(**overrides) -> dict:
    record = {
        "case_id": "T-1", "group": "plan", "arm": "harness_opt", "rep": 0,
        "final_text": "已服务 6,456.10 人。DEMO_ONLY，未经真实现场核验。",
        "successful_task_types": ["assess"],
        "final_params": {"population_demand_ratio": 0.35, "max_walk_time_min": 20.0,
                         "budget_units": None, "disabled_shelter_ids": []},
        "numeric": {"consistent": True, "violations": []},
        "demo": {"ok": True, "disclosed": True, "forbidden": []},
        "clarification_fields": [],
        "intent": "assess",
        "last_ops": [{"field": "max_walk_time_min", "op": "set", "value": 20.0}],
        "slot_expected": False,
        "llm_calls": 2, "llm_latency_ms": 9000.0, "b_api_ms": 3000.0,
        "duration_ms": 12500.0, "prompt_tokens": 900, "completion_tokens": 120,
        "tool_calls": 1, "planning_tasks": 1, "retries": 0,
        "runner_error": None, "max_turns_hit": False,
    }
    record.update(overrides)
    return record


def test_grade_passes_a_correct_record():
    failures, info = grade(_case(task_types=["assess"], numeric_consistent=True,
                                 demo_disclosed=True,
                                 params={"population_demand_ratio": 0.35}),
                           _record())
    assert failures == []
    assert info["params_ok"] and info["numeric_ok"] and info["demo_ok"]


def test_grade_reports_missing_task():
    failures, _ = grade(_case(task_types=["optimize"]), _record())
    assert any(failure.startswith("task_types:") for failure in failures)


def test_grade_detects_inheritance_loss():
    failures, _ = grade(_case(params={"population_demand_ratio": 0.35}),
                        _record(final_params={"population_demand_ratio": 0.2}))
    assert any("population_demand_ratio" in failure for failure in failures)


def test_grade_uses_structured_clarification_when_available():
    record = _record(successful_task_types=[], clarification_fields=["budget_units"],
                     final_text="请给出预算上限。", demo={"ok": False})
    failures, info = grade(_case(no_result="optimize", clarify_fields=["budget_units"]), record)
    assert failures == []
    assert info["clarified"] is True


def test_grade_flags_a_task_that_should_have_been_blocked():
    record = _record(successful_task_types=["optimize"], clarification_fields=["budget_units"])
    failures, _ = grade(_case(no_result="optimize"), record)
    assert any(failure.startswith("no_result:") for failure in failures)


def test_grade_intent_only_for_structured_arms():
    case = _case(intent="assess")
    structured, _ = grade(case, _record(intent="optimize"))
    assert any(failure.startswith("intent:") for failure in structured)
    tool_arm, _ = grade(case, _record(arm="harness", intent=None))
    assert not any(failure.startswith("intent:") for failure in tool_arm)


def test_grade_ops_are_order_insensitive_and_tolerant():
    case = _case(ops=[{"field": "budget_units", "op": "delta", "value": 2}])
    record = _record(last_ops=[{"field": "budget_units", "op": "delta", "value": 2.0000001}])
    failures, _ = grade(case, record)
    assert failures == []


def test_asks_or_refuses_is_narrow():
    assert asks_or_refuses("请提供预算上限，例如预算 2。")
    assert asks_or_refuses("无法执行：缺少预算。")
    # A normal answer with an interrogative style must not count as a refusal.
    assert not asks_or_refuses("东区现状评估已完成，已服务 6,456 人，覆盖率 5.9%。")
    # "无法覆盖全部需求" is a finding, not a refusal.
    assert not asks_or_refuses("当前方案无法覆盖全部需求，缺口 7,634 个节点。")


def test_demo_flags_reject_forbidden_wording():
    flags = demo_flags("本结果已核验，可作为应急执行建议。", produced_result=True)
    assert flags["forbidden"]
    assert not flags["ok"]


def test_demo_flags_require_a_result():
    assert not demo_flags("DEMO_ONLY", produced_result=False)["ok"]


def test_params_match_normalises_lists():
    assert params_match({"disabled_shelter_ids": ["a", "b"]},
                        {"disabled_shelter_ids": ["b", "a"]}) == []


def test_paired_counts_wins_losses_ties():
    rows = [
        {"case_id": "A", "arm": "baseline", "rep": 0, "passed": False, "duration_ms": 10},
        {"case_id": "A", "arm": "harness_opt", "rep": 0, "passed": True, "duration_ms": 5},
        {"case_id": "B", "arm": "baseline", "rep": 0, "passed": True, "duration_ms": 10},
        {"case_id": "B", "arm": "harness_opt", "rep": 0, "passed": True, "duration_ms": 5},
        {"case_id": "C", "arm": "baseline", "rep": 0, "passed": True, "duration_ms": 10},
        {"case_id": "C", "arm": "harness_opt", "rep": 0, "passed": False, "duration_ms": 5},
    ]
    result = paired(rows, "baseline", "harness_opt")
    assert (result["right_wins"], result["right_losses"], result["ties"]) == (1, 1, 1)


def test_metrics_reports_inheritance_separately():
    rows = [
        {"arm": "harness_opt", "case_id": "M", "rep": 0, "passed": True, "inherits": True,
         "info": {"params_ok": True}, "failure_types": [], "llm_calls": 2, "tool_calls": 1,
         "planning_tasks": 1, "prompt_tokens": 10, "completion_tokens": 5,
         "llm_latency_ms": 100.0, "b_api_ms": 50.0, "duration_ms": 150.0, "retries": 0},
        {"arm": "harness_opt", "case_id": "P", "rep": 0, "passed": False, "inherits": False,
         "info": {"params_ok": False}, "failure_types": ["param_error"], "llm_calls": 2,
         "tool_calls": 1, "planning_tasks": 0, "prompt_tokens": 10, "completion_tokens": 5,
         "llm_latency_ms": 100.0, "b_api_ms": 50.0, "duration_ms": 150.0, "retries": 0},
    ]
    result = metrics(rows)
    assert result["inheritance_rate"] == 1.0
    assert result["param_rate"] == 0.5
    assert result["case_pass_rate"] == 0.5
