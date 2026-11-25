"""End-to-end harness tests against the real Module B service (in-process ASGI)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from agent.b_client import BClient
from agent.errors import HarnessError
from agent.harness.executor import ExecuteRequest, HarnessExecutor
from agent.harness.trace import read_trace
from tests.module_c.conftest import JobLostClient

HYPO_OPTION = {
    "option_id": "HYPOTHETICAL_CAP_ZS_1B4DF7920B",
    "shelter_id": "zs_1b4df7920b",
    "action": "hypothetical_capacity_increase",
    "delta_capacity_persons": 100,
    "cost_units": 1,
    "eligible": True,
    "source_status": "hypothetical_assumption",
    "hypothetical": True,
    "human_confirmed": False,
    "combination_policy": "standalone",
}


def test_assess_returns_demo_result_with_projected_summary(executor: HarnessExecutor):
    outcome = executor.execute(ExecuteRequest(task_type="assess"))
    assert outcome.ok, outcome.error
    assert outcome.data_status == "DEMO_ONLY"
    assert outcome.verification_status == "PASS_DEMO"
    assert outcome.solver_status == "OPTIMAL"
    assert outcome.summary_bytes <= 20 * 1024
    assert outcome.summary["metrics"]["served_demand"] > 0
    assert [call.tool_name for call in outcome.tool_calls] == ["submit_task", "get_job", "get_result"]


def test_optimize_with_zero_budget_selects_nothing(executor: HarnessExecutor):
    outcome = executor.execute(ExecuteRequest(task_type="optimize", changes={"budget_units": 0}))
    assert outcome.ok, outcome.error
    optimization = outcome.summary["detail"]["optimization"]
    assert optimization["selected_option_ids"] == []
    assert optimization["total_cost_units"] == 0


# The five most loaded shelters. Disabling them makes capacity - not
# reachability - the binding constraint, which is the only regime where an
# expansion option can change the served total on this demo dataset.
SCARCE_SHELTERS = ["zs_1b4df7920b", "zs_60213118ec", "zs_e016bde4b3",
                   "zs_737caba8e8", "zs_6358642b6f"]
SATURATED_OPTION = {
    "option_id": "CAP_ZS_FED820D94E_300",
    "shelter_id": "zs_fed820d94e",
    "action": "hypothetical_capacity_increase",
    "delta_capacity_persons": 300,
    "cost_units": 1,
    "eligible": True,
    "source_status": "hypothetical_assumption",
    "hypothetical": True,
    "human_confirmed": False,
    "combination_policy": "standalone",
}


def test_optimize_picks_an_option_when_capacity_binds(executor: HarnessExecutor):
    outcome = executor.execute(ExecuteRequest(
        task_type="optimize",
        changes={"budget_units": 1, "disabled_shelter_ids": SCARCE_SHELTERS},
        improvement_options=[SATURATED_OPTION]))
    assert outcome.ok, outcome.error
    optimization = outcome.summary["detail"]["optimization"]
    assert optimization["selected_option_ids"] == [SATURATED_OPTION["option_id"]]
    assert optimization["total_cost_units"] == 1
    assert optimization["served_demand"] > 1000  # 732 baseline + 300 capacity


def test_optimize_reports_no_gain_when_capacity_is_not_binding(executor: HarnessExecutor):
    """A valid business answer: buying capacity changes nothing here."""
    outcome = executor.execute(ExecuteRequest(
        task_type="optimize", changes={"budget_units": 1},
        improvement_options=[HYPO_OPTION]))
    assert outcome.ok, outcome.error
    assert outcome.summary["detail"]["optimization"]["selected_option_ids"] == []


def test_compare_returns_two_rows_and_no_forced_winner(executor: HarnessExecutor):
    outcome = executor.execute(ExecuteRequest(
        task_type="compare",
        parameters={"scenarios": [{"population_demand_ratio": 0.1},
                                  {"population_demand_ratio": 0.3}]}))
    assert outcome.ok, outcome.error
    comparison = outcome.summary["detail"]["comparison"]
    assert comparison["scenario_count"] == 2
    assert comparison["unique_recommendation"] is None
    served = [row["served_demand"] for row in comparison["scenarios"]]
    assert served[0] < served[1]


def test_stress_test_compares_fixed_and_adaptive(executor: HarnessExecutor):
    outcome = executor.execute(ExecuteRequest(
        task_type="stress_test",
        parameters={"disabled_shelter_ids": ["zs_1b4df7920b"], "budget_units": 1},
        improvement_options=[SATURATED_OPTION]))
    assert outcome.ok, outcome.error
    stress = outcome.summary["detail"]["stress"]
    assert stress["disabled_shelter_ids"] == ["zs_1b4df7920b"]
    assert stress["fixed_plan"]["served_demand"] < 6456.103   # closing a loaded site hurts
    assert stress["adaptive_replan"] is not None
    assert outcome.summary_bytes <= 20 * 1024


def test_multi_turn_inheritance_keeps_untouched_ratio(executor: HarnessExecutor):
    first = executor.execute(ExecuteRequest(
        task_type="assess", changes={"population_demand_ratio": 0.35}))
    assert first.ok, first.error
    second = executor.execute(ExecuteRequest(
        task_type="assess", changes={"max_walk_time_min": 20}))
    assert second.ok, second.error
    assert second.scenario.params.population_demand_ratio == 0.35
    assert second.scenario.params.max_walk_time_min == 20
    assert second.scenario.parent_scenario_id == first.scenario.scenario_id
    assert first.scenario.scenario_id != second.scenario.scenario_id


def test_explain_result_reuses_a_previous_result(executor: HarnessExecutor):
    first = executor.execute(ExecuteRequest(task_type="assess"))
    assert first.ok, first.error
    explained = executor.execute(ExecuteRequest(
        task_type="explain_result", result_id=first.result_id))
    assert explained.ok, explained.error
    assert explained.summary["result_id"] != first.result_id


def test_missing_budget_is_refused_without_calling_b(executor: HarnessExecutor):
    outcome = executor.execute(ExecuteRequest(task_type="optimize"))
    assert not outcome.ok
    assert outcome.error["code"] == "GUARD_MISSING_PARAM"
    assert outcome.tool_calls == []          # nothing was submitted
    assert outcome.job_id is None


def test_invalid_ratio_is_refused_without_calling_b(executor: HarnessExecutor):
    outcome = executor.execute(ExecuteRequest(task_type="assess",
                                              changes={"population_demand_ratio": 1.4}))
    assert not outcome.ok
    assert outcome.error["code"] == "GUARD_INVALID_PARAM"
    assert outcome.tool_calls == []


def test_verified_mode_is_refused_on_demo_source(executor: HarnessExecutor):
    outcome = executor.execute(ExecuteRequest(task_type="assess",
                                              changes={"analysis_mode": "verified"}))
    assert not outcome.ok
    assert outcome.error["code"] in {"GUARD_INVALID_PARAM", "GUARD_NOT_VERIFIED_SOURCE"}


def test_job_timeout_is_bounded_and_reported(config, b_client: BClient, session, trace):
    fast = replace(config, job_timeout_s=0.2, max_job_retries=0)
    executor = HarnessExecutor(b_client, session, trace, fast)
    outcome = executor.execute(ExecuteRequest(task_type="assess"))
    assert not outcome.ok
    assert outcome.error["code"] == "JOB_TIMEOUT"
    assert outcome.duration_ms < 5000


def test_job_state_lost_is_detected_not_polled_forever(config, b_app_client, session, trace):
    client = BClient(config, client=JobLostClient(b_app_client, fail_after=1))
    executor = HarnessExecutor(client, session, trace, config)
    outcome = executor.execute(ExecuteRequest(task_type="assess"))
    assert not outcome.ok
    assert "JOB_STATE_LOST" in outcome.error["message"] or outcome.error["code"] == "JOB_STATE_LOST"
    assert outcome.duration_ms < 15000


def test_same_idempotency_key_returns_same_job(b_client: BClient):
    payload = {"task_type": "assess", "analysis_mode": "demo", "task_id": "fixed-1",
               "idempotency_key": "fixed-key-1"}
    first = b_client.submit(payload)
    second = b_client.submit(payload)
    assert first["job_id"] == second["job_id"]


def test_summary_matches_full_result(executor: HarnessExecutor, b_client: BClient):
    outcome = executor.execute(ExecuteRequest(task_type="assess"))
    assert outcome.ok, outcome.error
    full = b_client.get_result(outcome.result_id)
    for key in ("served_demand", "total_demand", "weighted_walk_time_min"):
        assert outcome.summary["metrics"][key] == full["result_summary"][key]


def test_trace_is_written_and_queryable_by_task(executor: HarnessExecutor, trace, config):
    outcome = executor.execute(ExecuteRequest(task_type="assess"))
    rows = read_trace(config.trace_dir / f"{executor.session.session_id}.jsonl")
    task_rows = [row for row in rows if row.get("task_id") == outcome.task_id]
    assert {row["event"] for row in task_rows} == {"tool_call", "task"}
    assert all("args_digest" in row for row in task_rows if row["event"] == "tool_call")
    assert not any("password" in str(row).lower() for row in rows)
