"""Cross-turn clarification and multi-session isolation.

Everything here runs against the real in-process Module B app: a clarification
that resumes into a task must still produce a real planning result.
"""
from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.service import HarnessService
from agent.structured_runner import StructuredRunner
from agent.workflow.dialogue import (
    DialogueState,
    PendingTask,
    fill_pending,
    is_cancel,
)
from backend.app import app as b_app
from tests.module_c.rule_llm import RuleLLM

SHIELD = "zs_fed820d94e"


@pytest.fixture()
def service(tmp_path):
    config = HarnessConfig()
    client = BClient(config, client=TestClient(b_app))
    svc = HarnessService(config, client=client, trace_dir=str(tmp_path / "trace"))
    yield svc
    svc.close()


def make_runner(service, session_id: str, llm=None, *, enabled: bool = True):
    return StructuredRunner("harness_opt", service, llm or RuleLLM(), service.config,
                            session_id=session_id, dialogue_enabled=enabled)


# --------------------------------------------------------------------------- #
# Slot filling (pure)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("2", 2.0),
    ("预算2", 2.0),
    ("预算 2.5", 2.5),
    ("就用2吧", 2.0),
])
def test_bare_value_fills_budget(text, expected):
    filled = fill_pending(PendingTask(task_type="optimize", missing=["budget_units"]), text)
    assert filled.complete
    assert filled.values["budget_units"] == expected


def test_unrelated_reply_does_not_fill_a_slot():
    filled = fill_pending(PendingTask(task_type="optimize", missing=["budget_units"]), "没有预算")
    assert not filled.complete
    assert filled.remaining == ["budget_units"]


def test_cancel_words_are_narrow():
    assert is_cancel("算了")
    assert is_cancel("取消")
    assert is_cancel("先不做了")
    # "stop shutting it down" is a parameter change, not a cancelled task.
    assert not is_cancel("不用停用了，撤销停用，然后评估现状")


def test_dialogue_state_round_trips():
    from agent.workflow.router import RoutePlan

    state = DialogueState()
    state.open(RoutePlan(action="clarify", task_type="optimize",
                         clarify_fields=["budget_units"], question="预算?"),
               params=None, text="帮我优化")  # type: ignore[arg-type]
    restored = DialogueState.from_dict(state.to_dict())
    assert restored.pending is not None
    assert restored.pending.task_type == "optimize"
    assert restored.pending.missing == ["budget_units"]


# --------------------------------------------------------------------------- #
# Closed loop against the real planner
# --------------------------------------------------------------------------- #
def test_three_step_clarification_resumes_the_original_task(service):
    runner = make_runner(service, "sess-loop")
    first = runner.run_turn("帮我做个扩容优化。")
    assert first.status == "NEEDS_CLARIFICATION"
    assert "budget_units" in first.clarification_fields
    assert runner.dialogue.pending is not None
    assert runner.dialogue.pending.task_type == "optimize"

    second = runner.run_turn("预算是2。")
    assert second.status == "NEEDS_CLARIFICATION"
    assert "improvement_options" in second.clarification_fields
    # The budget from turn 2 must be carried, not forgotten.
    assert runner.dialogue.pending.changes.get("budget_units") == 2

    third = runner.run_turn(f"{SHIELD} 扩容 300 人，成本 1。")
    assert third.status == "SUCCEEDED", third.text
    assert third.resumed is True
    assert third.bindings.get("result_id")
    assert runner.dialogue.pending is None
    state = service.session_state("sess-loop")
    assert state["current"]["params"]["budget_units"] == 2
    assert state["result_ids"]


def test_cancel_closes_the_pending_task_without_running_anything(service):
    runner = make_runner(service, "sess-cancel")
    runner.run_turn("帮我做个扩容优化。")
    before = service.session_state("sess-cancel")["result_ids"]

    cancelled = runner.run_turn("算了，不做了。")
    assert cancelled.status == "CANCELLED"
    assert cancelled.cancelled is True
    assert runner.dialogue.pending is None
    assert service.session_state("sess-cancel")["result_ids"] == before
    assert "取消" in cancelled.text


