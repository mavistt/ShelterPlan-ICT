from __future__ import annotations

import copy
import csv
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

V3 = Path(__file__).resolve().parents[1]
ROOT = V3.parents[1]
C_ROOT_RAW = os.environ.get("SHELTERPLAN_MODULE_C_PATH")
if not C_ROOT_RAW:
    raise SystemExit("Set SHELTERPLAN_MODULE_C_PATH to a separate Module C checkout; no C source is bundled here.")
C_ROOT = Path(C_ROOT_RAW).resolve()
if not (C_ROOT / "agent").is_dir():
    raise SystemExit(f"Module C path does not contain agent/: {C_ROOT}")
RUN_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
sys.path[:0] = [str(C_ROOT), str(ROOT)]
os.chdir(V3)

from starlette.testclient import TestClient
from backend.app import app as b_app
from backend.api import planning as b_planning
from backend.graph import executor as b_graph
from backend.models import TaskRequest
from backend.storage import JsonResultStore
from agent import service as c_service_module
from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.errors import GuardClaimViolation, GuardInvalidOption, GuardUnclearOperation, GuardUnknownResult, ResultMismatch
from agent.harness import guards
from agent.intent.normalizer import apply_ops
from agent.intent.schema import ParamOp
from agent.service import HarnessService
from agent.workflow.answer import _solver_sentence
from agent.workflow.controller import ControlResult
from agent.workflow.router import RoutePlan
from tests.module_c.rule_llm import RuleLLM
from agent.api import ConversationService


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def source_paths() -> dict[str, Path]:
    return {
        "real000": ROOT / "merge_data/v2_difficulty_discovery/runs/real_gis_search/REAL-000-TARGETED-TRAP.json",
        "real081": ROOT / "merge_data/v2_difficulty_discovery/runs/real_gis_search/REAL-081.json",
        "synthetic": ROOT / "merge_data/v2_difficulty_discovery/runs/synthetic_stress/SYN-greedy-counterexample.json",
        "audit": ROOT / "merge_data/runs/01_structure_audit.json",
        "d03": ROOT / "merge_data/runs/full-20261113-main/gt/D03.json",
    }


