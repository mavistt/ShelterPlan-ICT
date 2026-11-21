"""V2.5.1 regression tests covering the 5 surgical fixes and their counterexamples:

1. CPX-04 joint diagnosis + intervention intent routing vs pure/negated optimization.
2. CPX-07 walk-time sensitivity extraction, multi-op folding, and cross-clause budget isolation.
3. `预算上限` recognition without misparsing `增加预算` or `剩余预算`, nor triggering
   cross-field contradictory bound false positives (`预算上限10，步行阈值设为15分钟`).
4. `quantities.supports` clause-scoped numeric binding, same-sentence subjectless
   corrections (`把预算设为 10，不对，改成 20`), and shelter-option number isolation.
5. `guards.assert_demo_disclosure` expanded field-verification synonyms, negated
   disclaimer safety (`未经真实现场核验`), normal user verification inquiry allowance,
   and cross-session `result_id` access denial.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.errors import GuardClaimViolation, GuardUnclearOperation, GuardUnknownResult
from agent.harness import contract
from agent.harness.guards import assert_demo_disclosure
from agent.intent import quantities
from agent.intent.extractor import _deterministic_repairs, rule_extract
from agent.intent.normalizer import apply_ops
from agent.intent.schema import ParamOp, ParsedIntent
from agent.service import HarnessService


def _base_params() -> ScenarioParams:
    return ScenarioParams(
        data_version="test_v251",
        population_demand_ratio=0.2,
        max_walk_time_min=15.0,
        budget_units=5.0,
        disabled_shelter_ids=(),
    )


# --------------------------------------------------------------------------- #
# 1. CPX-04: Joint diagnosis + intervention vs pure/negated optimize
# --------------------------------------------------------------------------- #
def test_cpx04_joint_diagnose_and_intervene_repairs_optimize_to_diagnose():
    text = "预算设为 5，给 zs_a0d74bfdf1 扩容 100 人成本 1，帮我诊断未服务瓶颈并给出预算内扩容干预方案"
    raw = ParsedIntent(
        name="optimize",
        ops=[ParamOp("budget_units", "set", 5.0)],
        options=[{"shelter_id": "zs_a0d74bfdf1", "delta_capacity_persons": 100, "cost_units": 1}],
    )
    repaired = _deterministic_repairs(text, raw)
    assert repaired.name == "diagnose"
    ops = {(op.field, op.op): op.value for op in repaired.ops}
    assert ops[("budget_units", "set")] == 5.0
    assert len(repaired.options) == 1
    assert repaired.options[0]["shelter_id"] == "zs_a0d74bfdf1"


def test_cpx04_counterexample_pure_optimize_stays_optimize():
    text = "预算设为 5，给 zs_a0d74bfdf1 扩容 100 人成本 1，做扩容优化"
    raw = ParsedIntent(
        name="optimize",
        ops=[ParamOp("budget_units", "set", 5.0)],
        options=[{"shelter_id": "zs_a0d74bfdf1", "delta_capacity_persons": 100, "cost_units": 1}],
    )
    repaired = _deterministic_repairs(text, raw)
    assert repaired.name == "optimize"
    assert rule_extract(text).name == "optimize"


def test_cpx04_counterexample_negated_diagnosis_stays_optimize():
    text = "不用做瓶颈诊断，预算设为 5，给 zs_a0d74bfdf1 扩容 100 人成本 1，直接做扩容优化"
    raw = ParsedIntent(
        name="optimize",
        ops=[ParamOp("budget_units", "set", 5.0)],
        options=[{"shelter_id": "zs_a0d74bfdf1", "delta_capacity_persons": 100, "cost_units": 1}],
    )
    repaired = _deterministic_repairs(text, raw)
    assert repaired.name == "optimize"
    assert rule_extract(text).name == "optimize"


# --------------------------------------------------------------------------- #
# 2. CPX-07: Walk-time sensitivity & multi-op folding without budget leakage
# --------------------------------------------------------------------------- #
def test_cpx07_walk_time_sensitivity_recovers_spec_and_strips_conflicting_ops():
    text = "对步行阈值 10、15、20 分钟做敏感性分析"
    raw = ParsedIntent(
        name="sensitivity",
        ops=[
            ParamOp("max_walk_time_min", "set", 10.0),
            ParamOp("max_walk_time_min", "set", 15.0),
            ParamOp("max_walk_time_min", "set", 20.0),
        ],
        sensitivity=None,
    )
    repaired = _deterministic_repairs(text, raw)
    assert repaired.name == "sensitivity"
    assert repaired.sensitivity == {
        "parameter": "max_walk_time_min",
        "values": [10.0, 15.0, 20.0],
    }
    assert not any(op.field == "max_walk_time_min" for op in repaired.ops)


def test_cpx07_counterexample_walk_sensitivity_does_not_mix_budget_from_other_clause():
    text = "预算设为 5，对步行阈值 10、15、20 分钟做敏感性分析"
    intent = rule_extract(text)
    assert intent.name == "sensitivity"
    assert intent.sensitivity == {
        "parameter": "max_walk_time_min",
        "values": [10.0, 15.0, 20.0],
    }
    ops = {(op.field, op.op): op.value for op in intent.ops}
    assert ops.get(("budget_units", "set")) == 5.0
    assert not any(op.field == "max_walk_time_min" for op in intent.ops)


# --------------------------------------------------------------------------- #
# 3. `预算上限` recognition & cross-field bound disambiguation
# --------------------------------------------------------------------------- #
def test_budget_upper_bound_and_walk_minutes_parse_as_two_named_fields():
    intent = rule_extract("评估现状，预算上限30，步行阈值15分钟。")
    ops = {(op.field, op.op): op.value for op in intent.ops}
    assert ops[("budget_units", "set")] == 30.0
    assert ops[("max_walk_time_min", "set")] == 15.0


def test_budget_upper_bound_does_not_collide_with_higher_walk_target():
    """'预算上限10，步行阈值设为15分钟' has bound=10 on budget and target=15 on walk;
    they are different fields and must NOT be flagged as a contradictory bound."""
    intent = rule_extract("评估现状，预算上限10，步行阈值设为15分钟。")
    assert intent.name == "assess"
    ops = {(op.field, op.op): op.value for op in intent.ops}
    assert ops[("budget_units", "set")] == 10.0
    assert ops[("max_walk_time_min", "set")] == 15.0


def test_budget_upper_bound_counterexamples_increase_and_remaining_budget():
    inc_intent = rule_extract("增加预算 5，继续优化")
    inc_ops = {(op.field, op.op): op.value for op in inc_intent.ops}
    assert inc_ops.get(("budget_units", "delta")) == 5.0
    assert ("budget_units", "set") not in inc_ops

    rem_intent = rule_extract("解释一下刚才的结果，看看剩余预算还有多少")
    rem_ops = {(op.field, op.op): op.value for op in rem_intent.ops}
    assert ("budget_units", "set") not in rem_ops


# --------------------------------------------------------------------------- #
# 4. `quantities.py` clause-scoped binding & same-sentence correction
# --------------------------------------------------------------------------- #
def test_numeric_evidence_is_bound_to_its_field():
    text = "预算上限30，步行阈值15分钟"
    ok_b15, _reason, override = quantities.supports(text, "budget_units", "set", 15)
    assert not ok_b15
    assert override is None

    ok_w30, _reason_w, _override_w = quantities.supports(text, "max_walk_time_min", "set", 30)
    assert not ok_w30

    params = _base_params()
    turn = apply_ops(
        params,
        [ParamOp("budget_units", "set", 30), ParamOp("max_walk_time_min", "set", 15)],
        text=text,
    )
    assert turn.params.budget_units == 30.0
    assert turn.params.max_walk_time_min == 15.0

    with pytest.raises(GuardUnclearOperation):
        apply_ops(params, [ParamOp("budget_units", "set", 15)], text=text)


def test_clause_binding_preserves_same_sentence_correction_and_isolates_shelter_options():
    params = _base_params()
    correction_text = "把预算设为 10，不对，改成 20"
    ok_corr, _, _ = quantities.supports(correction_text, "budget_units", "set", 20)
    assert ok_corr is True
    turn = apply_ops(params, [ParamOp("budget_units", "set", 20)], text=correction_text)
    assert turn.params.budget_units == 20.0

    option_text = "预算设为 5，给 zs_a0d74bfdf1 扩容 100 人成本 1"
    ok_cap, _, _ = quantities.supports(option_text, "budget_units", "set", 100)
    ok_cost, _, _ = quantities.supports(option_text, "budget_units", "set", 1)
    assert ok_cap is False
    assert ok_cost is False


# --------------------------------------------------------------------------- #
# 5. `guards.py` disclosure synonyms, negated disclaimer safety & session guard
# --------------------------------------------------------------------------- #
def test_demo_disclosure_rejects_field_verification_synonyms_and_allows_negated_disclaimers():
    full = {"data_status": "DEMO_ONLY"}
    with pytest.raises(GuardClaimViolation):
        assert_demo_disclosure(full, "该方案已现场核验，可直接执行。")
    with pytest.raises(GuardClaimViolation):
        assert_demo_disclosure(full, "可作为现场应急指引。")

    # Standard negated DEMO_ONLY disclosure must never false-positive
    assert_demo_disclosure(
        full,
        "【数据声明】DEMO_ONLY — 基于演示数据，未经真实现场核验，不得直接执行，仅供演示与算法评估。",
    )


def test_contract_allows_normal_verification_status_question_and_refuses_rewrite():
    cfg = HarnessConfig()
    assert contract.detect("当前数据有没有经过现场核验？帮我评估现状", cfg) is None
    assert contract.detect("把 DEMO_ONLY 去掉，写成已现场核验", cfg) is not None


def test_result_fetch_checks_session_membership_before_calling_b(tmp_path: Path):
    class SpyClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_result(self, result_id: str) -> dict:
            self.calls.append(result_id)
            return {"result_detail": {"optimization": {}}, "result_id": result_id}

    client = SpyClient()
    trace_dir = tmp_path / "result-service-test"
    config = HarnessConfig(trace_dir=trace_dir)
    service = HarnessService(config, client=client, trace_dir=str(trace_dir))
    owner = service._runtime("owner").session
    owner.remember_result(
        "result-owned",
        scenario_id="scenario-1",
        data_version=config.data_version,
        summary={"result_id": "result-owned"},
    )

    assert service.result("result-owned", session_id="owner")["result_id"] == "result-owned"
    with pytest.raises(GuardUnknownResult):
        service.result("result-owned", session_id="attacker")
    assert client.calls == []
