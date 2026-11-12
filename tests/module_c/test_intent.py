"""Offline tests for the structured intent layer.

No model is involved: a scripted LLM replays canned JSON so these tests pin the
parser, the repair ladder and the rule fallback, not model quality.
"""
from __future__ import annotations

import pytest

from agent.contracts import ScenarioParams
from agent.errors import HarnessError
from agent.intent.extractor import IntentExtractor, _deterministic_repairs, rule_extract
from agent.intent.normalizer import apply_ops, expand_options
from agent.intent.schema import (
    IntentParseError,
    ParamOp,
    ParsedIntent,
    parse_intent_payload,
    repair_json,
)
from tests.module_c.scripted_llm import ScriptedLLM, ScriptedResponse, json_response, ok_usage

PARAMS = ScenarioParams(0.2, 15.0, 6.0, ("zs_60213118ec",), "zhongshan_pilot_v0.1", "demo")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def test_parse_minimal_intent():
    intent = parse_intent_payload({"intent": "assess"})
    assert intent.name == "assess"
    assert intent.ops == []
    assert intent.source == "llm"


def test_aliases_and_percent_value():
    intent = parse_intent_payload({
        "intent": "Modify_Scenario",
        "ops": [{"field": "预算", "op": "设为", "value": "30%"}],
    })
    assert intent.name == "modify_scenario"
    assert intent.ops[0].field == "budget_units"
    assert intent.ops[0].op == "set"
    assert intent.ops[0].value == pytest.approx(0.3)


def test_unknown_intent_is_fatal():
    with pytest.raises(IntentParseError):
        parse_intent_payload({"intent": "delete_everything"})


def test_unknown_field_is_dropped_with_warning():
    intent = parse_intent_payload({
        "intent": "assess",
        "ops": [{"field": "weather", "op": "set", "value": 1}],
    })
    assert intent.ops == []
    assert any("unknown field" in warning for warning in intent.warnings)


def test_unknown_operator_is_dropped_not_fatal():
    intent = parse_intent_payload({
        "intent": "assess",
        "ops": [{"field": "budget_units", "op": "explode", "value": 1},
                {"field": "budget_units", "op": "set", "value": 2}],
    })
    assert [op.op for op in intent.ops] == ["set"]
    assert intent.ops[0].value == 2.0


def test_json_repair_variants():
    cases = [
        '```json\n{"intent": "assess"}\n```',
        '好的，结果如下：{"intent": "assess"} 完毕',
        '{"intent": "assess",}',
        '{\u201cintent\u201d: \u201cassess\u201d}',
    ]
    for text in cases:
        assert repair_json(text) == {"intent": "assess"}, text


def test_json_repair_returns_none_for_prose():
    assert repair_json("我不确定用户想要什么") is None


# --------------------------------------------------------------------------- #
# Deterministic safety repairs
# --------------------------------------------------------------------------- #
def test_negative_budget_literal_becomes_set():
    intent = ParsedIntent("optimize", [ParamOp("budget_units", "delta", -5.0)])
    fixed = _deterministic_repairs("预算-5，帮我做优化。", intent)
    assert [(op.op, op.value) for op in fixed.ops] == [("set", -5.0)]


def test_ratio_literal_overrides_multiplier():
    intent = ParsedIntent("modify_scenario", [ParamOp("population_demand_ratio", "scale", 1.5)])
    fixed = _deterministic_repairs("需求比例按150%评估一下现状。", intent)
    assert fixed.ops[0].op == "set"
    assert fixed.ops[0].value == pytest.approx(1.5)


def test_delta_percentage_phrasing_is_not_rewritten():
    intent = ParsedIntent("modify_scenario", [ParamOp("population_demand_ratio", "delta", 5.0)])
    fixed = _deterministic_repairs("比例再加5个百分点", intent)
    assert fixed.ops[0].op == "delta"


def test_unmentioned_shelter_is_dropped():
    intent = ParsedIntent("stress_test", [ParamOp("disabled_shelter_ids", "set", ["zs_hallucinated"])])
    fixed = _deterministic_repairs("模拟关停一个场所，看看影响。", intent)
    assert fixed.ops == []
    assert "disabled_shelter_ids" in fixed.clarify


def test_expansion_request_is_not_a_shutdown():
    intent = ParsedIntent("optimize", [ParamOp("disabled_shelter_ids", "set", ["zs_fed820d94e"])],
                          options=[])
    fixed = _deterministic_repairs(
        "预算6，候选措施：给 zs_fed820d94e 扩容300人，成本1。做扩容优化。", intent)
    assert fixed.ops == []
    assert fixed.options and fixed.options[0]["delta_capacity_persons"] == 300.0


def test_compare_without_comparison_wording_is_downgraded():
    intent = ParsedIntent("compare", compare={"field": "population_demand_ratio", "values": [0.2, 0.35]})
    fixed = _deterministic_repairs("按需求比例0.35评估一下现状。", intent)
    assert fixed.name == "assess"
    assert fixed.compare is None


# --------------------------------------------------------------------------- #
# Rule fallback
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("评估一下现状", "assess"),
    ("预算2做扩容优化", "optimize"),
    ("比较需求比例0.2和0.4", "compare"),
    ("预算0、1、2三档做敏感性分析", "sensitivity"),
    ("假设关停 zs_1b4df7920b 做压力测试", "stress_test"),
    ("帮我诊断一下瓶颈原因", "diagnose"),
    ("解释一下刚才的结果", "explain"),
])
def test_rule_intent_mapping(text, expected):
    assert rule_extract(text).name == expected


