"""Quantity phrasing: sign, multiplier, unit, and operator semantics.

These are the V2.1 P0 defects, one test per symptom:

* "成本-1" used to be read as cost **1** (the minus was dropped by the option
  regex), i.e. a free expansion;
* "预算翻倍" arrived as `scale: 6` (3 -> 18) and "步行阈值半小时" as `scale: 7.5`
  (15 -> 112.5 minutes, which made Module B time out);
* an operation with no trace in the sentence (a `scale` nobody asked for) reached
  Module B instead of being questioned.

The tests are phrased the way a user would type, because the rule layer is only
worth anything if it survives real phrasing.
"""
from __future__ import annotations

import pytest

from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.intent import quantities
from agent.intent.extractor import rule_extract
from agent.intent.normalizer import apply_ops
from agent.intent.schema import ParamOp


@pytest.fixture()
def params():
    config = HarnessConfig()
    return ScenarioParams(data_version=config.data_version,
                          population_demand_ratio=0.2, max_walk_time_min=15.0,
                          budget_units=3.0, disabled_shelter_ids=())


def ops_of(text):
    return {(op.field, op.op): op.value for op in rule_extract(text).ops}


# --------------------------------------------------------------------------- #
# 1. sign preservation
# --------------------------------------------------------------------------- #
def test_negative_cost_survives_extraction():
    options = rule_extract("预算3，做扩容优化，候选是 zs_fed820d94e 扩容500人、成本-1。").options
    assert options and options[0]["cost_units"] == -1.0, options


def test_negative_capacity_survives_extraction():
    options = rule_extract("预算3，做扩容优化，候选是 zs_fed820d94e 扩容-500人、成本1。").options
    assert options and options[0]["delta_capacity_persons"] == -500.0, options


def test_negative_budget_stays_negative(params):
    """A literal negative is a target value, not a multiplier, and must reach the guard."""
    assert ops_of("预算-3，做优化。")[("budget_units", "set")] == -3.0
    with pytest.raises(Exception) as caught:
        apply_ops(params, [ParamOp("budget_units", "set", -3.0)], text="预算-3，做优化。")
    assert caught.value.__class__.__name__ == "GuardInvalidParam"


# --------------------------------------------------------------------------- #
# 2. multipliers -> scale, units -> set
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text, expected", [
    ("预算翻倍，评估现状。", 2.0),
    ("预算减半，评估现状。", 0.5),
    ("预算翻一番，评估现状。", 2.0),
    ("预算提高到两倍，评估现状。", 2.0),
    ("预算提高20%，评估现状。", 1.2),
    ("预算降低25%，评估现状。", 0.75),
])
def test_budget_multiplier_phrases(text, expected):
    assert ops_of(text)[("budget_units", "scale")] == pytest.approx(expected)


@pytest.mark.parametrize("text, expected", [
    ("步行阈值半小时，评估现状。", 30.0),
    ("步行阈值一个半小时，评估现状。", 90.0),
    ("步行阈值一刻钟，评估现状。", 15.0),
    ("步行阈值2小时，评估现状。", 120.0),
    ("步行阈值25分钟，评估现状。", 25.0),
])
def test_walk_duration_phrases(text, expected):
    assert ops_of(text)[("max_walk_time_min", "set")] == pytest.approx(expected)


@pytest.mark.parametrize("text, expected", [
    ("需求比例30%，评估现状。", 0.3),
    ("需求比例按三成算，评估现状。", 0.3),
    ("需求比例0.45，评估现状。", 0.45),
])
def test_ratio_phrases(text, expected):
    assert ops_of(text)[("population_demand_ratio", "set")] == pytest.approx(expected)


def test_budget_delta_is_not_a_scale():
    assert ops_of("预算增加2，评估现状。")[("budget_units", "delta")] == 2.0
    assert ops_of("预算减少2，评估现状。")[("budget_units", "delta")] == -2.0


