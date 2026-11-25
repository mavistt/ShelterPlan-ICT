"""Offline agent-loop tests with a scripted LLM.

These pin down the LOOP semantics that the real-model experiment depends on:
session parameter merging, the numeric-citation gate, the DEMO_ONLY gate and
the missing-budget refusal. They are deterministic and involve no model.
"""
from __future__ import annotations

from dataclasses import replace

from agent.agent_loop import AgentRunner
from agent.agent_tools import (
    SHARED_SYSTEM_PROMPT, baseline_registry, harness_registry,
)
from agent.b_client import BClient
from agent.service import HarnessService
from tests.module_c.scripted_llm import ScriptedLLM, ScriptedResponse, ok_usage, tool_call


def make_harness(config, b_app_client, session_id="s-scripted"):
    service = HarnessService(config, client=BClient(config, client=b_app_client))
    registry, service = harness_registry(service, session_id)
    runner = AgentRunner("harness", registry, SHARED_SYSTEM_PROMPT,
                         ScriptedLLM([]), config=config, service=service, session_id=session_id)
    return runner, service


def make_baseline(config, b_app_client, session_id="b-scripted"):
    client = BClient(replace(config, max_transport_retries=0), client=b_app_client)
    registry, state = baseline_registry(client, config)
    runner = AgentRunner("baseline", registry, SHARED_SYSTEM_PROMPT,
                         ScriptedLLM([]), config=config)
    return runner, state


def assess_changes(ratio: float | None = None, walk: float | None = None) -> dict:
    changes = {}
    if ratio is not None:
        changes["population_demand_ratio"] = ratio
    if walk is not None:
        changes["max_walk_time_min"] = walk
    return changes


def test_session_merges_only_named_changes(config, b_app_client):
    runner, _ = make_harness(config, b_app_client)
    runner.llm.responses = [
        ScriptedResponse(tool_calls=[tool_call("submit_task", task_type="assess",
                                               changes=assess_changes(ratio=0.35))],
                         usage=ok_usage()),
        ScriptedResponse(tool_calls=[tool_call("submit_task", task_type="assess",
                                               changes=assess_changes(walk=20))],
                         usage=ok_usage()),
        ScriptedResponse(content="评估完成：比例保持0.35，阈值20分钟。", usage=ok_usage()),
    ]
    run = runner.run_case("T-merge", [{"user": "评估，需求比例0.35"}, {"user": "步行阈值改成20"}])
    assert run.final_text
    assert [row["ok"] for row in run.submitted] == [True, True]
    assert run.submitted[0]["params"]["population_demand_ratio"] == 0.35
    assert run.submitted[1]["params"]["population_demand_ratio"] == 0.35  # inherited
    assert run.submitted[1]["params"]["max_walk_time_min"] == 20


def test_baseline_arm_resets_unmentioned_ratio(config, b_app_client):
    """The same scripted behaviour through the raw arm loses the ratio.

    This is the deterministic mechanism check behind the V1 experiment: the
    baseline arm has no session snapshot, so B rebuilds the scenario from its
    config defaults.
    """
    runner, state = make_baseline(config, b_app_client)
    runner.llm.responses = [
        ScriptedResponse(tool_calls=[tool_call("submit_task", task_type="assess",
                                               changes=assess_changes(ratio=0.35))],
                         usage=ok_usage()),
        ScriptedResponse(tool_calls=[tool_call("get_result", result_id=" PLACEHOLDER")],
                         usage=ok_usage()),
        ScriptedResponse(content="完成。", usage=ok_usage()),
    ]
    turns = [{"user": "评估，需求比例0.35"}, {"user": "步行阈值改成20"}]
    # The scripted model cannot know result ids up front; intercept by patching
    # the second scripted response at runtime is complex - instead submit twice
    # and let the second job fail fast on B-side validation? Simplest honest
    # check: run the first turn only and inspect effective params.
    run = runner.run_case("T-reset", turns[:1])
    assert run.submitted and run.submitted[0]["job_id"]
    requests = state["requests"]
    assert requests[0]["effective_params"]["population_demand_ratio"] == 0.35
    # A stateless follow-up with only the threshold change resets the ratio:
    from agent.agent_tools import effective_baseline_params
    second = effective_baseline_params({"task_type": "assess", "changes": {"max_walk_time_min": 20}})
    assert second["population_demand_ratio"] == 0.2  # silently reset
    assert second["max_walk_time_min"] == 20


def test_numeric_gate_rewrites_wrong_number(config, b_app_client):
    runner, _ = make_harness(config, b_app_client)
    runner.llm.responses = [
        ScriptedResponse(tool_calls=[tool_call("submit_task", task_type="assess")], usage=ok_usage()),
        ScriptedResponse(content="评估完成，已服务需求为 99999.0 人。", usage=ok_usage()),
        ScriptedResponse(content="评估完成。本次评估基于 DEMO_ONLY 数据，未经真实现场核验；"
                                 "已服务需求约 6456 人，总需求约 110206 人。", usage=ok_usage()),
    ]
    run = runner.run_case("T-numeric", [{"user": "评估现状"}])
    assert run.rewrite_used is True
    assert run.citations is not None
    assert run.citations["consistent"] is True
    assert "99999" not in (run.final_text or "")


def test_demo_gate_forces_disclosure(config, b_app_client):
    runner, _ = make_harness(config, b_app_client)
    runner.llm.responses = [
        ScriptedResponse(tool_calls=[tool_call("submit_task", task_type="assess")], usage=ok_usage()),
        ScriptedResponse(content="本方案已核验，可作为可执行建议发布。", usage=ok_usage()),
        ScriptedResponse(content="本结果基于 DEMO_ONLY 数据，未经真实现场核验，不得作为可执行应急建议；"
                                 "仅供参考。", usage=ok_usage()),
    ]
    run = runner.run_case("T-demo", [{"user": "评估现状"}])
    assert run.rewrite_used is True
    assert run.demo_violation is False
    assert "DEMO_ONLY" in (run.final_text or "")


def test_missing_budget_is_refused_before_any_remote_call(config, b_app_client):
    runner, _ = make_harness(config, b_app_client)
    runner.llm.responses = [
        ScriptedResponse(tool_calls=[tool_call("submit_task", task_type="optimize")], usage=ok_usage()),
        ScriptedResponse(content="好的。", usage=ok_usage()),
    ]
    run = runner.run_case("T-budget", [{"user": "帮我优化"}])
    # The tool call itself succeeds; the planning task inside it is refused.
    task = run.submitted[0]
    assert task["ok"] is False
    assert task["error_code"] == "GUARD_MISSING_PARAM"
    assert run.tool_calls[0].payload["result"]["error"]["code"] == "GUARD_MISSING_PARAM"


def test_loop_limit_is_reported(config, b_app_client):
    runner, _ = make_harness(config, b_app_client)
    runner.max_llm_calls = 3
    runner.llm.responses = [
        ScriptedResponse(tool_calls=[tool_call("submit_task", task_type="assess")], usage=ok_usage()),
        ScriptedResponse(tool_calls=[tool_call("get_capabilities")], usage=ok_usage()),
        ScriptedResponse(tool_calls=[tool_call("get_capabilities")], usage=ok_usage()),
    ]
    run = runner.run_case("T-loop", [{"user": "评估"}])
    assert run.max_turns_hit is True
    assert run.final_text is None
