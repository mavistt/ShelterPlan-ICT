from __future__ import annotations

from dataclasses import replace

import pytest

from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.errors import ResultMismatch
from agent.harness.executor import ExecuteRequest, HarnessExecutor
from agent.harness.session import Session
from agent.harness.trace import TraceRecorder, read_trace
from agent.intent.extractor import _deterministic_repairs, rule_extract
from agent.intent.normalizer import apply_ops
from agent.intent.schema import ParsedIntent
from agent.workflow.dialogue import DialogueState, fill_pending, resume_intent
from agent.workflow.router import TurnMemory, route


def composite_text() -> str:
    return (
        "\u5173\u95ed zs_60213118ec \u548c zs_e016bde4b3 \u540e\u5148\u5206\u6790\u5f71\u54cd\uff0c"
        "\u518d\u5728\u9884\u7b97 20 \u5185\u7ed9 zs_737caba8e8 \u6269\u5bb9 100 \u4eba\uff0c"
        "\u6210\u672c 3\uff0c\u505a\u707e\u540e\u6062\u590d\u89c4\u5212"
    )


def toy_option() -> dict:
    return {
        "option_id": "A_PLUS_50", "shelter_id": "A",
        "action": "hypothetical_capacity_increase", "delta_capacity_persons": 50,
        "cost_units": 10, "eligible": True, "source_status": "hypothetical",
        "hypothetical": True, "human_confirmed": False,
        "combination_policy": "standalone",
    }


def test_chinese_composite_intent_binds_damage_budget_and_candidate_separately(config):
    intent = rule_extract(composite_text())
    assert intent.name == "disaster_recovery"
    assert intent.ops[0].field == "budget_units"
    assert intent.ops[0].value == 20
    assert intent.ops[-1].field == "disabled_shelter_ids"
    assert intent.ops[-1].value == ["zs_60213118ec", "zs_e016bde4b3"]
    assert len(intent.options) == 1
    assert intent.options[0]["shelter_id"] == "zs_737caba8e8"
    assert intent.options[0]["cost_units"] == 3

    params = ScenarioParams(0.2, 15, None, (), "zhongshan_pilot_v0.1", "demo")
    normalized = apply_ops(params, intent.ops, text=composite_text())
    plan = route(intent, params=normalized.params, changes=normalized.changes,
                 memory=TurnMemory(), config=config, user_goal=composite_text())
    assert plan.action == "task"
    assert plan.task_type == "disaster_recovery"
    assert plan.changes["budget_units"] == 20
    assert plan.changes["disabled_shelter_ids"] == ["zs_60213118ec", "zs_e016bde4b3"]
    assert plan.options[0]["shelter_id"] not in plan.changes["disabled_shelter_ids"]
    assert plan.user_goal == composite_text()


def test_composite_missing_candidate_cost_is_clarified_and_resumes_original_task():
    original = (
        "\u5173\u95ed zs_60213118ec \u540e\u5206\u6790\u5f71\u54cd\uff0c\u9884\u7b97 20 \u5185"
        "\u7ed9 zs_737caba8e8 \u6269\u5bb9 100 \u4eba\uff0c\u505a\u6062\u590d\u89c4\u5212"
    )
    intent = rule_extract(original)
    params = ScenarioParams(0.2, 15, None, (), "zhongshan_pilot_v0.1", "demo")
    normalized = apply_ops(params, intent.ops, text=original)
    config = HarnessConfig()
    plan = route(intent, params=normalized.params, changes=normalized.changes,
                 memory=TurnMemory(), config=config, user_goal=original)
    assert plan.action == "clarify"
    assert plan.task_type == "disaster_recovery"
    assert "improvement_options" in plan.clarify_fields

    dialogue = DialogueState()
    pending = dialogue.open(plan, params=normalized.params, text=original, now=1.0)
    reply = "\u5019\u9009\uff1a\u7ed9 zs_737caba8e8 \u6269\u5bb9 100 \u4eba\uff0c\u6210\u672c 3"
    filled = fill_pending(pending, reply)
    resumed = resume_intent(pending, filled.values)
    assert filled.complete
    assert resumed.name == "disaster_recovery"
    assert resumed.options[0]["cost_units"] == 3
    assert resumed.raw["user_goal"] == original