def test_two_clauses_do_not_leak_into_each_other():
    """The half-hour belongs to the walk threshold, not to the budget."""
    ops = ops_of("需求比例30%，步行阈值半小时，预算翻倍，评估现状。")
    assert ops[("population_demand_ratio", "set")] == pytest.approx(0.3)
    assert ops[("max_walk_time_min", "set")] == pytest.approx(30.0)
    assert ops[("budget_units", "scale")] == pytest.approx(2.0)


def test_multiplier_is_applied_to_the_current_value(params):
    assert apply_ops(params, [ParamOp("budget_units", "scale", 2.0)],
                     text="预算翻倍").changes == {"budget_units": 6.0}
    assert apply_ops(params, [ParamOp("budget_units", "scale", 0.5)],
                     text="预算减半").changes == {"budget_units": 1.5}


# --------------------------------------------------------------------------- #
# 3. no unfounded arithmetic
# --------------------------------------------------------------------------- #
def test_scale_without_a_multiplier_is_refused(params):
    with pytest.raises(Exception) as caught:
        apply_ops(params, [ParamOp("budget_units", "scale", 6.0)], text="把预算调一下。")
    assert caught.value.__class__.__name__ == "GuardUnclearOperation"


def test_set_with_a_number_the_user_never_said_is_refused(params):
    with pytest.raises(Exception) as caught:
        apply_ops(params, [ParamOp("max_walk_time_min", "set", 42.0)], text="把步行阈值调一下。")
    assert caught.value.__class__.__name__ == "GuardUnclearOperation"


def test_delta_without_a_delta_verb_is_refused(params):
    with pytest.raises(Exception) as caught:
        apply_ops(params, [ParamOp("budget_units", "delta", 5.0)], text="预算的事情等会儿说。")
    assert caught.value.__class__.__name__ == "GuardUnclearOperation"


def test_restore_without_restore_wording_is_refused(params):
    with pytest.raises(Exception) as caught:
        apply_ops(params, [ParamOp("budget_units", "restore", "previous")], text="看看现在的情况。")
    assert caught.value.__class__.__name__ == "GuardUnclearOperation"


def test_model_multiplier_is_corrected_by_the_sentence(params):
    """`scale: 6` on "翻倍" is not a misunderstanding to ask about - it is wrong."""
    result = apply_ops(params, [ParamOp("budget_units", "scale", 6.0)], text="预算翻倍")
    assert result.changes == {"budget_units": 6.0}
    assert any("6.0" in warning and "2.0" in warning for warning in result.warnings), result.warnings


def test_restore_is_field_scoped(params):
    """Restoring the budget must not rewind the walk threshold."""
    from agent.contracts import ScenarioState
    earlier = ScenarioState(scenario_id="c-1", parent_scenario_id=None,
                            params=params.merged({"budget_units": 1.0}),
                            changes={"budget_units": 1.0}, turn=1)
    later = ScenarioState(scenario_id="c-2", parent_scenario_id="c-1",
                          params=params.merged({"budget_units": 9.0,
                                                "max_walk_time_min": 40.0}),
                          changes={"budget_units": 9.0, "max_walk_time_min": 40.0}, turn=2)
    result = apply_ops(later.params, [ParamOp("budget_units", "restore", "previous")],
                       text="把预算恢复到上一轮", history=[earlier, later])
    assert result.changes == {"budget_units": 1.0}
    assert result.params.max_walk_time_min == 40.0


def test_unknown_field_is_still_rejected(params):
    """Trust-bearing fields are not scenario parameters and cannot be set by ops."""
    with pytest.raises(Exception) as caught:
        apply_ops(params, [ParamOp("analysis_mode", "set", "verified")],
                  text="用 verified 模式跑一次")
    assert caught.value.__class__.__name__ == "GuardInvalidParam"


# --------------------------------------------------------------------------- #
# 4. the helpers themselves
# --------------------------------------------------------------------------- #
def test_supports_reports_override_rather_than_failure():
    ok, reason, override = quantities.supports("预算翻倍", "budget_units", "scale", 6.0)
    assert ok is True and override == 2.0 and reason


def test_supports_accepts_a_derived_ratio():
    ok, _reason, override = quantities.supports("需求比例按三成", "population_demand_ratio",
                                                "set", 0.3)
    assert ok is True and override is None
