"""General wording semantics for the C close-out.

These are *authored* regressions about how a user phrases things, not a rewrite
of any frozen case id. Four areas the V2.2 post-fix run left open:

* a duration increment ("减少 3 分钟", "再放宽半小时") is a delta, not a target;
* a corrected or historical parameter ("不对，改成 0.45" / "按上一轮的预算")
  must not be replaced by a model guess;
* asking about a result with no result in the session must ask for `result_id`,
  not a generic "which task?";
* an optimise/compare request whose candidate table or comparison values are in
  the sentence must keep those slots through a clarification and a resume.

Everything runs through the real in-process Module B app; the model substitute
is the project's rule extractor, so a passing test tests the semantics, not the
model.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.intent.extractor import rule_extract
from agent.intent.normalizer import apply_ops
from agent.service import HarnessService
from agent.structured_runner import StructuredRunner
from backend.app import app as b_app
from tests.module_c.rule_llm import RuleLLM

SHIELD = "zs_fed820d94e"


@pytest.fixture()
def params():
    config = HarnessConfig()
    return ScenarioParams(data_version=config.data_version,
                          population_demand_ratio=0.2, max_walk_time_min=15.0,
                          budget_units=3.0, disabled_shelter_ids=())


@pytest.fixture()
def service(tmp_path):
    config = HarnessConfig()
    svc = HarnessService(config, client=BClient(config, client=TestClient(b_app)),
                         trace_dir=str(tmp_path / "trace"))
    yield svc
    svc.close()


def runner(service, session_id):
    return StructuredRunner("harness_opt", service, RuleLLM(), service.config,
                            session_id=session_id, dialogue_enabled=True)


def state(service, session_id):
    return service.session_state(session_id)["current"]["params"]


def apply(text, params):
    """Extraction -> grounding -> normalisation, the same path the runner uses."""
    intent = rule_extract(text)
    return apply_ops(params, intent.ops, defaults=params, text=text)


# --------------------------------------------------------------------------- #
# 1. duration increments
# --------------------------------------------------------------------------- #
def test_duration_decrement_is_a_delta_not_a_target(params):
    result = apply("步行阈值减少3分钟，重新评估。", params)
    assert result.changes["max_walk_time_min"] == 12.0


def test_duration_increment_phrase_without_a_literal_number(params):
    result = apply("步行阈值再放宽半小时，评估。", params)
    assert result.changes["max_walk_time_min"] == 45.0


@pytest.mark.parametrize("text,expected", [
    ("步行阈值改成20分钟，评估。", 20.0),
    ("步行阈值提高到半小时，评估。", 30.0),
    ("需求比例30%，步行阈值半小时，评估现状。", 30.0),
])
def test_absolute_duration_phrases_stay_absolute(params, text, expected):
    assert apply(text, params).changes.get("max_walk_time_min") == expected


# --------------------------------------------------------------------------- #
# 2. corrected / historical parameter references
# --------------------------------------------------------------------------- #
def test_corrected_target_beats_the_earlier_value(params):
    result = apply("需求比例先按30%算……不对，改成0.45，然后评估。", params)
    assert result.changes["population_demand_ratio"] == pytest.approx(0.45)


def test_history_reference_resolves_against_the_previous_snapshot(service):
    run = runner(service, "sem-history")
    first = run.run_turn("预算设为5，评估现状。")
    assert first.status == "SUCCEEDED", first.text
    assert state(service, "sem-history")["budget_units"] == 5.0

    second = run.run_turn("按上一轮的预算再评估。")
    assert second.status == "SUCCEEDED", second.text
    assert state(service, "sem-history")["budget_units"] == 5.0
    assert "history reference" in " ".join((second.intent or {}).get("warnings") or [])


def test_plain_target_is_not_treated_as_a_correction(params):
    result = apply("预算改成2，步行阈值改成20分钟，评估。", params)
    assert result.changes["budget_units"] == 2.0
    assert result.changes["max_walk_time_min"] == 20.0


# --------------------------------------------------------------------------- #
# 3. asking about a missing result
# --------------------------------------------------------------------------- #
def test_result_question_without_a_result_asks_for_the_result_id(service):
    record = runner(service, "sem-no-result").run_turn("上次那个结果怎么样？")
    assert record.status == "NEEDS_CLARIFICATION", record.text
    assert "result_id" in record.clarification_fields
    assert "结果" in record.text
    assert service.session_state("sem-no-result")["result_ids"] == []


# --------------------------------------------------------------------------- #
# 4. optimise / compare slots
# --------------------------------------------------------------------------- #
def test_negated_shutdown_is_not_a_stress_test(service):
    text = f"给 {SHIELD} 扩容200人、成本1，预算2；但不要停用任何场所。"
    record = runner(service, "sem-neg-disable").run_turn(text)
    assert record.status == "SUCCEEDED", record.text
    current = state(service, "sem-neg-disable")
    assert current["budget_units"] == 2.0
    assert current["disabled_shelter_ids"] == []
    assert record.bindings.get("task_type") == "optimize"


def test_paired_values_recover_a_comparison(service):
    record = runner(service, "sem-compare").run_turn(
        "比较预算2和预算5两种情况的覆盖差异。")
    assert record.status == "SUCCEEDED", record.text
    assert record.bindings.get("task_type") == "compare"


def test_clarification_keeps_confirmed_slots_for_resume(service):
    """A candidate table supplied before a budget question must survive it."""
    from agent.workflow.dialogue import DialogueState, resume_intent
    from agent.workflow.router import TurnMemory, route

    config = HarnessConfig()
    baseline = ScenarioParams(
        data_version=config.data_version, population_demand_ratio=0.2,
        max_walk_time_min=15.0, budget_units=None, disabled_shelter_ids=())
    intent = rule_extract(f"给 {SHIELD} 扩容300人、成本1，做扩容优化。")
    plan = route(intent, params=baseline, changes={}, memory=TurnMemory(), config=config)
    assert plan.action == "clarify"
    assert plan.options, "the confirmed candidate table must be carried"
    assert plan.clarify_fields == ["budget_units"]

    dialogue = DialogueState()
    pending = dialogue.open(plan, params=baseline, text="做扩容优化")
    assert pending.options == plan.options
    resumed = resume_intent(pending, {"budget_units": 2.0})
    assert resumed.options == plan.options
    assert any(op.field == "budget_units" and op.value == 2.0 for op in resumed.ops)


# --------------------------------------------------------------------------- #
# 5. contradictory bounds and slots the model omitted
# --------------------------------------------------------------------------- #
def test_contradictory_bound_is_a_question_not_a_plan(service):
    record = runner(service, "sem-contradiction").run_turn(
        "需求比例改成0.4，但要求不超过0.2，评估现状。")
    assert record.status == "NEEDS_CLARIFICATION", record.text
    assert service.session_state("sem-contradiction")["result_ids"] == []


def test_compatible_bound_does_not_block():
    from agent.intent.extractor import _contradictory_bound

    assert _contradictory_bound("预算改成2，不超过5，评估现状。") is None
    assert _contradictory_bound("需求比例改成0.4，但要求不超过0.2，评估现状。")


def test_stated_budget_survives_an_empty_model_ops_list():
    from agent.intent.extractor import _deterministic_repairs
    from agent.intent.schema import ParsedIntent

    text = "给 zs_fed820d94e 扩容200人、成本1，预算2；但不要停用任何场所。"
    fixed = _deterministic_repairs(text, ParsedIntent(name="optimize", ops=[]))
    assert fixed.name == "optimize"
    assert any(op.field == "budget_units" and op.value == 2.0 for op in fixed.ops)