def test_composite_multiround_clarification_carries_slots_and_original_goal(config, b_client):
    from agent.service import HarnessService
    from agent.structured_runner import StructuredRunner
    from tests.module_c.rule_llm import RuleLLM

    service = HarnessService(config, client=b_client, trace_dir=str(config.trace_dir))
    runner = StructuredRunner("harness_opt", service, RuleLLM(), service.config,
                              session_id="composite-multiround", dialogue_enabled=True)
    original = (
        "\u5173\u95ed zs_fed820d94e \u540e\u5206\u6790\u53d7\u635f\u5f71\u54cd\uff0c"
        "\u518d\u505a\u707e\u540e\u6062\u590d\u89c4\u5212"
    )
    first = runner.run_turn(original)
    assert first.status == "NEEDS_CLARIFICATION"
    assert set(first.clarification_fields) == {"budget_units", "improvement_options"}

    second = runner.run_turn("\u9884\u7b97 2")
    assert second.status == "NEEDS_CLARIFICATION"
    pending = runner.dialogue.pending
    assert pending is not None
    assert pending.changes["budget_units"] == 2
    assert pending.changes["disabled_shelter_ids"] == ["zs_fed820d94e"]
    assert pending.original_text == original

    third_reply = (
        "\u7ed9 zs_60213118ec \u6269\u5bb9 100 \u4eba\uff0c"
        "\u6210\u672c 1"
    )
    filled = fill_pending(pending, third_reply)
    resumed = resume_intent(pending, filled.values)
    assert filled.complete
    assert resumed.raw["user_goal"] == original
    resumed_ops = {op.field: op.value for op in resumed.ops}
    assert resumed_ops["budget_units"] == 2
    assert resumed_ops["disabled_shelter_ids"] == ["zs_fed820d94e"]
    assert resumed.options[0]["shelter_id"] == "zs_60213118ec"
    service.close()


def test_public_composite_candidate_limit_matches_task_contract():
    from backend.graph.registry import capabilities

    capability = capabilities()
    recovery = capability["composite_workflows"][0]
    assert recovery["max_recovery_options"] == 12
    assert capability["solver"]["max_options"] >= recovery["max_recovery_options"]


def test_damage_plus_impact_comparison_keeps_shutdown_intent():
    text = (
        "\u5173\u95ed zs_60213118ec \u548c zs_e016bde4b3\uff0c"
        "\u6bd4\u8f83\u53d7\u635f\u524d\u540e\u7684\u5f71\u54cd"
    )
    intent = _deterministic_repairs(text, ParsedIntent("compare"))
    assert intent.name == "stress_test"
    assert any(op.field == "disabled_shelter_ids"
               and op.value == ["zs_60213118ec", "zs_e016bde4b3"] for op in intent.ops)


def test_compare_explicit_rows_retain_common_effective_parameters(config):
    params = ScenarioParams(0.35, 15, 6, ("zs_60213118ec",),
                            "zhongshan_pilot_v0.1", "demo", "shiqi")
    intent = ParsedIntent("compare", compare={
        "field": "max_walk_time_min", "values": [10, 20]})
    plan = route(intent, params=params, changes={}, memory=TurnMemory(), config=config)
    rows = plan.parameters["scenarios"]
    assert len(rows) == 2
    assert [row["max_walk_time_min"] for row in rows] == [10, 20]
    for row in rows:
        assert row["population_demand_ratio"] == 0.35
        assert row["budget_units"] == 6
        assert row["disabled_shelter_ids"] == ["zs_60213118ec"]