def test_switching_task_drops_the_pending_clarification(service):
    runner = make_runner(service, "sess-switch")
    runner.run_turn("帮我做个扩容优化。")
    switched = runner.run_turn("先不优化了，改成评估现状。")
    assert switched.status == "SUCCEEDED", switched.text
    assert runner.dialogue.pending is None
    assert runner.dialogue.last_closed in {"switched", "resumed"}
    assert switched.bindings.get("task_type") == "assess"


def test_expired_clarification_is_not_resumed(service):
    runner = make_runner(service, "sess-expired")
    runner.run_turn("帮我做个扩容优化。")
    runner.dialogue.pending.expires_at = time.time() - 1

    expired = runner.run_turn("2")
    assert expired.status == "NEEDS_CLARIFICATION"
    assert "有效期" in expired.text
    assert runner.dialogue.pending is None
    assert service.session_state("sess-expired")["result_ids"] == []


def test_reply_can_carry_an_extra_condition(service):
    runner = make_runner(service, "sess-extra")
    runner.run_turn("帮我做个扩容优化。")
    runner.run_turn("预算2，步行阈值改成20分钟。")
    # Nothing has executed yet, so the session snapshot must not move; the
    # pending task is what carries the collected values.
    assert service.session_state("sess-extra")["current"]["params"]["max_walk_time_min"] == 15
    assert runner.dialogue.pending.changes.get("budget_units") == 2
    assert runner.dialogue.pending.changes.get("max_walk_time_min") == 20


def test_two_sessions_do_not_share_pending_state_or_results(service):
    a = make_runner(service, "sess-a")
    b = make_runner(service, "sess-b")

    a.run_turn("帮我做个扩容优化。")
    b.run_turn("评估一下现状。")
    assert a.dialogue.pending is not None
    assert b.dialogue.pending is None
    b_results = list(service.session_state("sess-b")["result_ids"])

    done = a.run_turn("预算2，候选 " + SHIELD + " 扩容 300 人成本1。")
    assert done.status == "SUCCEEDED", done.text
    a_results = service.session_state("sess-a")["result_ids"]
    assert a_results
    # B's result set must be untouched, and A's ids must not appear in B.
    assert service.session_state("sess-b")["result_ids"] == b_results
    assert not set(a_results) & set(b_results)

    # B must not inherit A's budget or result. A third, empty session proves
    # that results are not visible across sessions at all.
    c = make_runner(service, "sess-c")
    c_result = c.run_turn("解释一下刚才那个结果。")
    assert c_result.status == "NEEDS_CLARIFICATION"
    assert "result_id" in c_result.clarification_fields
    assert service.session_state("sess-b")["result_ids"] == b_results


def test_dialogue_disabled_keeps_v2_behaviour(service):
    """The V2 arms must stay reproducible: no dialogue, no auto-resume."""
    runner = make_runner(service, "sess-v2", enabled=False)
    first = runner.run_turn("帮我做个扩容优化。")
    assert first.status == "NEEDS_CLARIFICATION"
    assert runner.dialogue.pending is None
    second = runner.run_turn("预算是2。")
    # V2 semantics: a bare reply is re-parsed, not resumed.
    assert second.resumed is False


def test_rule_llm_never_sees_expected_values():
    """Guard the fixture rule: it only receives the user's sentence."""
    llm = RuleLLM()
    runner = StructuredRunner("harness_opt", None, llm, HarnessConfig(),
                              session_id="sess-fixture", dialogue_enabled=False)
    assert runner is not None
    llm.chat([{"role": "user", "content": "用户：评估现状"}])
    sent = llm.conversations[-1][-1]["content"]
    assert sent == "用户：评估现状"