def make_gt(real000: dict, real081: dict, synthetic: dict, audit: dict) -> list[dict[str, Any]]:
    oracle0 = real000["result"]["oracle"]
    oracle81 = real081["result"]["oracle"]
    oracle_syn = synthetic["oracle"]
    closure_id = "zs_1b4df7920b"
    baseline = float(audit["baseline_15_min"]["served_demand"])
    closure_delta = 3130.9577
    opt0 = real000["options"]
    opt81 = real081["options"]
    oracle0_shelters = [row["shelter_id"] for row in opt0 if row["option_id"] in set(oracle0["selected_option_ids"])]
    return [
        {
            "task_id": "V3-01-REAL000-OPT", "family": "single_round_budget_optimization", "turns": [
                {"user_intent": "预算20，按 REAL-000 三个 GIS 锚定扩容候选做预算优化。", "expected_params": {"budget_units": 20.0}, "expected_tool_constraints": {"task_type": "optimize", "candidate_count": 3, "session_scope": "same"}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"budget_units": 20.0, "solver_status": "OPTIMAL", "verification_status": "PASS_DEMO"},
            "mathematical_or_reference": {"kind": "independent_v2_oracle", "objective_served_demand": oracle0["objective_served_demand"], "rounding_error_upper_bound_persons": oracle0["rounding_error_upper_bound_persons"], "selected_option_ids": oracle0["selected_option_ids"], "greedy_served_demand": real000["result"]["greedy"]["served_demand"], "greedy_selected_option_ids": real000["result"]["greedy"]["selected_option_ids"]},
            "refusal_or_clarification": {"refuse_if": ["budget absent", "candidate count >12", "candidate not explicitly supplied"]},
            "verifier_expected": "PASS_DEMO; DEMO_ONLY disclosure and hypothetical-option warning remain",
            "source": str(source_paths()["real000"].relative_to(ROOT)), "assumptions": ["A GIS demand/OD/capacity unchanged", "option costs and capacity deltas are hypothetical"]
        },
        {
            "task_id": "V3-02-REAL081-OPT", "family": "single_round_budget_optimization", "turns": [
                {"user_intent": "预算26.64，优化 REAL-081 的8个GIS锚定候选。", "expected_params": {"budget_units": 26.64}, "expected_tool_constraints": {"task_type": "optimize", "candidate_count": 8}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"budget_units": 26.64, "solver_status": "OPTIMAL", "verification_status": "PASS_DEMO"},
            "mathematical_or_reference": {"kind": "independent_v2_oracle", "objective_served_demand": oracle81["objective_served_demand"], "rounding_error_upper_bound_persons": oracle81["rounding_error_upper_bound_persons"], "selected_option_ids": oracle81["selected_option_ids"], "greedy_served_demand": real081["result"]["greedy"]["served_demand"], "greedy_selected_option_ids": real081["result"]["greedy"]["selected_option_ids"]},
            "refusal_or_clarification": {"refuse_if": ["budget absent", "candidate count >12"]},
            "verifier_expected": "PASS_DEMO; data source stays DEMO_ONLY",
            "source": str(source_paths()["real081"].relative_to(ROOT)), "assumptions": ["A GIS is unchanged", "all 8 option costs and capacity deltas are hypothetical"]
        },
        {
            "task_id": "V3-03-SYN8-TRAP", "family": "fully_synthetic_greedy_vs_mip", "turns": [
                {"user_intent": "在V2全合成8候选反例上比较Greedy与MIP。", "expected_params": {"budget_units": 20.0}, "expected_tool_constraints": {"candidate_count": 8, "must_not_increase_limit": True}, "expected_status": "NOT_RUN"}],
            "expected_final_state": {"solver_status": "NOT_RUN"},
            "mathematical_or_reference": {"kind": "independent_v2_exhaustive_maxflow_oracle", "objective_served_demand": oracle_syn["objective_served_demand"], "rounding_error_upper_bound_persons": oracle_syn["rounding_error_upper_bound_persons"], "selected_option_ids": oracle_syn["selected_option_ids"], "greedy_served_demand": synthetic["greedy"]["served_demand"], "greedy_selected_option_ids": synthetic["greedy"]["selected_option_ids"]},
            "refusal_or_clarification": {"refuse_if": ["public API does not accept synthetic demand/shelter/OD payload injection"]},
            "verifier_expected": "V2 PASS_DEMO fixture; no V3 C/B claim",
            "source": str(source_paths()["synthetic"].relative_to(ROOT)), "assumptions": ["fully synthetic fixture", "V3 route is not run because public TaskRequest only accepts task parameters/options, not arbitrary planning graph"]
        },
        {
            "task_id": "V3-04-GREEDY-MIP-EXPLAIN", "family": "greedy_vs_mip_explanation", "turns": [
                {"user_intent": "解释 REAL-000 中 Greedy 6516.103 与 MIP 6556.103 的差异。", "expected_params": {"budget_units": 20.0}, "expected_tool_constraints": {"task_type": "explain_result", "result_id_must_belong_to_session": True}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"solver_status": "OPTIMAL", "verification_status": "PASS_DEMO", "must_explain_greedy_and_mip": True, "must_bind_numbers_to_v2_reference": True},
            "mathematical_or_reference": {"kind": "independent_v2_oracle", "objective_served_demand": oracle0["objective_served_demand"], "selected_option_ids": oracle0["selected_option_ids"], "greedy_served_demand": real000["result"]["greedy"]["served_demand"], "greedy_selected_option_ids": real000["result"]["greedy"]["selected_option_ids"], "gap_persons": real000["result"]["mip_minus_greedy_persons"]},
            "refusal_or_clarification": {"refuse_if": ["result belongs to a different session", "explanation claims numbers not in result summary"]},
            "verifier_expected": "PASS_DEMO; numeric claims must be result-bound",
            "source": str(source_paths()["real000"].relative_to(ROOT)), "assumptions": ["explain only V2 measured result; it must not call the optimizer again"]
        },
        {
            "task_id": "V3-05-MULTITURN-BUDGET", "family": "multi_turn_budget_adjustment_and_resume", "turns": [
                {"user_intent": "第1轮：预算15，做扩容优化但不提供候选。", "expected_params": {"budget_units": 15.0}, "expected_tool_constraints": {"no_B_call": True}, "expected_status": "NEEDS_CLARIFICATION"},
                {"user_intent": "第2轮：预算改为20并补充REAL-000三个候选，继续优化。", "expected_params": {"budget_units": 20.0}, "expected_tool_constraints": {"candidate_count": 3, "pending_task_must_resume": True}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"budget_units": 20.0, "solver_status": "OPTIMAL", "verification_status": "PASS_DEMO", "selected_shelter_ids": oracle0_shelters},
            "mathematical_or_reference": {"kind": "independent_v2_oracle_for_resumed_final_turn", "objective_served_demand": oracle0["objective_served_demand"], "rounding_error_upper_bound_persons": oracle0["rounding_error_upper_bound_persons"], "selected_option_ids": oracle0["selected_option_ids"], "selected_shelter_ids": oracle0_shelters, "per_turn_state_is_gt": True},
            "refusal_or_clarification": {"refuse_if": ["first turn calls B without candidates", "second turn loses candidate table or retains budget15"]},
            "verifier_expected": "first turn no certificate; resumed final PASS_DEMO",
            "source": str(source_paths()["real000"].relative_to(ROOT)), "assumptions": ["second-turn natural language explicitly supplies all three hypothetical options"]
        },
        {
            "task_id": "V3-06-BUDGET-WALK-BIND", "family": "multi_parameter_numeric_binding", "turns": [
                {"user_intent": "评估现状，预算上限30，步行阈值15分钟。", "expected_params": {"budget_units": 30.0, "max_walk_time_min": 15.0}, "expected_tool_constraints": {"task_type": "assess", "both_numbers_must_bind_to_named_fields": True}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"budget_units": 30.0, "max_walk_time_min": 15.0, "solver_status": "OPTIMAL", "verification_status": "PASS_DEMO"},
            "mathematical_or_reference": {"kind": "prior_v1_baseline_reference", "served_demand": float(audit["baseline_15_min"]["served_demand"]), "note": "budget does not change the pure assess baseline"},
            "refusal_or_clarification": {"refuse_if": ["budget is set to15 from the walk-time literal", "either stated value is silently dropped"]},
            "verifier_expected": "PASS_DEMO; exact scenario fields equal text",
            "source": str(source_paths()["audit"].relative_to(ROOT)), "assumptions": ["RuleLLM deterministic adapter only; no claim about real LLM"]
        },
        {
            "task_id": "V3-07-REVISE-PLAN", "family": "user_revises_plan_and_reoptimizes", "turns": [
                {"user_intent": "先用REAL-000预算20完整候选求解，之后删除A，只保留B/C并重新优化。", "expected_params": {"budget_units": 20.0}, "expected_tool_constraints": {"second_call_options": ["V2_TRAP_B", "V2_TRAP_C"], "reoptimize": True}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"budget_units": 20.0, "selected_option_ids": ["V2_TRAP_B", "V2_TRAP_C"], "solver_status": "OPTIMAL"},
            "mathematical_or_reference": {"kind": "independent_v2_oracle_restricted_candidate_subset", "objective_served_demand": oracle0["objective_served_demand"], "selected_option_ids": oracle0["selected_option_ids"]},
            "refusal_or_clarification": {"refuse_if": ["removed A reappears", "request reuses stale result without a new B call"]},
            "verifier_expected": "PASS_DEMO",
            "source": str(source_paths()["real000"].relative_to(ROOT)), "assumptions": ["removing an option cannot improve over the 3-option optimum; original Oracle optimum remains feasible in B/C subset"]
        },
        {
            "task_id": "V3-08-DISABLE-BOTTLENECK", "family": "site_shutdown_counterfactual", "turns": [
                {"user_intent": f"评估15分钟基线，然后停用瓶颈场所 {closure_id} 并比较影响。", "expected_params": {"max_walk_time_min": 15.0, "disabled_shelter_ids": [closure_id]}, "expected_tool_constraints": {"two_scenarios": ["baseline", "disabled"], "candidate_count": 0}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"verification_status": "PASS_DEMO", "disabled_shelter_ids": [closure_id]},
            "mathematical_or_reference": {"kind": "v1_historical_reference", "baseline_served_demand": baseline, "closure_loss_persons": closure_delta, "closure_served_demand_reference": baseline - closure_delta, "oracle": False},
            "refusal_or_clarification": {"refuse_if": ["service reports no scenario change", "closure increases service with no other changes"]},
            "verifier_expected": "both results PASS_DEMO; historical V1 numeric reference is directional, not a fresh independent oracle",
            "source": str(source_paths()["audit"].relative_to(ROOT)), "assumptions": ["closure is a hypothetical scenario", "capacity data are DEMO_ONLY and unverified"]
        },
        {
            "task_id": "V3-09-RESTORE-BUDGET", "family": "parameter_undo_restore", "turns": [
                {"user_intent": "预算设20；下一轮预算设25；然后撤销最近的预算修改。", "expected_params": {"budget_units_sequence": [20.0, 25.0, 20.0]}, "expected_tool_constraints": {"restore_is_field_scoped": True, "no_solver_call_required": True}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"budget_units": 20.0},
            "mathematical_or_reference": {"kind": "state_transition_gt", "expected_budget_history": [20.0, 25.0, 20.0]},
            "refusal_or_clarification": {"refuse_if": ["restore changes walking ratio or disabled list", "history value is missing"]},
            "verifier_expected": "no B certificate expected; parameter history only",
            "source": "V3-created deterministic transition from C Session/normalizer contract", "assumptions": ["no live planning call is part of this state-only task"]
        },
        {
            "task_id": "V3-10-ASK-MISSING-BUDGET", "family": "clarify_missing_budget", "turns": [
                {"user_intent": "帮我做扩容优化。", "expected_params": {"budget_units": None}, "expected_tool_constraints": {"no_B_call": True}, "expected_status": "NEEDS_CLARIFICATION"}],
            "expected_final_state": {"status": "NEEDS_CLARIFICATION", "clarification_fields": ["budget_units"]},
            "mathematical_or_reference": {"kind": "clarification_contract", "no_math_result": True},
            "refusal_or_clarification": {"ask_for": ["budget_units"], "do_not_infer": True},
            "verifier_expected": "not run; no B result",
            "source": "C public application API contract", "assumptions": ["RuleLLM deterministic adapter"]
        },
        {
            "task_id": "V3-11-ASK-MISSING-CANDIDATES", "family": "clarify_missing_candidates", "turns": [
                {"user_intent": "预算20，帮我做扩容优化。", "expected_params": {"budget_units": 20.0}, "expected_tool_constraints": {"no_B_call": True}, "expected_status": "NEEDS_CLARIFICATION"}],
            "expected_final_state": {"status": "NEEDS_CLARIFICATION", "clarification_fields": ["improvement_options"]},
            "mathematical_or_reference": {"kind": "clarification_contract", "no_math_result": True},
            "refusal_or_clarification": {"ask_for": ["improvement_options"], "do_not_invent": True},
            "verifier_expected": "not run; no B result",
            "source": "C public application API contract", "assumptions": ["RuleLLM deterministic adapter"]
        },
        {
            "task_id": "V3-12-DEMO-ONLY-DISCLOSURE", "family": "demo_only_status_explanation", "turns": [
                {"user_intent": "说明REAL-000方案的数据状态和能否作为现场应急指引。", "expected_params": {"data_status": "DEMO_ONLY"}, "expected_tool_constraints": {"must_not_call_verified_mode": True}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"data_status": "DEMO_ONLY", "verified_for_real_planning": False},
            "mathematical_or_reference": {"kind": "source_status_contract", "result_id_from": "V3-01"},
            "refusal_or_clarification": {"refuse_claims": ["现场验证", "可直接执行", "verified emergency guidance"]},
            "verifier_expected": "PASS_DEMO plus explicit disclosure",
            "source": str(source_paths()["real000"].relative_to(ROOT)), "assumptions": ["all V2 option costs are hypothetical"]
        },
        {
            "task_id": "V3-13-FEASIBLE-WORDING", "family": "feasible_status_wording", "turns": [
                {"user_intent": "使用FEASIBLE结果作答，说明是否已证明最优。", "expected_params": {"solver_status": "FEASIBLE", "optimality_proven": False}, "expected_tool_constraints": {"must_not_claim_proven_optimal": True}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"phrase_contains": ["FEASIBLE", "未证明最优", "不得表述为"], "semantic_rule": "the answer may quote '已证明最优' only inside an explicit prohibition; it must not assert optimality"},
            "mathematical_or_reference": {"kind": "existing_v1_feasible_contract", "source": str(source_paths()["d03"].relative_to(ROOT))},
            "refusal_or_clarification": {"refuse_claims": ["optimality proven when false"]},
            "verifier_expected": "FEASIBLE accepted without optimal claim; claim='optimal' rejected",
            "source": str(source_paths()["d03"].relative_to(ROOT)), "assumptions": ["formatter/guard path only; no deterministic live timeout or FEASIBLE plan is exposed"]
        },
        {
            "task_id": "V3-14-VERIFIER-RECOVERY", "family": "verifier_rejects_then_recovers", "turns": [
                {"user_intent": "验证器收到FAIL结果时不得发布；修复后用原场景安全重试。", "expected_params": {"budget_units": 20.0}, "expected_tool_constraints": {"first_injected_status": "FAIL", "one_clean_retry": True}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"first_status": "REJECTED_RESULT_MISMATCH", "recovery_solver_status": "OPTIMAL", "verification_status": "PASS_DEMO"},
            "mathematical_or_reference": {"kind": "same_v2_independent_oracle_as_v3_01", "objective_served_demand": oracle0["objective_served_demand"], "selected_option_ids": oracle0["selected_option_ids"]},
            "refusal_or_clarification": {"refuse_if": ["verification_status != PASS_DEMO/PASS_VERIFIED", "retry mutates user parameters"]},
            "verifier_expected": "reject injected FAIL, accept fresh B PASS_DEMO",
            "source": str(source_paths()["real000"].relative_to(ROOT)), "assumptions": ["fault injection changes only returned verifier status, not source B result"]
        },
        {
            "task_id": "V3-15-CROSS-SESSION-RESULT", "family": "cross_session_result_denial", "turns": [
                {"user_intent": "另一Session请求读取owner Session的result_id。", "expected_params": {}, "expected_tool_constraints": {"deny_before_B_get_result": True}, "expected_status": "REFUSED"}],
            "expected_final_state": {"error_code": "GUARD_UNKNOWN_RESULT", "attacker_B_get_result_calls": 0},
            "mathematical_or_reference": {"kind": "session_ownership_security_invariant", "no_math_result": True},
            "refusal_or_clarification": {"must_refuse": True},
            "verifier_expected": "no result published or fetched",
            "source": "C service membership contract and V3 security regression", "assumptions": ["spy BClient records upstream access attempts"]
        },
        {
            "task_id": "V3-16-CANDIDATE-LIMIT", "family": "candidate_limit_boundary", "turns": [
                {"user_intent": "校验12候选可被接纳、13候选在C/B边界前拒绝。", "expected_params": {"candidate_limit": 12}, "expected_tool_constraints": {"12_count_allowed_by_schema": True, "13_count_rejected_before_B": True}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"max_candidates": 12, "13_candidate_calls_to_B": 0},
            "mathematical_or_reference": {"kind": "public_contract", "source": str((ROOT / "backend/models.py").relative_to(ROOT))},
            "refusal_or_clarification": {"refuse_if": ["candidate_count >12"], "do_not": ["raise limit to64"]},
            "verifier_expected": "not run for 13 candidates; schema/guard refusal",
            "source": "backend.models.TaskRequest and C guards.validate_improvement_options", "assumptions": ["12-item Pydantic contract validation only; no duplicate-option solver semantics claimed"]
        },
        {
            "task_id": "V3-17-ORDINARY-AGENT-BASELINE", "family": "architecture_baseline", "turns": [
                {"user_intent": "same prompts through an ordinary independent tool agent", "expected_params": {"model": "same as other arms"}, "expected_tool_constraints": {"paid_api": False}, "expected_status": "NOT_RUN"}],
            "expected_final_state": {"status": "NOT_RUN"},
            "mathematical_or_reference": {"kind": "not_applicable_without_agent", "no_math_result": True},
            "refusal_or_clarification": {"must_not_fabricate_result": True},
            "verifier_expected": "NOT_RUN",
            "source": "V3 architecture availability audit", "assumptions": ["no real LLM configured; RuleLLM is not an ordinary tool agent"]
        },
        {
            "task_id": "V3-18-SKILL-CACHE-REPEAT", "family": "skill_graph_cache_repeat", "turns": [
                {"user_intent": "重复相同REAL-000任务，检查Skill Graph依赖缓存和结果一致性。", "expected_params": {"budget_units": 20.0}, "expected_tool_constraints": {"same_scenario_and_options": True, "candidate_count": 3}, "expected_status": "SUCCEEDED"}],
            "expected_final_state": {"both_solver_status": "OPTIMAL", "both_verification_status": "PASS_DEMO", "same_selected_options": oracle0["selected_option_ids"]},
            "mathematical_or_reference": {"kind": "independent_v2_oracle", "objective_served_demand": oracle0["objective_served_demand"], "rounding_error_upper_bound_persons": oracle0["rounding_error_upper_bound_persons"], "selected_option_ids": oracle0["selected_option_ids"]},
            "refusal_or_clarification": {"must_not_claim_no_graph_ablation": True},
            "verifier_expected": "both PASS_DEMO; B trace cache_hit rows inspected",
            "source": str(source_paths()["real000"].relative_to(ROOT)), "assumptions": ["cache behavior is measured by B trace rows; not a no-Skill-Graph ablation"]
        },
    ]


def save_trace(task_id: str, payload: dict[str, Any]) -> str:
    path = V3 / "traces" / f"{task_id}.json"
    write_json(path, payload)
    return str(path.relative_to(V3)).replace("\\", "/")


def summarized_full(full: dict[str, Any] | None) -> dict[str, Any] | None:
    if not full:
        return None
    detail = full.get("result_detail") or {}
    optimization = detail.get("optimization") or {}
    selected_options = optimization.get("selected_options") or []
    return {
        "result_id": full.get("result_id"),
        "scenario_id": full.get("scenario_id"),
        "solver_status": full.get("solver_status"),
        "verification_status": full.get("verification_status"),
        "data_status": full.get("data_status"),
        "result_summary": full.get("result_summary"),
        "selected_option_ids": optimization.get("selected_option_ids"),
        "selected_shelter_ids": [row.get("shelter_id") for row in selected_options],
        "total_cost_units": optimization.get("total_cost_units"),
        "objective_value": optimization.get("objective_value"),
        "graph_trace": [
            {"skill_id": row.get("skill_id"), "cache_hit": row.get("cache_hit"), "status": row.get("status"), "duration_ms": row.get("duration_ms")}
            for row in (full.get("trace") or [])
        ],
        "cache_hit": full.get("cache_hit"),
        "recomputed_nodes": full.get("recomputed_nodes"),
        "plan_decision": full.get("plan_decision"),
        "elapsed_ms": full.get("elapsed_ms"),
    }


def direct_payload(case: dict[str, Any], task_id: str) -> dict[str, Any]:
    scenario = copy.deepcopy(case["scenario"])
    scenario_id = f"v3-direct-{task_id.lower()}"
    scenario["scenario_id"] = scenario_id
    scenario["parent_scenario_id"] = None
    scenario["changes"] = {"budget_units": float(scenario["budget_units"])}
    return {
        "task_id": task_id,
        "task_type": "optimize",
        "scenario": scenario,
        "scenario_id": scenario_id,
        "parent_scenario_id": None,
        "data_version": scenario["data_version"],
        "analysis_mode": "demo",
        "changes": scenario["changes"],
        "parameters": {},
        "improvement_options": copy.deepcopy(case["options"]),
        "idempotency_key": f"v3-direct:{task_id}",
    }


def run_direct_b(client: BClient, case: dict, task_id: str) -> dict[str, Any]:
    b_graph.CACHE.clear()
    start = time.perf_counter()
    submitted, job = client.submit_and_wait(direct_payload(case, task_id), deadline_s=180)
    full = client.get_result(job["result_id"])
    return {
        "wall_ms": (time.perf_counter() - start) * 1000,
        "submitted": submitted,
        "job": job,
        "full": full,
        "summary": summarized_full(full),
        "tool_calls": 3,
    }


def run_c(service: HarnessService, b_client: BClient, *, task_id: str,
          session_id: str, options: list[dict], budget: float, changes: dict | None = None,
          clear_cache: bool = True) -> dict[str, Any]:
    if clear_cache:
        b_graph.CACHE.clear()
    start = time.perf_counter()
    outcome = service.submit(
        "optimize", session_id=session_id,
        changes=changes if changes is not None else {"budget_units": budget},
        improvement_options=options,
    )
    wall_ms = (time.perf_counter() - start) * 1000
    full = b_client.get_result(outcome["result_id"]) if outcome.get("result_id") else None
    return {"wall_ms": wall_ms, "outcome": outcome, "full": full,
            "summary": summarized_full(full)}


def run_conversation(service: HarnessService, config: HarnessConfig, task_id: str,
                     turns: list[str]) -> list[dict[str, Any]]:
    store_dir = V3 / "state" / RUN_ID / task_id
    llm = RuleLLM()
    conversation = ConversationService(config, service=service, llm=llm, store_dir=str(store_dir))
    session_id = f"v3-{task_id.lower()}-{RUN_ID}"
    conversation.create_session(session_id)
    c_trace_path = V3 / "traces" / "harness" / f"{session_id}.jsonl"
    observations = []
    try:
        for index, user_text in enumerate(turns, 1):
            calls_before = service.client.call_count
            trace_line_start = len(c_trace_path.read_text(encoding="utf-8").splitlines()) if c_trace_path.exists() else 0
            accepted = conversation.submit_turn(session_id, user_text, client_turn_id=f"{task_id}-{index}")
            deadline = time.monotonic() + 180
            final = conversation.get_turn(accepted["turn_id"])
            while final["status"] == "RUNNING" and time.monotonic() < deadline:
                time.sleep(0.04)
                final = conversation.get_turn(accepted["turn_id"])
            if final["status"] == "RUNNING":
                raise TimeoutError(f"conversation turn {index} did not finish")
            trace_lines = c_trace_path.read_text(encoding="utf-8").splitlines() if c_trace_path.exists() else []
            trace_events = [json.loads(line) for line in trace_lines[trace_line_start:] if line.strip()]
            observations.append({
                "user_text": user_text,
                "accepted": accepted,
                "final": final,
                "session": conversation.session_view(session_id),
                "llm_calls_total": llm.calls,
                "b_client_call_count_delta": service.client.call_count - calls_before,
                "c_harness_tool_call_count": sum(event.get("event") == "tool_call" for event in trace_events),
                "c_harness_trace_events": trace_events,
            })
        return observations
    finally:
        conversation.close()


def main() -> None:
    for folder in ("tasks", "traces", "reports", "artifacts", "outputs/module_b/runs", "outputs/module_b/scenarios", "state"):
        (V3 / folder).mkdir(parents=True, exist_ok=True)
    sources = source_paths()
    real000, real081, synthetic, audit = (read_json(sources[key]) for key in ("real000", "real081", "synthetic", "audit"))
    gt = make_gt(real000, real081, synthetic, audit)
    write_json(V3 / "TASK_GT.json", {"schema_version": "v3.1", "independent_of_v3_outputs": True, "tasks": gt})

    rows: dict[str, dict[str, Any]] = {
        row["task_id"]: {"task_id": row["task_id"], "family": row["family"], "turns": len(row["turns"]), "run_status": "NOT_RUN", "task_success": "", "solver_status": "", "verification_status": "", "tool_calls": "", "duration_ms": "", "trace_path": "", "gt_source": row["source"], "note": ""}
        for row in gt
    }
    b_test_client = TestClient(b_app)
    config = HarnessConfig(trace_dir=V3 / "traces" / "harness", job_timeout_s=180)
    c_client = BClient(config, client=b_test_client)
    direct_client = BClient(config, client=b_test_client)
    service = HarnessService(config, client=c_client, trace_dir=str(V3 / "traces" / "harness"))
    task_outputs: dict[str, Any] = {}

    # 01/02: paired direct B and C Harness requests, same exact candidates and solver.
    for task_id, case, budget in [
        ("V3-01-REAL000-OPT", real000, 20.0),
        ("V3-02-REAL081-OPT", real081, 26.64),
    ]:
        direct = run_direct_b(direct_client, case, task_id)
        c_result = run_c(service, c_client, task_id=task_id, session_id=f"v3-{task_id.lower()}",
                         options=case["options"], budget=budget)
        oracle = case["result"]["oracle"]
        expected = set(oracle["selected_option_ids"])
        c_selected = set((c_result["summary"] or {}).get("selected_option_ids") or [])
        b_selected = set((direct["summary"] or {}).get("selected_option_ids") or [])
        oracle_ok = bool(c_result["outcome"].get("ok")) and c_result["outcome"].get("solver_status") == "OPTIMAL" and c_result["outcome"].get("verification_status") == "PASS_DEMO" and c_selected == expected and abs(float((c_result["summary"] or {}).get("objective_value") or 0) - float(oracle["objective_served_demand"])) <= float(oracle["rounding_error_upper_bound_persons"])
        parity = b_selected == expected and c_selected == b_selected
        trace_path = save_trace(task_id, {
            "task_id": task_id, "user_intent": next(x for x in gt if x["task_id"] == task_id)["turns"],
            "candidate_count": len(case["options"]), "budget_units": budget,
            "ground_truth_frozen_before_run": next(x for x in gt if x["task_id"] == task_id)["mathematical_or_reference"],
            "direct_b": {"wall_ms": direct["wall_ms"], "tool_calls": direct["tool_calls"], "result": direct["summary"]},
            "c_harness": {"wall_ms": c_result["wall_ms"], "outcome": c_result["outcome"], "result": c_result["summary"]},
            "checks": {"direct_b_matches_oracle": b_selected == expected, "c_harness_matches_oracle": oracle_ok, "entry_point_parity": parity},
        })
        rows[task_id].update(run_status="PASS" if oracle_ok and parity else "FAIL", task_success=bool(oracle_ok and parity),
                             solver_status=c_result["outcome"].get("solver_status"), verification_status=c_result["outcome"].get("verification_status"),
                             tool_calls=len(c_result["outcome"].get("tool_calls") or []), duration_ms=round(c_result["wall_ms"], 3), trace_path=trace_path,
                             note=f"paired direct B; parity={parity}; B direct wall={direct['wall_ms']:.1f}ms")
        task_outputs[task_id] = {"direct": direct, "c": c_result}

    # 03: synthetic planning graph cannot be injected through the current public task API.
    row = next(x for x in gt if x["task_id"] == "V3-03-SYN8-TRAP")
    trace_path = save_trace(row["task_id"], {"task_id": row["task_id"], "status": "NOT_RUN",
        "reason": "TaskRequest accepts scenario parameters and improvement options but no arbitrary synthetic demands/shelters/OD graph. Reusing a different live A graph would not be the V2 synthetic task.",
        "v2_oracle": row["mathematical_or_reference"]})
    rows[row["task_id"]].update(run_status="NOT_RUN", trace_path=trace_path, note="no supported public synthetic graph injection")

    # 04: B graph explanation must use a same-session result_id.
    t1 = task_outputs["V3-01-REAL000-OPT"]["c"]["outcome"]
    explain = service.submit("explain_result", session_id="v3-v3-01-real000-opt", result_id=t1["result_id"])
    explain_summary = explain.get("summary") or {}
    explain_detail = explain_summary.get("detail") or {}
    explain_metrics = explain_summary.get("metrics") or {}
    greedy_mip_explained = (
        isinstance(explain_detail.get("greedy"), dict)
        and isinstance(explain_detail.get("optimization"), dict)
        and explain_metrics.get("optimized_served_demand") is not None
    )
    explain_ok = bool(explain.get("ok")) and explain.get("verification_status") == "PASS_DEMO" and greedy_mip_explained
    trace_path = save_trace("V3-04-GREEDY-MIP-EXPLAIN", {
        "task_id": "V3-04-GREEDY-MIP-EXPLAIN", "result_id": t1["result_id"],
        "result_owner_session": "v3-v3-01-real000-opt", "outcome": explain,
        "reference": next(x for x in gt if x["task_id"] == "V3-04-GREEDY-MIP-EXPLAIN")["mathematical_or_reference"],
        "content_check": {"greedy_and_optimization_details_present": greedy_mip_explained,
                          "observed_detail_keys": sorted(explain_detail),
                          "observed_optimized_served_demand": explain_metrics.get("optimized_served_demand"),
                          "failure_reason": None if greedy_mip_explained else "B explain_result returned diagnosis only; no paired Greedy/MIP values or selection comparison."}})
    rows["V3-04-GREEDY-MIP-EXPLAIN"].update(run_status="PASS" if explain_ok else "FAIL", task_success=explain_ok,
        solver_status=explain.get("solver_status"), verification_status=explain.get("verification_status"),
        tool_calls=len(explain.get("tool_calls") or []), duration_ms=explain.get("duration_ms"), trace_path=trace_path,
        note=("B explain_result used owner-session result id; " +
              ("paired Greedy/MIP evidence present" if explain_ok else "B returned diagnosis only; Greedy/MIP difference not explained")))
    task_outputs["V3-04-GREEDY-MIP-EXPLAIN"] = explain

    # 05: clarification, user changes budget, then supplies options; check both turns.
    options_text = "；".join(f"{o['shelter_id']} 扩容{int(o['delta_capacity_persons'])}人，成本{o['cost_units']:g}" for o in real000["options"])
    turns05 = ["预算15，帮我做扩容优化。", f"预算20，并用方案：{options_text}，做扩容优化。"]
    t05 = run_conversation(service, config, "V3-05-MULTITURN-BUDGET", turns05)
    f05 = [x["final"] for x in t05]
    s05 = [x["session"] for x in t05]
    t05_result_id = f05[-1].get("result_id")
    t05_full = c_client.get_result(t05_result_id) if t05_result_id else None
    oracle0 = real000["result"]["oracle"]
    t05_selected_shelters = {row.get("shelter_id") for row in (((t05_full or {}).get("result_detail") or {}).get("optimization") or {}).get("selected_options", [])}
    oracle0_shelters = {row["shelter_id"] for row in real000["options"] if row["option_id"] in set(oracle0["selected_option_ids"])}
    t05_objective = float((((t05_full or {}).get("result_detail") or {}).get("optimization") or {}).get("objective_value") or 0)
    t05_objective_ok = abs(t05_objective - float(oracle0["objective_served_demand"])) <= float(oracle0["rounding_error_upper_bound_persons"])
    t05_ok = (
        len(t05) == 2
        and f05[0]["status"] == "NEEDS_CLARIFICATION"
        and "improvement_options" in (f05[0].get("clarification") or {}).get("fields", [])
        and t05[0]["c_harness_tool_call_count"] == 0
        and f05[-1]["status"] == "SUCCEEDED"
        and t05[1]["c_harness_tool_call_count"] > 0
        and (s05[-1].get("params") or {}).get("budget_units") == 20
        and t05_selected_shelters == oracle0_shelters
        and t05_objective_ok
    )
    trace_path = save_trace("V3-05-MULTITURN-BUDGET", {
        "task_id": "V3-05-MULTITURN-BUDGET",
        "turns": t05,
        "B_result": summarized_full(t05_full),
        "ground_truth": next(x for x in gt if x["task_id"] == "V3-05-MULTITURN-BUDGET")["mathematical_or_reference"],
        "check": {
            "turn1_asked_candidates": bool(f05 and f05[0]["status"] == "NEEDS_CLARIFICATION"),
            "turn1_no_B_tools": bool(t05 and t05[0]["c_harness_tool_call_count"] == 0),
            "turn2_budget20": bool(s05 and (s05[-1].get("params") or {}).get("budget_units") == 20),
            "final_selected_shelters_match_oracle": t05_selected_shelters == oracle0_shelters,
            "final_objective_within_oracle_error": t05_objective_ok,
        },
    })
    rows["V3-05-MULTITURN-BUDGET"].update(run_status="PASS" if t05_ok else "FAIL", task_success=t05_ok,
        solver_status=(t05_full or {}).get("solver_status"), verification_status=(t05_full or {}).get("verification_status"),
        tool_calls=sum(len((x["final"].get("timings") or {}).get("tool_calls") or []) for x in t05), duration_ms=sum((x["final"].get("timings") or {}).get("run_ms") or 0 for x in t05),
        trace_path=trace_path, note=f"statuses={[x['status'] for x in f05]}")
    task_outputs["V3-05-MULTITURN-BUDGET"] = t05

    # 06: RuleLLM through C public API with both numeric fields.
    t06 = run_conversation(service, config, "V3-06-BUDGET-WALK-BIND", ["评估现状，预算上限30，步行阈值15分钟。"])
    f06 = t06[-1]["final"]
    p06 = (t06[-1]["session"].get("params") or {})
    t06_ok = f06["status"] == "SUCCEEDED" and t06[0]["c_harness_tool_call_count"] > 0 and p06.get("budget_units") == 30 and p06.get("max_walk_time_min") == 15
    trace_path = save_trace("V3-06-BUDGET-WALK-BIND", {"task_id": "V3-06-BUDGET-WALK-BIND", "turns": t06,
        "ground_truth": next(x for x in gt if x["task_id"] == "V3-06-BUDGET-WALK-BIND")["mathematical_or_reference"],
        "malicious_cross_binding_regression": {"text": "预算上限30，步行阈值15分钟", "proposed_budget": 15, "expected_rejected": True}})
    rows["V3-06-BUDGET-WALK-BIND"].update(run_status="PASS" if t06_ok else "FAIL", task_success=t06_ok,
        solver_status=f06.get("metrics", {}).get("solver_status"), verification_status=f06.get("metrics", {}).get("verification_status"),
        tool_calls=None, duration_ms=(f06.get("timings") or {}).get("run_ms"), trace_path=trace_path,
        note=f"parsed budget={p06.get('budget_units')}; walk={p06.get('max_walk_time_min')}; RuleLLM calls={t06[-1]['llm_calls_total']}")
    task_outputs["V3-06-BUDGET-WALK-BIND"] = t06

    # 07: remove A from the option table and re-run at the proven budget.
    option_bc = [x for x in real000["options"] if x["option_id"] in {"V2_TRAP_B", "V2_TRAP_C"}]
    t07 = run_c(service, c_client, task_id="V3-07-REVISE-PLAN", session_id="v3-v3-07-revise", options=option_bc, budget=20)
    t07_ok = bool(t07["outcome"].get("ok")) and set((t07["summary"] or {}).get("selected_option_ids") or []) == set(oracle0["selected_option_ids"]) and abs(float((t07["summary"] or {}).get("objective_value") or 0)-oracle0["objective_served_demand"]) <= oracle0["rounding_error_upper_bound_persons"]
    trace_path = save_trace("V3-07-REVISE-PLAN", {"task_id": "V3-07-REVISE-PLAN", "changed_candidate_ids": ["V2_TRAP_B", "V2_TRAP_C"],
        "removed_candidate_ids": ["V2_TRAP_A"], "outcome": t07["outcome"], "result": t07["summary"], "oracle": oracle0})
    rows["V3-07-REVISE-PLAN"].update(run_status="PASS" if t07_ok else "FAIL", task_success=t07_ok,
        solver_status=t07["outcome"].get("solver_status"), verification_status=t07["outcome"].get("verification_status"),
        tool_calls=len((t07["outcome"].get("tool_calls") or [])), duration_ms=round(t07["wall_ms"], 3), trace_path=trace_path,
        note="new B request, candidate A omitted")
    task_outputs["V3-07-REVISE-PLAN"] = t07

    # 08: baseline versus disabling the V1 bottleneck shelter.
    closure_id = "zs_1b4df7920b"
    base08 = service.submit("assess", session_id="v3-v1-closure", changes={"max_walk_time_min": 15.0})
    closed08 = service.submit("assess", session_id="v3-v1-closure", changes={"disabled_shelter_ids": [closure_id], "max_walk_time_min": 15.0})
    baseline08 = (base08.get("summary") or {}).get("metrics", {}).get("served_demand")
    closed_val08 = (closed08.get("summary") or {}).get("metrics", {}).get("served_demand")
    closed_params08 = ((closed08.get("scenario") or {}).get("params") or {})
    t08_ok = bool(base08.get("ok")) and bool(closed08.get("ok")) and closed_params08.get("disabled_shelter_ids") == [closure_id] and closed_val08 < baseline08
    trace_path = save_trace("V3-08-DISABLE-BOTTLENECK", {"task_id": "V3-08-DISABLE-BOTTLENECK",
        "baseline": base08, "closure": closed08,
        "historical_v1_reference": next(x for x in gt if x["task_id"] == "V3-08-DISABLE-BOTTLENECK")["mathematical_or_reference"],
        "observed_loss": (baseline08-closed_val08) if baseline08 is not None and closed_val08 is not None else None})
    rows["V3-08-DISABLE-BOTTLENECK"].update(run_status="PASS" if t08_ok else "FAIL", task_success=t08_ok,
        solver_status=closed08.get("solver_status"), verification_status=closed08.get("verification_status"),
        tool_calls=len(base08.get("tool_calls") or [])+len(closed08.get("tool_calls") or []), duration_ms=(base08.get("duration_ms") or 0)+(closed08.get("duration_ms") or 0), trace_path=trace_path,
        note=f"live baseline={baseline08}; disabled={closed_val08}; loss={(baseline08-closed_val08) if baseline08 is not None and closed_val08 is not None else None}")
    task_outputs["V3-08-DISABLE-BOTTLENECK"] = {"baseline": base08, "closure": closed08}

    # 09: field-scoped history restore, checking each parameter snapshot.
    p0 = ScenarioParams(population_demand_ratio=0.2, max_walk_time_min=15.0, budget_units=None,
                        disabled_shelter_ids=(), data_version=config.data_version)
    restore_service = HarnessService(config, client=c_client, trace_dir=str(V3 / "traces" / "harness"))
    s09 = restore_service.session_object("v3-restore")
    turns09 = []
    for value, text in [(20.0, "预算20"), (25.0, "预算25")]:
        normalized = apply_ops(s09.current.params, [ParamOp("budget_units", "set", value)], text=text)
        state = s09.plan_turn(normalized.changes)
        s09.commit(state)
        turns09.append({"budget_units": state.params.budget_units, "changes": state.changes})
    restored = apply_ops(s09.current.params, [ParamOp("budget_units", "restore", "previous")],
                         history=s09.history, defaults=p0, text="撤销上一步预算修改")
    turns09.append({"budget_units": restored.params.budget_units, "changes": restored.changes})
    t09_ok = [x["budget_units"] for x in turns09] == [20.0, 25.0, 20.0] and set(restored.changes) <= {"budget_units"}
    trace_path = save_trace("V3-09-RESTORE-BUDGET", {"task_id": "V3-09-RESTORE-BUDGET", "parameter_turns": turns09,
        "restore_result": {"budget_units": restored.params.budget_units, "changes": restored.changes, "restored_from": restored.restored_from},
        "b_api_calls": 0})
    rows["V3-09-RESTORE-BUDGET"].update(run_status="PASS" if t09_ok else "FAIL", task_success=t09_ok, tool_calls=0,
        trace_path=trace_path, note=f"budget states={[x['budget_units'] for x in turns09]}")

    # 10/11: RuleLLM asks instead of inventing budget or candidates.
    for task_id, text, expected_field in [
        ("V3-10-ASK-MISSING-BUDGET", "帮我做个扩容优化。", "budget_units"),
        ("V3-11-ASK-MISSING-CANDIDATES", "预算20，帮我做扩容优化。", "improvement_options"),
    ]:
        observations = run_conversation(service, config, task_id, [text])
        final = observations[-1]["final"]
        fields = (final.get("clarification") or {}).get("fields") or []
        ok = final["status"] == "NEEDS_CLARIFICATION" and observations[0]["c_harness_tool_call_count"] == 0 and expected_field in fields and final.get("result_id") is None
        trace_path = save_trace(task_id, {"task_id": task_id, "turns": observations,
                                         "expected_clarification_field": expected_field, "b_api_calls_expected": 0})
        rows[task_id].update(run_status="PASS" if ok else "FAIL", task_success=ok, tool_calls=0,
            duration_ms=(final.get("timings") or {}).get("run_ms"), trace_path=trace_path, note=f"clarification_fields={fields}")
        task_outputs[task_id] = observations

    # 12: check the actual V3-01 result's scope and claims.
    full12 = task_outputs["V3-01-REAL000-OPT"]["c"]["full"]
    c_summary12 = task_outputs["V3-01-REAL000-OPT"]["c"]["outcome"].get("summary") or {}
    t12_ok = bool(full12 and full12.get("data_status") == "DEMO_ONLY" and c_summary12.get("disclosure", {}).get("demo_only") is True and c_summary12.get("disclosure", {}).get("verified_for_real_planning") is False)
    try:
        guards.assert_demo_disclosure(full12 or {}, "该方案已现场核验，可直接执行")
        bad_claim_rejected = False
    except GuardClaimViolation:
        bad_claim_rejected = True
    t12_ok = t12_ok and bad_claim_rejected
    trace_path = save_trace("V3-12-DEMO-ONLY-DISCLOSURE", {"task_id": "V3-12-DEMO-ONLY-DISCLOSURE",
        "result": summarized_full(full12), "c_harness_summary_disclosure": c_summary12.get("disclosure"),
        "unsupported_field_verified_claim_rejected": bad_claim_rejected})
    rows["V3-12-DEMO-ONLY-DISCLOSURE"].update(run_status="PASS" if t12_ok else "FAIL", task_success=t12_ok,
        solver_status=(full12 or {}).get("solver_status"), verification_status=(full12 or {}).get("verification_status"),
        trace_path=trace_path, note="real result status plus injected forbidden claim check")

    # 13: deterministic FEASIBLE formatter and postflight claim gate.
    feasible_full = copy.deepcopy(full12)
    feasible_full["solver_status"] = "FEASIBLE"
    feasible_full["optimality_proven"] = False
    feasible_full.setdefault("result_detail", {}).setdefault("optimization", {})["optimality_proven"] = False
    feasible_full["verification_status"] = "PASS_DEMO"
    session13 = service.session_object("v3-v3-01-real000-opt")
    state13 = session13.current
    accepted13 = guards.postflight(full_result=feasible_full, state=state13, config=config)
    try:
        guards.postflight(full_result=feasible_full, state=state13, config=config, claim="optimal")
        claim_rejected13 = False
    except GuardClaimViolation:
        claim_rejected13 = True
    wording13 = _solver_sentence("FEASIBLE", False)
    t13_ok = accepted13["solver_status"] == "FEASIBLE" and not accepted13["optimality_proven"] and claim_rejected13 and "FEASIBLE" in wording13 and "未证明最优" in wording13 and "不得表述为" in wording13
    trace_path = save_trace("V3-13-FEASIBLE-WORDING", {"task_id": "V3-13-FEASIBLE-WORDING",
        "source_reference": str(sources["d03"].relative_to(ROOT)), "postflight": accepted13,
        "optimal_claim_rejected": claim_rejected13, "deterministic_wording": wording13,
        "injection_note": "status only; not a live B solver timeout"})
    rows["V3-13-FEASIBLE-WORDING"].update(run_status="PASS" if t13_ok else "FAIL", task_success=t13_ok,
        solver_status="FEASIBLE", verification_status="PASS_DEMO", trace_path=trace_path,
        note="formatter/claim gate injection; no public deterministic FEASIBLE solver run")

    # 14: tampered result refused by C gate, then clean same-scenario retry succeeds.
    owner_session = "v3-v3-01-real000-opt"
    valid_t1 = task_outputs["V3-01-REAL000-OPT"]["c"]["outcome"]
    original_full14 = c_client.get_result(valid_t1["result_id"])
    corrupt14 = copy.deepcopy(original_full14)
    corrupt14["verification_status"] = "FAIL"
    try:
        guards.postflight(full_result=corrupt14, state=service.session_object(owner_session).current, config=config)
        rejected14 = False
        reject_error14 = None
    except ResultMismatch as exc:
        rejected14 = True
        reject_error14 = exc.to_dict()
    recovery14 = service.submit("optimize", session_id=owner_session,
                                improvement_options=real000["options"])
    recovery_opt14 = (((recovery14.get("summary") or {}).get("detail") or {}).get("optimization") or {})
    recovery_math_ok14 = (
        set(recovery_opt14.get("selected_option_ids") or []) == set(oracle0["selected_option_ids"])
        and abs(float(recovery_opt14.get("served_demand") or 0) - float(oracle0["objective_served_demand"]))
        <= float(oracle0["rounding_error_upper_bound_persons"])
    )
    recovery_ok14 = bool(recovery14.get("ok")) and recovery14.get("verification_status") == "PASS_DEMO" and recovery14.get("solver_status") == "OPTIMAL" and recovery_math_ok14
    t14_ok = rejected14 and recovery_ok14
    trace_path = save_trace("V3-14-VERIFIER-RECOVERY", {"task_id": "V3-14-VERIFIER-RECOVERY",
        "injected_result_id": original_full14["result_id"], "corrupt_status": "FAIL",
        "refusal": reject_error14, "recovery_outcome": recovery14, "recovery_math_matches_oracle": recovery_math_ok14,
        "parameters_after_recovery": service.session_state(owner_session)["current"]["params"]})
    rows["V3-14-VERIFIER-RECOVERY"].update(run_status="PASS" if t14_ok else "FAIL", task_success=t14_ok,
        solver_status=recovery14.get("solver_status"), verification_status=recovery14.get("verification_status"),
        tool_calls=len(recovery14.get("tool_calls") or []), duration_ms=recovery14.get("duration_ms"), trace_path=trace_path,
        note=f"corrupt_result_rejected={rejected14}; retry_ok={recovery_ok14}")

    # 15: membership guard rejects before calling the B result reader.
    class SpyClient:
        calls: list[str] = []
        def get_result(self, result_id: str):
            self.calls.append(result_id)
            return {"result_id": result_id, "result_detail": {"optimization": {}}}
    spy = SpyClient()
    sec_service = HarnessService(config, client=spy, trace_dir=str(V3 / "traces" / "security"))
    sec_service.session_object("owner").remember_result("owned-result", scenario_id="s1",
        data_version=config.data_version, summary={"result_id": "owned-result"})
    owner_allowed = sec_service.result("owned-result", "owner")["result_id"] == "owned-result"
    try:
        sec_service.result("owned-result", "attacker")
        attacker_refused = False
    except GuardUnknownResult as exc:
        attacker_refused = True
        sec_error = exc.to_dict()
    t15_ok = owner_allowed and attacker_refused and spy.calls == ["owned-result"]
    trace_path = save_trace("V3-15-CROSS-SESSION-RESULT", {"task_id": "V3-15-CROSS-SESSION-RESULT",
        "owner_allowed": owner_allowed, "attacker_refused": attacker_refused,
        "B_get_result_calls_total_including_owner": spy.calls,
        "attacker_B_get_result_calls": len(spy.calls) - 1 if owner_allowed else len(spy.calls),
        "error": sec_error if attacker_refused else None})
    rows["V3-15-CROSS-SESSION-RESULT"].update(run_status="PASS" if t15_ok else "FAIL",
        task_success=t15_ok, tool_calls=0, trace_path=trace_path,
        note=f"denied upstream access; spy_calls={len(spy.calls)}")

    # 16: C guard and B schema boundary; never send 13 candidates to B.
    def option_clone(index: int) -> dict[str, Any]:
        row = copy.deepcopy(real081["options"][index % len(real081["options"])])
        row["option_id"] = f"V3_LIMIT_{index:02d}"
        return row
    options12 = [option_clone(i) for i in range(12)]
    accepted12 = guards.validate_improvement_options(options12)
    request12 = TaskRequest(task_type="optimize", improvement_options=options12)
    try:
        guards.validate_improvement_options(options12 + [option_clone(12)])
        c_rejected13 = False
        c_error13 = None
    except GuardInvalidOption as exc:
        c_rejected13 = True
        c_error13 = exc.to_dict()
    try:
        TaskRequest(task_type="optimize", improvement_options=options12 + [option_clone(12)])
        b_rejected13 = False
    except Exception as exc:
        b_rejected13 = True
        b_error13 = f"{type(exc).__name__}: {exc}"
    t16_ok = len(accepted12) == 12 and len(request12.improvement_options) == 12 and c_rejected13 and b_rejected13
    trace_path = save_trace("V3-16-CANDIDATE-LIMIT", {"task_id": "V3-16-CANDIDATE-LIMIT",
        "c_12_accepted": len(accepted12), "b_schema_12_accepted": len(request12.improvement_options),
        "c_13_rejected": c_rejected13, "c_error": c_error13,
        "b_schema_13_rejected": b_rejected13, "b_error": b_error13,
        "calls_to_b_for_13": 0})
    rows["V3-16-CANDIDATE-LIMIT"].update(run_status="PASS" if t16_ok else "FAIL",
        task_success=t16_ok, tool_calls=0, trace_path=trace_path,
        note="12 accepted at validation boundary, 13 rejected before solver; no limit widening")

    # 17: no real/paid LLM baseline is configured; RuleLLM is not counted as this arm.
    trace_path = save_trace("V3-17-ORDINARY-AGENT-BASELINE", {"task_id": "V3-17-ORDINARY-AGENT-BASELINE",
        "status": "NOT_RUN", "reason": "No real model configured and no paid API authorized; RuleLLM is a deterministic adapter, not an ordinary tool agent."})
    rows["V3-17-ORDINARY-AGENT-BASELINE"].update(run_status="NOT_RUN", trace_path=trace_path,
        note="real LLM unavailable; no paid API called")

    # 18: repeated same C task exposes actual B graph cache rows and must preserve result.
    cache_session = "v3-skill-cache"
    first18 = run_c(service, c_client, task_id="V3-18-SKILL-CACHE-REPEAT-1", session_id=cache_session,
                    options=real000["options"], budget=20)
    second18 = run_c(service, c_client, task_id="V3-18-SKILL-CACHE-REPEAT-2", session_id=cache_session,
                     options=real000["options"], budget=20, changes={}, clear_cache=False)
    first_cache = [r for r in ((first18["full"] or {}).get("trace") or []) if r.get("cache_hit")]
    second_cache = [r for r in ((second18["full"] or {}).get("trace") or []) if r.get("cache_hit")]
    second_selected = set((second18["summary"] or {}).get("selected_option_ids") or [])
    second_objective = float((second18["summary"] or {}).get("objective_value") or 0)
    t18_math_ok = second_selected == set(oracle0["selected_option_ids"]) and abs(second_objective - float(oracle0["objective_served_demand"])) <= float(oracle0["rounding_error_upper_bound_persons"])
    t18_ok = first18["outcome"].get("ok") and second18["outcome"].get("ok") and second18["outcome"].get("solver_status") == "OPTIMAL" and second18["outcome"].get("verification_status") == "PASS_DEMO" and t18_math_ok and bool(second_cache)
    trace_path = save_trace("V3-18-SKILL-CACHE-REPEAT", {"task_id": "V3-18-SKILL-CACHE-REPEAT",
        "first": {"wall_ms": first18["wall_ms"], "result": first18["summary"]},
        "second": {"wall_ms": second18["wall_ms"], "result": second18["summary"]},
        "cache_hit_rows_first": first_cache, "cache_hit_rows_second": second_cache,
        "cache_saved_wall_ms": first18["wall_ms"]-second18["wall_ms"],
        "same_result_semantics": t18_math_ok,
        "causality_limit": "Repeat run measures cache use in B traces; it is not a Skill-Graph-off ablation."})
    rows["V3-18-SKILL-CACHE-REPEAT"].update(run_status="PASS" if t18_ok else "FAIL",
        task_success=t18_ok, solver_status=second18["outcome"].get("solver_status"),
        verification_status=second18["outcome"].get("verification_status"),
        tool_calls=len(second18["outcome"].get("tool_calls") or []), duration_ms=round(second18["wall_ms"], 3),
        trace_path=trace_path, note=f"first cache hits={len(first_cache)}; second cache hits={len(second_cache)}; wall delta={first18['wall_ms']-second18['wall_ms']:.1f}ms")

    c_client.close()
    direct_client.close()
    service.close()
    # Freeze outcome manifest only after the independent GT was written at the start.
    manifest_path = V3 / "TASK_MANIFEST.csv"
    fields = ["task_id", "family", "turns", "run_status", "task_success", "solver_status", "verification_status", "tool_calls", "duration_ms", "trace_path", "gt_source", "note"]
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows[task_id] for task_id in [x["task_id"] for x in gt])
    write_json(V3 / "tasks" / "run_summary.json", {"run_id": RUN_ID, "tasks": rows, "source_paths": {k: str(v.relative_to(ROOT)) for k, v in sources.items()}})
    print(json.dumps({k: {"status": v["run_status"], "success": v["task_success"], "trace": v["trace_path"], "note": v["note"]} for k, v in rows.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