def test_rule_relative_ops():
    assert rule_extract("预算减半").ops[0].to_dict() == {"field": "budget_units", "op": "scale", "value": 0.5}
    assert rule_extract("预算再加2").ops[0].to_dict() == {"field": "budget_units", "op": "delta", "value": 2.0}


def test_rule_options_from_natural_language():
    options = rule_extract("预算2，候选措施：给 zs_fed820d94e 扩容300人，成本1，做优化").options
    assert options == [{"shelter_id": "zs_fed820d94e",
                        "delta_capacity_persons": 300.0, "cost_units": 1.0}]


# --------------------------------------------------------------------------- #
# Extraction ladder
# --------------------------------------------------------------------------- #
def test_extractor_single_call():
    llm = ScriptedLLM([json_response('{"intent":"assess","ops":[],"confidence":0.9}')])
    result = IntentExtractor(llm).extract("评估一下现状", params=PARAMS)
    assert result.mode == "llm"
    assert result.llm_calls == 1
    assert result.intent.name == "assess"


def test_extractor_repairs_fenced_json_without_second_call():
    llm = ScriptedLLM([json_response('```json\n{"intent":"assess"}\n```')])
    result = IntentExtractor(llm).extract("评估一下现状", params=PARAMS)
    assert result.mode == "llm_repaired"
    assert result.llm_calls == 1


def test_extractor_retries_once_on_invalid_schema():
    llm = ScriptedLLM([
        json_response('{"intent":"nonsense"}'),
        json_response('{"intent":"assess"}'),
    ])
    result = IntentExtractor(llm).extract("评估一下现状", params=PARAMS)
    assert result.mode == "llm_retry"
    assert result.llm_calls == 2
    assert result.intent.name == "assess"


def test_extractor_falls_back_to_rules_after_two_failures():
    llm = ScriptedLLM([json_response("抱歉，我不明白"), json_response("还是不明白")])
    result = IntentExtractor(llm).extract("预算减半，其它不变", params=PARAMS)
    assert result.mode == "rules_fallback"
    assert result.intent.name == "modify_scenario"
    assert result.intent.ops[0].op == "scale"


def test_extractor_survives_transport_error():
    class Broken:
        def chat(self, *args, **kwargs):
            raise HarnessError("LLM endpoint unreachable")

    result = IntentExtractor(Broken()).extract("评估一下现状", params=PARAMS)
    assert result.mode == "rules_fallback"
    assert result.intent.name == "assess"


# --------------------------------------------------------------------------- #
# Normalisation and option expansion
# --------------------------------------------------------------------------- #
def test_apply_ops_keeps_untouched_fields():
    turn = apply_ops(PARAMS, [ParamOp("max_walk_time_min", "set", 20)])
    assert turn.params.max_walk_time_min == 20
    assert turn.params.population_demand_ratio == 0.2
    assert turn.params.disabled_shelter_ids == ("zs_60213118ec",)
    assert turn.changes == {"max_walk_time_min": 20.0}


def test_apply_ops_no_change_is_empty():
    assert apply_ops(PARAMS, [ParamOp("budget_units", "set", 6)]).changes == {}


def test_apply_ops_scale_without_baseline_asks():
    params = ScenarioParams(0.2, 15.0, None, (), "zhongshan_pilot_v0.1", "demo")
    with pytest.raises(HarnessError) as excinfo:
        apply_ops(params, [ParamOp("budget_units", "scale", 0.5)])
    assert excinfo.value.code == "GUARD_MISSING_PARAM"
    assert excinfo.value.details["field"] == "budget_units"


def test_restore_is_field_scoped():
    from agent.contracts import ScenarioState
    history = [
        ScenarioState("s1", None, PARAMS, {}, 1, None, None),
        ScenarioState("s2", None, PARAMS, {}, 2, None, None),
    ]
    turn = apply_ops(PARAMS, [ParamOp("budget_units", "set", 3),
                              ParamOp("max_walk_time_min", "restore", "previous")],
                     history=history, defaults=PARAMS)
    # Restoring the walk threshold must not rewind the budget changed this turn.
    assert turn.params.budget_units == 3
    assert turn.params.max_walk_time_min == 15


def test_apply_ops_rejects_out_of_range_ratio():
    with pytest.raises(HarnessError) as excinfo:
        apply_ops(PARAMS, [ParamOp("population_demand_ratio", "set", 1.5)])
    assert excinfo.value.code == "GUARD_INVALID_PARAM"


def test_expand_options_fills_b_contract():
    rows = expand_options([{"shelter_id": "zs_fed820d94e",
                            "delta_capacity_persons": 300, "cost_units": 1}])
    assert rows[0]["action"] == "hypothetical_capacity_increase"
    assert rows[0]["hypothetical"] is True
    assert rows[0]["human_confirmed"] is False
    assert rows[0]["option_id"]


def test_expand_options_drops_invalid_rows():
    assert expand_options([{"shelter_id": "", "delta_capacity_persons": 300}]) == []
    assert expand_options([{"shelter_id": "zs_x", "delta_capacity_persons": 0}]) == []