def test_real_harness_to_b_api_solver_verifier_and_cross_stage_guard(
    config, b_app_client, tmp_path, monkeypatch,
):
    toy_config = replace(
        config, data_version="synthetic_toy_v1", default_population_demand_ratio=1.0,
        default_max_walk_time_min=15.0, expected_data_status="SYNTHETIC_FIXTURE",
        default_region_id=None,
    )
    session = Session("composite-e2e", ScenarioParams(
        1.0, 15.0, None, (), "synthetic_toy_v1", "demo"),
        valid_shelter_ids=frozenset({"A", "B"}))
    trace = TraceRecorder(toy_config.trace_dir / "composite-e2e.jsonl", session.session_id)
    client = BClient(toy_config, client=b_app_client)
    executor = HarnessExecutor(client, session, trace, toy_config)
    build_payload = executor._build_payload

    def include_allowlisted_test_fixture(*args, **kwargs):
        payload = build_payload(*args, **kwargs)
        payload["fixture_id"] = "toy_case"
        return payload

    monkeypatch.setattr(executor, "_build_payload", include_allowlisted_test_fixture)
    before_state = session.current
    outcome = executor.execute(ExecuteRequest(
        task_type="disaster_recovery",
        changes={"disabled_shelter_ids": ["B"], "budget_units": 10},
        improvement_options=[toy_option()],
        user_goal="Disable B and restore service within budget 10.",
    ))

    assert outcome.ok, outcome.error
    assert outcome.data_status == "SYNTHETIC_FIXTURE"
    assert outcome.solver_status == "OPTIMAL"
    assert outcome.verification_status == "PASS_DEMO"
    assert outcome.scenario.params.disabled_shelter_ids == ("B",)
    assert outcome.scenario.params.budget_units == 10
    assert outcome.summary["disclosure"]["synthetic_fixture"] is True
    assert "not Module A data" in outcome.summary["disclosure"]["must_state"]
    assert [call.tool_name for call in outcome.tool_calls] == ["submit_task", "get_job", "get_result"]

    full_result = client.get_result(outcome.result_id)
    rows = full_result["result_detail"]["composite"]["contract"]["stage_results"]
    assert [row["stage_id"] for row in rows] == [
        "baseline", "damage", "diagnose", "recovery", "compare", "verify"]
    assert full_result["trace"]
    assert {row["stage_id"] for row in full_result["trace"] if row["skill_id"] == "S05"} == {
        "baseline", "damage"}
    trace_rows = read_trace(toy_config.trace_dir / "composite-e2e.jsonl")
    task_rows = [row for row in trace_rows if row.get("task_id") == outcome.task_id]
    assert any(row.get("event") == "task" and row.get("composite_stages") for row in task_rows)

    corrupted = {**full_result}
    corrupted["result_detail"] = {**full_result["result_detail"]}
    corrupted["result_detail"]["composite"] = {
        **full_result["result_detail"]["composite"], "contract": {
            **full_result["result_detail"]["composite"]["contract"], "stage_results": [
                {**row, "effective_parameters": {
                    **row.get("effective_parameters", {}), "population_demand_ratio": 0.5}}
                if row["stage_id"] == "baseline" else row for row in rows
            ],
        },
    }
    from agent.harness import guards
    with pytest.raises(ResultMismatch):
        guards.postflight_disaster_recovery(
            full_result=corrupted, state=outcome.scenario, config=toy_config,
            session=session, expected_baseline_disabled=before_state.params.disabled_shelter_ids,
        )


