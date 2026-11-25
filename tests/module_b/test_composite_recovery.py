from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from backend.errors import CandidateError, ScenarioError
from backend.graph.composite_recovery import execute_disaster_recovery
from backend.models import DisasterRecoveryRequest, ImprovementOption


OPTION = ImprovementOption(
    option_id="A_PLUS_50", shelter_id="A", action="hypothetical_capacity_increase",
    delta_capacity_persons=50, cost_units=10, eligible=True,
    source_status="hypothetical", hypothetical=True, human_confirmed=False,
    combination_policy="standalone",
)


def request(**overrides) -> DisasterRecoveryRequest:
    payload = {
        "task_id": f"b-composite-test-{uuid.uuid4().hex}",
        "client_session_id": "c-test-session",
        "client_turn_id": "b-composite-turn-{uuid.uuid4().hex}",
        "user_goal": "Disable shelter B, diagnose service loss, and recover within budget 10.",
        "fixture_id": "toy_case",
        "data_version": "synthetic_toy_v1",
        "damaged_shelter_ids": ["B"],
        "budget_units": 10,
        "improvement_options": [OPTION],
    }
    payload.update(overrides)
    return DisasterRecoveryRequest.model_validate(payload)


def test_composite_runs_real_registered_skills_lp_mip_and_verifiers():
    result = execute_disaster_recovery(request(), execution_trace_id="trace-composite-test")
    contract = result["result_detail"]["composite"]["contract"]
    stages = {row["stage_id"]: row for row in contract["stage_results"]}
    comparison = result["result_detail"]["composite"]["comparison"]

    assert result["status"] == "COMPLETED"
    assert result["data_status"] == "SYNTHETIC_FIXTURE"
    assert result["verification_status"] == "PASS_DEMO"
    assert result["solver_status"] == "OPTIMAL"
    assert result["optimality_proven"] is True
    assert list(stages) == ["baseline", "damage", "diagnose", "recovery", "compare", "verify"]
    assert stages["baseline"]["metrics"]["served_demand"] == pytest.approx(150)
    assert stages["damage"]["metrics"]["served_demand"] == pytest.approx(100)
    assert stages["diagnose"]["skill_ids"] == ["S05"]
    assert "S07" in stages["recovery"]["skill_ids"]
    assert stages["recovery"]["solver_status"] == "OPTIMAL"
    assert stages["recovery"]["verification_status"] == "PASS_DEMO"
    assert comparison["recovered"]["optimized_served_demand"] == pytest.approx(150)
    assert comparison["selected_option_ids"] == ["A_PLUS_50"]
    assert comparison["recovery_ratio"]["value"] == pytest.approx(1.0)
    assert len(contract["constraint_checks"]) >= 15
    assert all(row["passed"] for row in contract["constraint_checks"])
    assert stages["damage"]["input_result_ids"] == [stages["baseline"]["result_id"]]
    assert stages["recovery"]["input_result_ids"] == [
        stages["damage"]["result_id"], stages["diagnose"]["result_id"]]
    assert result["result_detail"]["composite"]["historical_or_demo_disclosure"].startswith(
        "SYNTHETIC_FIXTURE:")
    assert {row["stage_id"] for row in result["trace"] if row["skill_id"] == "S05"} == {
        "baseline", "damage"}
    assert stages["diagnose"]["result_id"] == stages["damage"]["result_id"]


def test_missing_budget_or_options_stops_after_diagnosis_without_mip():
    result = execute_disaster_recovery(request(budget_units=None, improvement_options=[]))
    composite = result["result_detail"]["composite"]
    stages = {row["stage_id"]: row for row in composite["contract"]["stage_results"]}

    assert result["status"] == "NEEDS_INPUT"
    assert stages["diagnose"]["status"] == "COMPLETED"
    assert stages["recovery"]["status"] == "SKIPPED"
    assert stages["recovery"]["reason_code"] == "BUDGET_REQUIRED"
    assert not any(row["skill_id"] in {"S06", "S07", "S08"} for row in result["trace"])
    assert result["solver_status"] == "NOT_RUN"


def test_option_for_disabled_facility_fails_closed_with_partial_trace():
    disabled_option = OPTION.model_copy(update={"option_id": "B_PLUS_50", "shelter_id": "B"})
    with pytest.raises(CandidateError) as caught:
        execute_disaster_recovery(request(improvement_options=[disabled_option]))

    contract = caught.value.details["composite_task"]
    stages = {row["stage_id"]: row for row in contract["stage_results"]}
    assert contract["status"] == "FAILED"
    assert stages["damage"]["status"] == "COMPLETED"
    assert stages["diagnose"]["status"] == "COMPLETED"
    assert stages["recovery"]["status"] == "FAILED"
    assert stages["recovery"]["reason_code"] == "INVALID_RECOVERY_OPTIONS"
    assert [row["decision"] for row in contract["branch_decisions"]].count("STOP") == 1


def test_stage_verifier_failure_stops_before_damage_stage():
    from backend.graph import composite_recovery

    real_execute = composite_recovery.execute_task

    def fail_baseline_verifier(task, **kwargs):
        document = real_execute(task, **kwargs)
        if task.task_type == "assess":
            document["verification_status"] = "FAIL"
        return document

    with patch.object(composite_recovery, "execute_task", side_effect=fail_baseline_verifier):
        with pytest.raises(ScenarioError) as caught:
            execute_disaster_recovery(request())

    contract = caught.value.details["composite_task"]
    stages = {row["stage_id"]: row for row in contract["stage_results"]}
    assert stages["baseline"]["status"] == "FAILED"
    assert stages["baseline"]["reason_code"] == "STAGE_VERIFICATION_FAILED"
    assert "damage" not in stages
    assert [row["stage_id"] for row in contract["branch_decisions"] if row["decision"] == "STOP"] == ["baseline"]


def test_source_version_mismatch_is_rejected_before_any_stage():
    with pytest.raises(ScenarioError) as caught:
        execute_disaster_recovery(request(data_version="wrong-version"))
    assert caught.value.details["error_code"] == "DATA_VERSION_MISMATCH"
