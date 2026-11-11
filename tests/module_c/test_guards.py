"""Guard tests: what the harness refuses before and after calling Module B."""
from __future__ import annotations

import pytest

from agent.config import HarnessConfig
from agent.contracts import ScenarioParams, ScenarioState
from agent.errors import (
    GuardClaimViolation,
    GuardInvalidOption,
    GuardInvalidParam,
    GuardMissingParam,
    GuardNotVerifiedSource,
    GuardUnknownResult,
    GuardVersionMismatch,
    ResultMismatch,
)
from agent.harness import guards
from agent.harness.session import Session


def state(**overrides) -> ScenarioState:
    params = ScenarioParams(
        population_demand_ratio=overrides.get("population_demand_ratio", 0.2),
        max_walk_time_min=overrides.get("max_walk_time_min", 15.0),
        budget_units=overrides.get("budget_units"),
        disabled_shelter_ids=tuple(overrides.get("disabled_shelter_ids", ())),
        data_version=overrides.get("data_version", "zhongshan_pilot_v0.1"),
        analysis_mode=overrides.get("analysis_mode", "demo"),
    )
    return ScenarioState(scenario_id="c-x-t01-00000000", parent_scenario_id=None,
                         params=params, changes={}, turn=1)


def preflight(task_type="assess", *, config=None, session=None, **kwargs):
    config = config or HarnessConfig()
    session = session or Session("s-guard", state().params)
    return guards.preflight(task_type=task_type, state=kwargs.pop("state", state()),
                            config=config, session=session, **kwargs)


@pytest.mark.parametrize("value", [-0.1, 1.1, "0.3", float("nan")])
def test_ratio_out_of_range_is_rejected(value):
    with pytest.raises(GuardInvalidParam):
        guards.validate_changes({"population_demand_ratio": value})


@pytest.mark.parametrize("value", [-5, 1e12, "10", float("inf")])
def test_budget_out_of_range_is_rejected(value):
    with pytest.raises(GuardInvalidParam):
        guards.validate_changes({"budget_units": value})


def test_unknown_change_field_is_rejected():
    with pytest.raises(GuardInvalidParam):
        guards.validate_changes({"population": 0.3})


def test_missing_budget_blocks_optimize():
    with pytest.raises(GuardMissingParam) as excinfo:
        preflight("optimize")
    assert excinfo.value.code == "GUARD_MISSING_PARAM"


def test_zero_budget_is_allowed():
    checked = preflight("optimize", state=state(budget_units=0.0))
    assert checked["parameters"] == {}


def test_verified_mode_blocked_on_demo_source():
    with pytest.raises(GuardNotVerifiedSource):
        preflight(state=state(analysis_mode="verified"))


def test_data_version_mismatch_is_rejected():
    with pytest.raises(GuardVersionMismatch):
        preflight(state=state(data_version="other-version"))


def test_compare_needs_at_least_two_scenarios():
    with pytest.raises(GuardInvalidParam):
        preflight("compare", parameters={"scenarios": [{"population_demand_ratio": 0.1}]})


def test_compare_rejects_unknown_override_fields():
    with pytest.raises(GuardInvalidParam):
        preflight("compare", parameters={"scenarios": [
            {"population_demand_ratio": 0.1}, {"budget": 3}]})


def test_sensitivity_rejects_unknown_parameter():
    with pytest.raises(GuardInvalidParam):
        preflight("sensitivity", parameters={"parameter": "walking_speed", "values": [1]})


def test_stress_requires_a_disabled_shelter():
    with pytest.raises(GuardMissingParam):
        preflight("stress_test", parameters={})


def test_duplicate_option_ids_rejected():
    option = {"option_id": "A", "delta_capacity_persons": 1, "cost_units": 1,
              "hypothetical": True, "human_confirmed": False, "combination_policy": "standalone"}
    with pytest.raises(GuardInvalidOption):
        guards.validate_improvement_options([option, dict(option)])


def test_unconfirmed_non_hypothetical_option_rejected():
    option = {"option_id": "A", "delta_capacity_persons": 1, "cost_units": 1,
              "hypothetical": False, "human_confirmed": False, "combination_policy": "standalone"}
    with pytest.raises(GuardInvalidOption):
        guards.validate_improvement_options([option])


def test_explain_requires_a_known_result():
    with pytest.raises(GuardUnknownResult):
        preflight("explain_result", result_id="unknown-result")


def test_postflight_rejects_foreign_scenario():
    full = {"scenario_id": "other", "data_version": "zhongshan_pilot_v0.1",
            "verification_status": "PASS_DEMO", "solver_status": "OPTIMAL",
            "optimality_proven": True, "data_status": "DEMO_ONLY"}
    with pytest.raises(ResultMismatch):
        guards.postflight(full_result=full, state=state(), config=HarnessConfig())


def test_postflight_rejects_failed_verifier():
    full = {"scenario_id": "c-x-t01-00000000", "data_version": "zhongshan_pilot_v0.1",
            "verification_status": "FAIL", "solver_status": "OPTIMAL",
            "optimality_proven": True, "data_status": "DEMO_ONLY", "certificate": {"checks": []}}
    with pytest.raises(ResultMismatch):
        guards.postflight(full_result=full, state=state(), config=HarnessConfig())


def test_optimal_claim_requires_proof():
    full = {"scenario_id": "c-x-t01-00000000", "data_version": "zhongshan_pilot_v0.1",
            "verification_status": "PASS_DEMO", "solver_status": "FEASIBLE",
            "optimality_proven": False, "data_status": "DEMO_ONLY"}
    with pytest.raises(GuardClaimViolation):
        guards.postflight(full_result=full, state=state(), config=HarnessConfig(), claim="optimal")
    # Without the claim the same result is acceptable: FEASIBLE is publishable.
    checked = guards.postflight(full_result=full, state=state(), config=HarnessConfig())
    assert checked["solver_status"] == "FEASIBLE"


def test_numeric_claim_mismatch_is_rejected():
    summary = {"served_demand": 6456.1, "total_demand": 110205.7, "weighted_walk_time_min": 9.03}
    guards.assert_claims_consistent(summary, {"served_demand": 6456.1})
    with pytest.raises(GuardClaimViolation):
        guards.assert_claims_consistent(summary, {"served_demand": 9999.0})


def test_demo_only_cannot_be_described_as_verified():
    full = {"data_status": "DEMO_ONLY"}
    guards.assert_demo_disclosure(full, "该结果基于 DEMO_ONLY 数据，仅用于演示。")
    with pytest.raises(GuardClaimViolation):
        guards.assert_demo_disclosure(full, "该方案已经现场核实，可作为应急执行建议。")