def test_real_c_http_session_executes_composite_against_allowlisted_gis(tmp_path):
    import time

    from starlette.testclient import TestClient

    from agent.api import ConversationService, create_app
    from agent.service import HarnessService
    from backend.app import app as b_app
    from tests.module_c.rule_llm import RuleLLM

    config = replace(
        HarnessConfig.from_env(),
        data_version="shelterplan_module_a_v2.0-demo__east_district",
        default_region_id="east_district", expected_data_status="DEMO_ONLY",
        trace_dir=tmp_path / "c-api-trace",
        send_region_id_to_b=True, poll_initial_s=0.02, poll_max_s=0.1,
    )
    b_client = BClient(config, client=TestClient(b_app))
    service = HarnessService(config, client=b_client, trace_dir=str(config.trace_dir))
    conversation = ConversationService(
        config, service=service, llm=RuleLLM(), store_dir=str(tmp_path / "state"),
        arm="harness_opt", dialogue_enabled=True,
    )
    c_client = TestClient(create_app(conversation=conversation))
    try:
        created = c_client.post("/v1/sessions", json={})
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]
        text = (
            "\u5173\u95ed zs_1b4df7920b\u3001zs_60213118ec\u3001zs_e016bde4b3\u3001"
            "zs_737caba8e8\u3001zs_6358642b6f \u540e\u5148\u5206\u6790\u53d7\u635f\u5f71\u54cd\uff0c"
            "\u518d\u5728\u9884\u7b97 1 \u5185\u4e3a zs_fed820d94e \u6269\u5bb9 300 \u4eba\uff0c"
            "\u6210\u672c 1\uff0c\u505a\u707e\u540e\u6062\u590d\u89c4\u5212"
        )
        submitted = c_client.post(f"/v1/sessions/{session_id}/turns", json={"text": text})
        assert submitted.status_code == 202, submitted.text
        turn_id = submitted.json()["turn_id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            envelope = c_client.get(f"/v1/turns/{turn_id}").json()
            if envelope["status"] != "RUNNING":
                break
            time.sleep(0.05)
        assert envelope["status"] == "SUCCEEDED", envelope.get("error") or envelope
        assert envelope["task_id"]
        assert envelope["result_id"]
        assert envelope["data_version"] == config.data_version
        assert envelope["data_status"] == "DEMO_ONLY"
        assert any("DEMO_ONLY" in warning for warning in envelope["warnings"])
        assert isinstance(envelope.get("composite"), dict)
        assert [row["stage_id"] for row in envelope["composite"]["stages"]] == [
            "baseline", "damage", "diagnose", "recovery", "compare", "verify"
        ]

        layers_resp = c_client.get(
            f"/v1/results/{envelope['result_id']}/layers",
            params={"session_id": session_id, "variant": "auto"},
        )
        assert layers_resp.status_code == 200, layers_resp.text
        layers_by_id = {row["layer_id"]: row for row in layers_resp.json()["layers"]}
        assert layers_by_id["shelter_utilization"]["rows"]
        assert layers_by_id["shelter_utilization_baseline"]["rows"]
        assert layers_by_id["shelter_utilization_damaged"]["rows"]
        assert layers_by_id["shelter_utilization_recovered"]["rows"]

        full = conversation.service.get_full_result(envelope["result_id"])
        contract = full["result_detail"]["composite"]["contract"]
        stages = {row["stage_id"]: row for row in contract["stage_results"]}
        assert list(stages) == ["baseline", "damage", "diagnose", "recovery", "compare", "verify"]
        assert full["solver_status"] in {"OPTIMAL", "FEASIBLE"}
        assert full["verification_status"] == "PASS_DEMO"
        assert stages["diagnose"]["skill_ids"] == ["S05"]
        assert "S07" in stages["recovery"]["skill_ids"]
        assert all(row["verification_status"] == "PASS_DEMO"
                   for row in (stages["baseline"], stages["damage"], stages["recovery"]))
        assert stages["recovery"]["effective_parameters"]["budget_units"] == 1
        assert stages["recovery"]["effective_parameters"]["disabled_shelter_ids"] == [
            "zs_1b4df7920b", "zs_60213118ec", "zs_e016bde4b3", "zs_737caba8e8", "zs_6358642b6f"]
        assert full["result_detail"]["composite"]["comparison"]["selected_option_ids"]
        assert contract["execution_trace_id"] == full["execution_trace_id"]
        view = c_client.get(f"/v1/sessions/{session_id}").json()
        assert view["params"]["budget_units"] == 1
        assert view["params"]["disabled_shelter_ids"] == stages["recovery"]["effective_parameters"]["disabled_shelter_ids"]
    finally:
        c_client.close()
        conversation.close()
