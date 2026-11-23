"""V2.5 scoped improvements: business fixes, evidence-only maths, and engineering hardening."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import time
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from agent.api import ConversationService, create_app
from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.errors import GuardUnknownResult
from agent.harness import contract
from agent.harness.results import (
    evaluate_compare_monotonicity,
    evaluate_pair_monotonicity,
    evaluate_series_monotonicity,
    json_bytes,
    project,
    resolve_primary_stage_optimality,
)
from agent.harness.trace import read_trace
from agent.intent.extractor import _deterministic_repairs, rule_extract
from agent.intent.normalizer import expand_options
from agent.intent.schema import ParsedIntent
from agent.numeric_audit import check_citations
from agent.service import HarnessService
from agent.state_store import SessionStore
from agent.workflow.answer import compose_answer
from agent.workflow.controller import ControlResult, bind_outcome
from agent.workflow.router import RoutePlan, TurnMemory, route
from backend.app import app as b_app
from backend.engine import optimize_budgeted, solve_assignment
from backend.evaluation.baselines import run_baseline
from backend.graph.executor import load_task_source
from tests.module_c.rule_llm import RuleLLM

PARAMS = ScenarioParams(0.2, 15.0, None, (), "zhongshan_pilot_v0.1", "demo")


def _pilot_option(
    option_id: str,
    shelter_id: str,
    delta: float,
    cost: float,
    *,
    policy: str = "standalone",
    group: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "option_id": option_id,
        "shelter_id": shelter_id,
        "action": "hypothetical_capacity_increase",
        "delta_capacity_persons": delta,
        "cost_units": cost,
        "eligible": True,
        "source_status": "hypothetical_assumption",
        "hypothetical": True,
        "human_confirmed": False,
        "combination_policy": policy,
    }
    if policy == "exclusive_group":
        row["exclusive_group"] = group
    return row


def _poll_turn(client: TestClient, turn_id: str, *, timeout_s: float = 45.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/v1/turns/{turn_id}").json()
        if body["status"] != "RUNNING":
            return body
        time.sleep(0.05)
    raise AssertionError(f"turn {turn_id} did not finish within {timeout_s}s")


def _run_turn(client: TestClient, session_id: str, text: str, *, client_turn_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"text": text}
    if client_turn_id is not None:
        payload["client_turn_id"] = client_turn_id
    resp = client.post(f"/v1/sessions/{session_id}/turns", json=payload)
    assert resp.status_code == 202, resp.text
    return _poll_turn(client, resp.json()["turn_id"])


def test_diagnose_intervene_with_options_projects_both_diagnosis_and_optimization(
    config: HarnessConfig, b_app_client: TestClient, tmp_path: Path
):
    service = HarnessService(
        config, client=BClient(config, client=b_app_client), trace_dir=str(tmp_path / "trace")
    )
    options = [_pilot_option("CAP_1", "zs_6358642b6f", 100.0, 1.0)]
    outcome = service.submit(
        "diagnose_intervene",
        session_id="s-diag-opt",
        changes={"budget_units": 5.0},
        improvement_options=options,
    )
    assert outcome["ok"] is True, outcome
    summary = outcome["summary"]
    assert summary["task_type"] == "diagnose_intervene"
    detail = summary["detail"]
    assert "diagnosis" in detail and "optimization" in detail
    assert detail["diagnosis"]["unserved_node_count"] > 0
    assert detail["optimization"]["selected_option_ids"] == ["CAP_1"]
    assert detail["optimization"]["served_demand_delta"] == pytest.approx(100.0)

    # Re-fetching through service.result must preserve task_type and full params.
    fetched = service.result(outcome["result_id"], session_id="s-diag-opt")
    assert fetched["task_type"] == "diagnose_intervene"
    assert fetched["scenario_params"]["budget_units"] == 5.0
    assert "diagnosis" in fetched["detail"] and "optimization" in fetched["detail"]

    control = ControlResult(
        status="ok",
        plan=RoutePlan(action="task", task_type="diagnose_intervene"),
        outcome=outcome,
        summary=summary,
        bindings=bind_outcome(outcome),
    )
    text = compose_answer(control, params=ScenarioParams(0.2, 15.0, 5.0, (), config.data_version, "demo"))
    assert "【未服务节点】" in text
    assert "【干预优化】" in text
    assert "【覆盖变化】" in text
    assert "HYPOTHETICAL" in text
    audit = check_citations(text, {"summaries": [summary], "user_given": [5.0, 100.0, 1.0]})
    assert audit["consistent"] is True, audit


def test_router_diagnose_intervene_reuses_memory_options_when_budget_set(config: HarnessConfig):
    memory = TurnMemory(
        last_task_type="optimize",
        last_options=[_pilot_option("CAP_MEM", "zs_6358642b6f", 50.0, 1.0)],
    )
    with_budget = ScenarioParams(0.2, 15.0, 3.0, (), config.data_version, "demo")
    plan_with = route(ParsedIntent("diagnose"), params=with_budget, changes={}, memory=memory, config=config)
    assert plan_with.action == "task"
    assert plan_with.task_type == "diagnose_intervene"
    assert len(plan_with.options) == 1
    assert "options_reused_from_memory" in plan_with.reason_codes

    no_budget = ScenarioParams(0.2, 15.0, None, (), config.data_version, "demo")
    plan_without = route(ParsedIntent("diagnose"), params=no_budget, changes={}, memory=memory, config=config)
    assert plan_without.action == "task"
    assert plan_without.options == []


def test_combinatorial_option_extraction_policies_and_costs():
    knapsack_text = (
        "预算设为 20，项目A给 zs_6358642b6f 扩容 60 人成本 11，"
        "项目B给 zs_e016bde4b3 扩容 50 人成本 10，"
        "项目C给 zs_737caba8e8 扩容 50 人成本 10，做扩容优化"
    )
    extracted = rule_extract(knapsack_text)
    assert extracted.name == "optimize"
    assert [o.get("option_id") for o in extracted.options] == ["OPT_A", "OPT_B", "OPT_C"]
    assert [o["cost_units"] for o in extracted.options] == [11.0, 10.0, 10.0]

    stackable_text = (
        "预算设为 2，给 zs_6358642b6f 扩容 50 人成本 1 可叠加，"
        "给 zs_6358642b6f 扩容 60 人成本 1 可叠加，做扩容优化"
    )
    stack_rows = expand_options(rule_extract(stackable_text).options)
    assert len(stack_rows) == 2
    assert all(row["combination_policy"] == "stackable" for row in stack_rows)

    excl_text = (
        "预算设为 2，给 zs_6358642b6f 扩容 50 人成本 1 互斥组 G1，"
        "给 zs_e016bde4b3 扩容 60 人成本 1 互斥组 G1，做扩容优化"
    )
    excl_rows = expand_options(rule_extract(excl_text).options)
    assert len(excl_rows) == 2
    assert all(row["combination_policy"] == "exclusive_group" and row["exclusive_group"] == "G1" for row in excl_rows)

    # LLM omitting metadata still gets enriched from explicit text markers without fabricating numbers.
    llm_intent = ParsedIntent(
        "optimize",
        options=[
            {"shelter_id": "zs_6358642b6f", "delta_capacity_persons": 50, "cost_units": 1},
            {"shelter_id": "zs_e016bde4b3", "delta_capacity_persons": 60, "cost_units": 1},
        ],
    )
    repaired = _deterministic_repairs(excl_text, llm_intent)
    assert repaired.options[0]["combination_policy"] == "exclusive_group"
    assert repaired.options[0]["exclusive_group"] == "G1"


def test_primary_stage_optimal_tri_state_requires_stage_evidence_and_objective_match(
    config: HarnessConfig, b_app_client: TestClient, tmp_path: Path
):
    # Case A: unmodified B result omits stage1 objective_value -> strictly UNKNOWN.
    service = HarnessService(
        config, client=BClient(config, client=b_app_client), trace_dir=str(tmp_path / "trace")
    )
    outcome = service.submit(
        "optimize",
        session_id="s-stage",
        changes={"budget_units": 1.0},
        improvement_options=[_pilot_option("OPT_A", "zs_6358642b6f", 100.0, 1.0)],
    )
    assert outcome["ok"] is True
    opt = outcome["summary"]["detail"]["optimization"]
    assert opt["primary_stage_optimal"] == "UNKNOWN"
    assert opt["primary_stage_evidence"]["reason"] == "stage1_objective_value_missing_in_b_evidence"

    # Case B: native stage1 evidence matches final served demand -> TRUE even if overall FEASIBLE.
    feasible_with_stage1 = {
        "result_id": "r-s1",
        "task_id": "t-s1",
        "scenario_id": "sc-1",
        "data_version": config.data_version,
        "data_status": "DEMO_ONLY",
        "analysis_mode": "demo",
        "solver_status": "FEASIBLE",
        "verification_status": "PASS_DEMO",
        "result_summary": {"served_demand": 6456.103027, "total_demand": 110205.74, "weighted_walk_time_min": 9.0},
        "certificate": {"checks": []},
        "result_detail": {
            "optimization": {
                "optimality_proven": False,
                "selected_option_ids": ["OPT_B", "OPT_C"],
                "selected_options": [_pilot_option("OPT_B", "zs_e016bde4b3", 50.0, 10.0)],
                "total_cost_units": 20.0,
                "budget_units": 20.0,
                "objective_value": 6556.103027,
                "assignment": {
                    "served_demand": 6556.103027,
                    "total_demand": 110205.74,
                    "weighted_walk_time_min": 9.0,
                    "optimality_proven": False,
                },
                "mip_stages": [
                    {"stage": "maximize_served_demand", "status": 0, "optimality_proven": True, "objective_value": 6556.103027},
                    {"stage": "minimize_weighted_walk_time", "status": 4, "optimality_proven": False},
                ],
            }
        },
    }
    ev_true = resolve_primary_stage_optimality(feasible_with_stage1)
    assert ev_true["status"] == "TRUE"
    proj_true = project(feasible_with_stage1, task_type="optimize", config=config)
    ctrl_true = ControlResult(
        status="ok",
        plan=RoutePlan(action="task", task_type="optimize"),
        summary=proj_true,
        bindings=bind_outcome({"summary": proj_true, "task_type": "optimize"}),
    )
    ans = compose_answer(ctrl_true, params=PARAMS)
    assert "FEASIBLE" in ans
    assert "主目标阶段最优核验=TRUE" in ans
    assert "且最优性已证明" not in ans

    # Case C: stage1 objective diverges or stage1 failed -> FALSE.
    divergent = {
        "result_detail": {
            "optimization": {
                "assignment": {"served_demand": 6556.103027},
                "mip_stages": [
                    {"stage": "maximize_served_demand", "status": 0, "optimality_proven": True, "objective_value": 6600.0},
                ],
            }
        }
    }
    assert resolve_primary_stage_optimality(divergent)["status"] == "FALSE"
    stage1_failed = {
        "result_detail": {
            "optimization": {
                "assignment": {"served_demand": 6556.103027},
                "mip_stages": [
                    {"stage": "maximize_served_demand", "status": 1, "optimality_proven": False},
                ],
            }
        }
    }
    assert resolve_primary_stage_optimality(stage1_failed)["status"] == "FALSE"


def test_greedy_comparison_is_unavailable_without_b_evidence_and_never_self_computed(
    config: HarnessConfig, b_app_client: TestClient, tmp_path: Path
):
    service = HarnessService(
        config, client=BClient(config, client=b_app_client), trace_dir=str(tmp_path / "trace")
    )
    outcome = service.submit(
        "optimize",
        session_id="s-nogreedy",
        changes={"budget_units": 1.0},
        improvement_options=[_pilot_option("OPT_A", "zs_6358642b6f", 100.0, 1.0)],
    )
    assert outcome["ok"] is True
    greedy = outcome["summary"]["detail"]["optimization"]["greedy_comparison"]
    assert greedy["status"] == "unavailable"

    with_b_greedy = {
        "result_id": "r-g",
        "task_id": "t-g",
        "scenario_id": "sc-g",
        "data_version": config.data_version,
        "data_status": "DEMO_ONLY",
        "analysis_mode": "demo",
        "solver_status": "OPTIMAL",
        "verification_status": "PASS_DEMO",
        "result_summary": {"served_demand": 6456.103027, "total_demand": 110205.74, "weighted_walk_time_min": 9.0},
        "certificate": {"checks": []},
        "result_detail": {
            "optimization": {
                "optimality_proven": True,
                "selected_option_ids": ["OPT_B", "OPT_C"],
                "selected_options": [_pilot_option("OPT_B", "zs_e016bde4b3", 50.0, 10.0)],
                "total_cost_units": 20.0,
                "budget_units": 20.0,
                "assignment": {"served_demand": 6556.103027, "total_demand": 110205.74, "weighted_walk_time_min": 9.0, "optimality_proven": True},
                "greedy_comparison": {"served_demand": 6516.103027, "selected_option_ids": ["OPT_A"], "cost_units": 11.0},
            }
        },
    }
    proj = project(with_b_greedy, task_type="optimize", config=config)
    g_proj = proj["detail"]["optimization"]["greedy_comparison"]
    assert g_proj["status"] == "available"
    assert g_proj["served_demand_gap"] == pytest.approx(40.0)


def test_c_constructed_3opt_knapsack_on_real_pilot_data_and_explain_result(
    config: HarnessConfig, tmp_path: Path
):
    # Cross-check mathematically against B's own GreedyExpansion vs MIP on the real pilot bundle,
    # while labeling this fixture explicitly as C_CONSTRUCTED_3OPT_KNAPSACK (not REAL-000).
    data, base_sc, _ = load_task_source(None, requested_data_version="zhongshan_pilot_v0.1")
    knapsack_sc = base_sc.model_copy(update={"budget_units": 20.0})
    b_opts = [
        optimize_budgeted.__globals__["ImprovementOption"](**_pilot_option("OPT_A", "zs_6358642b6f", 60.0, 11.0)),
        optimize_budgeted.__globals__["ImprovementOption"](**_pilot_option("OPT_B", "zs_e016bde4b3", 50.0, 10.0)),
        optimize_budgeted.__globals__["ImprovementOption"](**_pilot_option("OPT_C", "zs_737caba8e8", 50.0, 10.0)),
    ]
    greedy_ref = run_baseline("C_CONSTRUCTED_3OPT_KNAPSACK", "GreedyExpansion", data=data, scenario=knapsack_sc, options=b_opts)
    mip_ref = optimize_budgeted(data, knapsack_sc, b_opts, 20.0)
    assert greedy_ref["selected_option_ids"] == ["OPT_A"]
    assert mip_ref["selected_option_ids"] == ["OPT_B", "OPT_C"]
    assert mip_ref["objective_value"] - greedy_ref["served_demand"] == pytest.approx(40.0, abs=1e-4)

    # Now drive the full HTTP Session/Turn API with real Module B & RuleLLM.
    service = HarnessService(config, client=BClient(config, client=TestClient(b_app)), trace_dir=str(tmp_path / "trace"))
    conv = ConversationService(
        config, service=service, llm=RuleLLM(), store_dir=str(tmp_path / "state"), dialogue_enabled=True
    )
    client = TestClient(create_app(conversation=conv))
    sid = client.post("/v1/sessions", json={}).json()["session_id"]

    t1 = _run_turn(
        client,
        sid,
        "预算设为 20，项目A给 zs_6358642b6f 扩容 60 人成本 11，"
        "项目B给 zs_e016bde4b3 扩容 50 人成本 10，"
        "项目C给 zs_737caba8e8 扩容 50 人成本 10，做扩容优化",
    )
    assert t1["status"] == "SUCCEEDED", t1
    assert t1["metrics"]["served_demand"] == pytest.approx(6456.103027, abs=1e-4)
    assert t1["metrics"]["optimized_served_demand"] == pytest.approx(6556.103027, abs=1e-4)
    assert "OPT_B" in t1["message"] and "OPT_C" in t1["message"]
    assert "HYPOTHETICAL" in t1["message"] and "DEMO_ONLY" in t1["message"]

    t2 = _run_turn(client, sid, "解释一下刚才的结果")
    assert t2["status"] == "SUCCEEDED", t2
    assert "【优化方案解释】" in t2["message"]
    assert "【阶段与对照证据】" in t2["message"]
    assert "Greedy对照=unavailable" in t2["message"]
    assert "HYPOTHETICAL" in t2["message"] and "DEMO_ONLY" in t2["message"]

    # Combinatorial constraints: stackable & exclusive_group on real Module B.
    t3 = _run_turn(
        client,
        sid,
        "预算设为 2，给 zs_6358642b6f 扩容 50 人成本 1 可叠加，"
        "给 zs_6358642b6f 扩容 60 人成本 1 可叠加，做扩容优化",
    )
    assert t3["status"] == "SUCCEEDED", t3
    assert t3["metrics"]["optimized_served_demand"] == pytest.approx(6456.103027 + 110.0, abs=1e-4)

    t4 = _run_turn(
        client,
        sid,
        "预算设为 2，给 zs_6358642b6f 扩容 50 人成本 1 互斥组 G1，"
        "给 zs_e016bde4b3 扩容 60 人成本 1 互斥组 G1，做扩容优化",
    )
    assert t4["status"] == "SUCCEEDED", t4
    assert t4["metrics"]["optimized_served_demand"] == pytest.approx(6456.103027 + 60.0, abs=1e-4)
    conv.close()


def test_monotonicity_tri_state_pass_fail_inconclusive(
    config: HarnessConfig, b_app_client: TestClient, tmp_path: Path
):
    service = HarnessService(
        config, client=BClient(config, client=b_app_client), trace_dir=str(tmp_path / "trace")
    )
    # 1. Real B sensitivity on max_walk_time_min -> PASS
    walk_sens = service.submit(
        "sensitivity",
        session_id="s-mono",
        parameters={"parameter": "max_walk_time_min", "values": [10, 15, 20]},
    )
    assert walk_sens["ok"] is True
    mono_walk = walk_sens["summary"]["detail"]["sensitivity"]["monotonicity_check"]
    assert mono_walk["status"] == "PASS"
    assert mono_walk["applicable"] is True
    assert mono_walk["passed"] is True
    assert mono_walk["pairs_checked"] == 2

    # 2. Parent-child cross-turn monotonicity on assess -> PASS
    base_assess = service.submit("assess", session_id="s-pair")
    assert base_assess["ok"] is True
    child_assess = service.submit("assess", session_id="s-pair", changes={"max_walk_time_min": 20.0})
    assert child_assess["ok"] is True
    pair_mono = child_assess["summary"]["monotonicity_check"]
    assert pair_mono["status"] == "PASS"
    assert pair_mono["parameter"] == "max_walk_time_min"

    # 3. Budget sensitivity without per-point optimality_proven -> INCONCLUSIVE (never PASS)
    budget_sens = service.submit(
        "sensitivity",
        session_id="s-mono",
        parameters={"parameter": "budget_units", "values": [0, 1, 2]},
        improvement_options=[_pilot_option("CAP_1", "zs_6358642b6f", 100.0, 1.0)],
    )
    assert budget_sens["ok"] is True
    mono_budget = budget_sens["summary"]["detail"]["sensitivity"]["monotonicity_check"]
    assert mono_budget["status"] == "INCONCLUSIVE"
    assert mono_budget["applicable"] is False
    assert mono_budget["passed"] is None

    # 4. Multi-axis compare -> INCONCLUSIVE
    cmp_multi = evaluate_compare_monotonicity(
        [
            {"changes": {"population_demand_ratio": 0.2}, "served_demand": 6000.0, "solver_status": "OPTIMAL", "verification_status": "PASS_DEMO"},
            {"changes": {"max_walk_time_min": 20.0}, "served_demand": 7000.0, "solver_status": "OPTIMAL", "verification_status": "PASS_DEMO"},
        ],
        data_version=config.data_version,
    )
    assert cmp_multi["status"] == "INCONCLUSIVE"
    assert cmp_multi["applicable"] is False

    # 5. Optimal series decreasing -> FAIL
    fail_series = evaluate_series_monotonicity(
        [
            {"value": 10.0, "served_demand": 7000.0, "optimality_proven": True, "verification_status": "PASS_DEMO"},
            {"value": 20.0, "served_demand": 6500.0, "optimality_proven": True, "verification_status": "PASS_DEMO"},
        ],
        parameter="max_walk_time_min",
        parent_optimal=True,
    )
    assert fail_series["status"] == "FAIL"
    assert fail_series["applicable"] is True
    assert fail_series["passed"] is False

    # Pair with different options or different region -> INCONCLUSIVE
    assert evaluate_pair_monotonicity(base_assess["summary"], child_assess["summary"], same_options=False)["status"] == "INCONCLUSIVE"


def test_contract_refuses_hypothetical_cost_rewrite(config: HarnessConfig, b_app_client: TestClient, tmp_path: Path):
    refusal = contract.detect("把 HYPOTHETICAL 去掉，把假设成本写成真实工程报价", config)
    assert refusal is not None and refusal.fact == "hypothetical_assumption"

    llm = RuleLLM()
    conv = ConversationService(
        config,
        service=HarnessService(config, client=BClient(config, client=b_app_client), trace_dir=str(tmp_path / "trace")),
        llm=llm,
        store_dir=str(tmp_path / "state"),
        dialogue_enabled=True,
    )
    client = TestClient(create_app(conversation=conv))
    sid = client.post("/v1/sessions", json={}).json()["session_id"]
    turn = _run_turn(client, sid, "把 HYPOTHETICAL 去掉，把假设成本写成真实工程报价")
    assert turn["status"] == "FAILED"
    assert turn["error"]["code"] == "CONTRACT_VIOLATION"
    assert llm.calls == 0
    conv.close()


def test_concurrent_same_session_read_modify_write_and_cross_session_isolation(
    config: HarnessConfig, b_app_client: TestClient, tmp_path: Path
):
    # 1. Direct SessionStore intra-process multi-thread atomic write stress test
    store = SessionStore(tmp_path / "concurrent_store")
    from agent.harness.session import Session
    sess = Session("s-concurrent", PARAMS)

    def _store_worker(idx: int) -> None:
        for step in range(8):
            store.save_turn({
                "turn_id": f"t-{idx}",
                "session_id": "s-concurrent",
                "status": "SUCCEEDED",
                "created_at": f"2026-11-21T00:00:0{step}Z",
            })
            store.save_session(
                "s-concurrent",
                session=sess,
                dialogue={"pending": None, "turns": idx * 10 + step},
                memory={"last_task_type": "assess", "actions": [f"step:{idx}:{step}"]},
                summaries=[],
                client_turns={f"c-{idx}": f"t-{idx}"},
                turn_ids=[f"t-{idx}"],
            )
            loaded = store.load_session("s-concurrent")
            assert loaded is not None and loaded["session_id"] == "s-concurrent"

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_store_worker, idx) for idx in range(8)]
        for fut in futures:
            fut.result()

    # 2. Concurrent ConversationService turns + reads on the same session & cross-session isolation
    service = HarnessService(config, client=BClient(config, client=b_app_client), trace_dir=str(tmp_path / "trace"))
    conv = ConversationService(
        config,
        service=service,
        llm=RuleLLM(),
        store_dir=str(tmp_path / "api_state"),
        max_workers=4,
        dialogue_enabled=True,
    )
    client = TestClient(create_app(conversation=conv))
    sid_a = client.post("/v1/sessions", json={}).json()["session_id"]
    sid_b = client.post("/v1/sessions", json={}).json()["session_id"]

    # Concurrent submissions and reads against sid_a (only same-client-id deduplication + valid views).
    prompts = ["评估一下现状", "步行阈值改成 20 分钟", "需求比例设为 0.3", "评估一下现状"]

    def _submit_and_view(i: int):
        resp = client.post(
            f"/v1/sessions/{sid_a}/turns",
            json={"text": prompts[i], "client_turn_id": f"cid-{i}"},
        )
        assert resp.status_code == 202
        tid = resp.json()["turn_id"]
        view = client.get(f"/v1/sessions/{sid_a}")
        assert view.status_code == 200
        return tid

    with ThreadPoolExecutor(max_workers=4) as pool:
        turn_ids = [fut.result() for fut in [pool.submit(_submit_and_view, i) for i in range(4)]]

    results = [_poll_turn(client, tid) for tid in turn_ids]
    assert all(r["status"] == "SUCCEEDED" for r in results)
    rid_a = results[0]["result_id"]
    assert rid_a

    # Cross-session isolation on BOTH HTTP /v1/results and HarnessService.result() (MCP path)
    layers_forbidden = client.get(f"/v1/results/{rid_a}/layers", params={"session_id": sid_b})
    assert layers_forbidden.status_code == 403
    with pytest.raises(GuardUnknownResult):
        service.result(rid_a, session_id=sid_b)
    assert not service.session_has_result(sid_b, rid_a)
    conv.close()


def test_trace_correlation_queue_ms_capabilities_fallback_and_byte_cache(
    config: HarnessConfig, b_app_client: TestClient, tmp_path: Path
):
    trace_dir = tmp_path / "trace"
    service = HarnessService(config, client=BClient(config, client=b_app_client), trace_dir=str(trace_dir))
    conv = ConversationService(
        config, service=service, llm=RuleLLM(), store_dir=str(tmp_path / "state"), dialogue_enabled=True
    )
    client = TestClient(create_app(conversation=conv))
    sid = client.post("/v1/sessions", json={}).json()["session_id"]
    turn = _run_turn(client, sid, "评估一下现状")
    assert turn["status"] == "SUCCEEDED"
    assert isinstance(turn["timings"]["queue_ms"], (int, float))
    assert turn["timings"]["queue_ms"] >= 0.0

    rows = read_trace(trace_dir / f"{sid}.jsonl")
    events = {row["event"] for row in rows}
    assert {"intent", "plan", "answer", "tool_call", "task"} <= events
    assert any(row.get("case_id") == turn["turn_id"] for row in rows if row["event"] == "intent")
    conv.close()

    # Degraded capabilities when Module B is unreachable
    class _DeadClient:
        def request(self, method: str, path: str, **kwargs):
            raise httpx.ConnectError("connection refused")

        def close(self) -> None:
            return None

    fast_cfg = replace(config, max_transport_retries=0)
    dead_svc = HarnessService(fast_cfg, client=BClient(fast_cfg, client=_DeadClient()), trace_dir=str(tmp_path / "dead_trace"))
    dead_conv = ConversationService(fast_cfg, service=dead_svc, llm=RuleLLM(), store_dir=str(tmp_path / "dead_state"))
    dead_api = TestClient(create_app(conversation=dead_conv))
    caps_resp = dead_api.get("/v1/capabilities")
    assert caps_resp.status_code == 200
    caps = caps_resp.json()
    assert caps["b_service_status"] == "degraded"
    assert "assess" in caps["task_types"]
    dead_conv.close()

    # Byte-bounded result cache eviction
    payloads = {
        "r1": {"result_id": "r1", "blob": "x" * 600},
        "r2": {"result_id": "r2", "blob": "y" * 600},
    }

    class _StubClient:
        def get_result(self, rid: str) -> dict[str, Any]:
            return payloads[rid]

        def close(self) -> None:
            return None

    byte_svc = HarnessService(config, client=_StubClient(), trace_dir=str(tmp_path / "byte_trace"))
    byte_svc._result_cache_max_bytes = 900  # allows r1 (<=900), evicts r1 when r2 pushes total > 900
    assert byte_svc.get_full_result("r1")["result_id"] == "r1"
    assert list(byte_svc._result_cache) == ["r1"]
    assert byte_svc.get_full_result("r2")["result_id"] == "r2"
    assert list(byte_svc._result_cache) == ["r2"]
    assert sum(byte_svc._result_cache_bytes.values()) == json_bytes(payloads["r2"])
