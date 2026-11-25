"""Stage 3 Pilot task factory, execution harness, GT and VETS evaluator.

This module consumes the frozen Stage 2.1 Pilot.  It does not generate new
scenarios and it never treats a label in the frozen JSON as an execution
result.  B is executed either through the local HTTP application (for the
configured Shiqi route) or through an explicit A-adapter/source override for
East District.  The latter is an actual B Solver call, not a mock, while the
missing HTTP route is reported as UNSUPPORTED.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from backend.adapters.a_reader import load_a_bundle
from backend.app import app
from backend.engine import solve_assignment
from backend.errors import PlanningError
from backend.graph.executor import execute_task
from backend.models import ImprovementOption, PlanningInput, PlanningScenario, TaskRequest
from backend.verifier.checks import verify_assignment
from fastapi.testclient import TestClient
from sources.preflight import ROOT


PILOT_PATH = ROOT / "benchmark/shelterplan_eval/pilot_stage2_1/accepted_scenarios.json"
GENERATOR_VERSION = "stage3-pilot-1.0"
SCHEMA_VERSION = 1
EXECUTION_STATUSES = {"PASS", "FAIL", "NOT_RUN", "INFRA_ERROR", "UNSCORABLE"}
TOLERANCE = 1e-6

DEFAULT_CASE_IDS = (
    "ST21-shiqi-000",          # ordinary assessment
    "ST21-east_district-001",  # budget competition
    "ST21-shiqi-002",          # capacity competition
    "ST21-east_district-003",  # facility disruption
    "ST21-shiqi-004",          # real two-round inheritance
    "ST21-shiqi-016",          # invalid shelter id
    "ST21-east_district-029",  # missing parameter / clarify
    "ST21-east_district-011",  # safe-stop evidence probe
)


@dataclass(frozen=True)
class LoadedSource:
    region_id: str
    data: PlanningInput
    base_scenario: PlanningScenario
    adapter_path: str


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _compact_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [copy.deepcopy(option) for option in options]


def load_pilot(path: Path = PILOT_PATH) -> list[dict[str, Any]]:
    rows = read_json(path)
    if not isinstance(rows, list) or len(rows) != 24:
        raise ValueError(f"Expected the frozen 24-case Pilot, found {len(rows) if isinstance(rows, list) else 'non-list'}")
    return rows


def configured_http_route(root: Path = ROOT) -> dict[str, Any]:
    """Return the checked-in B route identity without selecting a fallback."""
    path = root / "configs/module_b.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    module_b = document.get("module_b") or {}
    source_config = str(module_b.get("source_config") or "")
    return {
        "configured_region_id": module_b.get("region_id"),
        "source_config": source_config,
        "configured_data_version": (root / source_config).exists() and (
            (yaml.safe_load((root / source_config).read_text(encoding="utf-8")) or {}).get("project") or {}
        ).get("data_version"),
    }


def load_real_source(region_id: str, root: Path = ROOT) -> LoadedSource:
    """Load a region only through its explicit A v2 adapter."""
    config_path = (root / "configs/module_a_v2_b" / f"{region_id}.yaml").resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"No explicit A v2 adapter for region_id={region_id}: {config_path}")
    bundle = load_a_bundle(config_path, root)
    data = PlanningInput(
        region_id=bundle.region_id,
        data_version=bundle.manifest.data_version,
        data_status=bundle.availability.data_status,
        demands=bundle.demands,
        shelters=bundle.shelters,
        od_edges=bundle.od_edges,
        source_manifest_hash=bundle.manifest.source_manifest_hash,
        source_label=f"module_a:{bundle.manifest.data_version}",
    )
    if data.region_id != region_id:
        raise ValueError(f"Adapter identity mismatch: expected {region_id}, found {data.region_id}")
    return LoadedSource(
        region_id=region_id,
        data=data,
        base_scenario=PlanningScenario(
            scenario_id=f"stage3-base-{region_id}",
            region_id=region_id,
            data_version=data.data_version,
            analysis_mode="demo",
            population_demand_ratio=0.2,
            max_walk_time_min=15.0,
        ),
        adapter_path=str(config_path.relative_to(root).as_posix()),
    )


def _scenario_doc(scenario: dict[str, Any], *, scenario_id: str | None = None, budget: float | None = None, max_walk: float | None = None, parent_id: str | None = None) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id or scenario["scenario_id"],
        "parent_scenario_id": parent_id,
        "region_id": scenario["region_id"],
        "data_version": scenario["data_version"],
        "dataset_content_hash": scenario["dataset_content_hash"],
        "source_manifest_sha256": scenario["source_manifest_sha256"],
        "analysis_mode": "demo",
        "population_demand_ratio": scenario["population_demand_ratio"],
        "max_walk_time_min": scenario["max_walk_time_min"] if max_walk is None else max_walk,
        "budget_units": scenario["budget_units"] if budget is None else budget,
        "disabled_shelter_ids": list(scenario["disabled_shelter_ids"]),
        "changes": {
            "dataset_content_hash": scenario["dataset_content_hash"],
            "scenario_family": scenario["scenario_family"],
        },
    }


def _base_b_payload(scenario: dict[str, Any], task_type: str, *, options: list[dict[str, Any]] | None = None, scenario_id: str | None = None, budget: float | None = None, max_walk: float | None = None, parent_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": f"stage3-{scenario['scenario_id']}-{uuid.uuid4().hex[:8]}",
        "scenario_id": scenario_id or scenario["scenario_id"],
        "region_id": scenario["region_id"],
        "data_version": scenario["data_version"],
        "analysis_mode": "demo",
        "task_type": task_type,
        "scenario": _scenario_doc(scenario, scenario_id=scenario_id, budget=budget, max_walk=max_walk, parent_id=parent_id),
        "parameters": {},
    }
    if task_type in {"optimize", "diagnose_intervene", "stress_test", "sensitivity"}:
        payload["improvement_options"] = _compact_options(options if options is not None else scenario["improvement_options"])
        if task_type == "optimize":
            effective_budget = scenario["budget_units"] if budget is None else budget
            if effective_budget is not None:
                payload["parameters"] = {"budget_units": effective_budget}
    return payload


def _task_type_for(scenario: dict[str, Any]) -> str:
    if scenario["interaction_type"] == "safe_stop":
        return "assess"
    if scenario["interaction_type"] == "complete_request" and scenario["scenario_family"] in {"coverage_baseline", "facility_disruption"}:
        return "assess"
    if scenario["interaction_type"] == "missing_parameter":
        return "optimize"
    return "optimize"


def _stage_options(scenario: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    ids = set((scenario.get("recovery_stages") or {}).get(stage) or [])
    return [option for option in scenario["improvement_options"] if option["option_id"] in ids]


def build_task(scenario: dict[str, Any], *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert one frozen Scenario into a B-compatible task and GT contract."""
    interaction = scenario["interaction_type"]
    expected = scenario["expected_behavior"]
    route = route or configured_http_route()
    task_type = _task_type_for(scenario)
    contract_repairs: list[str] = []
    task: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "task_id": f"S3-{scenario['scenario_id']}",
        "scenario_id": scenario["scenario_id"],
        "region_id": scenario["region_id"],
        "data_version": scenario["data_version"],
        "scenario_family": scenario["scenario_family"],
        "design_difficulty": scenario.get("design_difficulty"),
        "design_difficulty_evidence": scenario.get("difficulty_evidence"),
        "interaction_type": interaction,
        "expected_behavior": expected,
        "execution_route": "configured_http" if route.get("configured_region_id") == scenario["region_id"] else "explicit_adapter_direct_solver",
        "http_route_status": "SUPPORTED" if route.get("configured_region_id") == scenario["region_id"] else "UNSUPPORTED",
        "http_route_identity": route,
        "source_adapter": f"configs/module_a_v2_b/{scenario['region_id']}.yaml",
        "contract_repairs": [],
        "agent_input": {
            "region_id": scenario["region_id"],
            "data_version": scenario["data_version"],
            "dataset_content_hash": scenario["dataset_content_hash"],
            "source_manifest_sha256": scenario["source_manifest_sha256"],
            "scenario_family": scenario["scenario_family"],
            "request": scenario.get("request_payload", {}),
        },
        "gt_assertions": {
            "expected_behavior": expected,
            "region_id": scenario["region_id"],
            "data_version": scenario["data_version"],
            "analysis_mode": "demo",
            "budget_units": scenario["budget_units"],
            "allowed_option_ids": [option["option_id"] for option in scenario["improvement_options"]],
            "disabled_shelter_ids": list(scenario["disabled_shelter_ids"]),
            "parent_state_required": interaction == "parameter_edit",
            "independent_oracle_required": expected == "EXECUTE" and task_type == "optimize",
            "safe_stop_capacity_evidence_required": expected == "SAFE_STOP",
        },
    }

    if interaction == "parameter_edit":
        # The old Stage 2 artifact used ST21-parent-* as a placeholder.  It is
        # deliberately not copied into a B request.  The runner inserts the
        # actual scenario_id returned by the parent execution.
        stage_one = _stage_options(scenario, "stage_1") or scenario["improvement_options"][:2]
        stage_two = _stage_options(scenario, "stage_2") or scenario["improvement_options"][2:]
        parent_budget = sum(float(option["cost_units"]) for option in stage_one)
        contract_repairs.append("virtual_parent_id_replaced_by_runtime_parent_scenario_id")
        task["rounds"] = [
            {"round": 1, "role": "parent", "payload": _base_b_payload(scenario, "optimize", options=stage_one, scenario_id=f"{scenario['scenario_id']}-parent", budget=parent_budget)},
            {"round": 2, "role": "child", "payload_template": _base_b_payload(scenario, "optimize", options=stage_two, scenario_id=f"{scenario['scenario_id']}-child", budget=scenario["budget_units"])},
        ]
        task["task_type"] = "multi_round"
    else:
        payload = _base_b_payload(scenario, task_type)
        if interaction == "invalid_id":
            payload["improvement_options"][0]["shelter_id"] = f"invalid-{scenario['scenario_id']}"
            contract_repairs.append("invalid_shelter_id_is_sent_to_B")
        elif interaction == "illegal_constraint":
            payload["scenario"]["max_walk_time_min"] = -5
            contract_repairs.append("negative_walk_threshold_is_sent_as_raw_HTTP_payload")
        elif interaction == "illegal_budget":
            payload["parameters"] = {"budget_units": -1}
            contract_repairs.append("negative_budget_is_sent_to_B")
        elif interaction == "data_version_mismatch":
            payload["data_version"] = "shelterplan-historical-v2"
            payload["scenario"]["data_version"] = "shelterplan-historical-v2"
            contract_repairs.append("historical_data_version_is_sent_to_B")
        elif interaction == "missing_parameter":
            payload["scenario"]["budget_units"] = None
            payload.pop("parameters", None)
            contract_repairs.append("missing_budget_is_sent_to_optimize")
        elif interaction == "safe_stop":
            # B assesses the actual state.  SAFE_STOP is an Agent decision and
            # is not inserted into the B request as a fake result.
            contract_repairs.append("safe_stop_is_evaluated_from_capacity_evidence")
        task["task_type"] = task_type
        task["b_request_payload"] = payload
    task["contract_repairs"] = contract_repairs
    return task


def build_tasks(scenarios: list[dict[str, Any]] | None = None, *, case_ids: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    scenarios = scenarios or load_pilot()
    selected = set(case_ids or DEFAULT_CASE_IDS)
    rows = [build_task(row) for row in scenarios if row["scenario_id"] in selected]
    if {row["scenario_id"] for row in rows} != selected:
        missing = sorted(selected - {row["scenario_id"] for row in rows})
        raise KeyError(f"Pilot case IDs not found: {missing}")
    return rows


def _as_source_options(options: list[dict[str, Any]]) -> list[ImprovementOption]:
    return [ImprovementOption(**option) for option in options]


def _request(payload: dict[str, Any]) -> TaskRequest:
    return TaskRequest(**payload)


def _certificate_summary(certificate: Any) -> Any:
    if not isinstance(certificate, dict):
        return certificate
    checks = certificate.get("checks")
    if not isinstance(checks, list):
        return certificate
    raw = json.dumps(certificate, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    failed = [row for row in checks if isinstance(row, dict) and not row.get("passed")]
    return {
        "verification_status": certificate.get("verification_status"),
        "verified_for_demo": certificate.get("verified_for_demo"),
        "verified_for_real_planning": certificate.get("verified_for_real_planning"),
        "check_count": len(checks),
        "passed_check_count": len(checks) - len(failed),
        "failed_checks": [
            {"check_id": row.get("check_id"), "object_id": row.get("object_id"), "evidence_ref": row.get("evidence_ref"), "message": row.get("message")}
            for row in failed[:20]
        ],
        "certificate_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _compact_output(output: dict[str, Any]) -> dict[str, Any]:
    detail = output.get("result_detail") or {}
    optimization = detail.get("optimization") or {}
    return {
        "status": "SUCCEEDED",
        "task_id": output.get("task_id"),
        "job_id": output.get("job_id"),
        "result_id": output.get("result_id"),
        "scenario_id": output.get("scenario_id"),
        "parent_scenario_id": output.get("parent_scenario_id"),
        "region_id": output.get("region_id"),
        "data_version": output.get("data_version"),
        "data_status": output.get("data_status"),
        "execution_trace_id": output.get("execution_trace_id"),
        "solver_status": output.get("solver_status"),
        "verification_status": output.get("verification_status"),
        "result_summary": output.get("result_summary"),
        "selected_option_ids": optimization.get("selected_option_ids", []),
        "total_cost_units": optimization.get("total_cost_units"),
        "greedy_comparison": optimization.get("greedy_comparison"),
        "optimization_certificate": _certificate_summary(output.get("optimization_certificate")),
        "certificate": _certificate_summary(output.get("certificate")),
        "trace": output.get("trace", []),
        "elapsed_ms": output.get("elapsed_ms"),
        "provenance": output.get("provenance"),
    }


def _compact_failure(*, error: Any = None, trace_id: str | None = None, http_status: int | None = None, response: Any = None, mode: str) -> dict[str, Any]:
    return {
        "status": "FAILED",
        "execution_mode": mode,
        "execution_trace_id": trace_id,
        "http_status": http_status,
        "error": error,
        "response": response,
    }


def execute_http(payload: dict[str, Any], *, poll_seconds: float = 180.0) -> dict[str, Any]:
    """Exercise the real FastAPI route and background B execution in-process."""
    started = time.perf_counter()
    with TestClient(app) as client:
        response = client.post("/planning/tasks", json=payload)
        if response.status_code != 202:
            return _compact_failure(mode="HTTP_TESTCLIENT", http_status=response.status_code, response=response.json())
        accepted = response.json()
        job_id = accepted["job_id"]
        deadline = time.perf_counter() + poll_seconds
        job = None
        while time.perf_counter() < deadline:
            job = client.get(f"/planning/jobs/{job_id}").json()
            if job.get("status") in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(0.05)
        if job and job.get("status") == "FAILED":
            return _compact_failure(mode="HTTP_TESTCLIENT", trace_id=job.get("execution_trace_id"), response=job, error=job.get("error"), http_status=202)
        if not job or job.get("status") != "SUCCEEDED":
            return _compact_failure(mode="HTTP_TESTCLIENT", trace_id=(job or accepted).get("execution_trace_id"), response=job, error={"error_code": "POLL_TIMEOUT"}, http_status=202)
        result = client.get(f"/planning/results/{job['result_id']}")
        if result.status_code != 200:
            return _compact_failure(mode="HTTP_TESTCLIENT", trace_id=job.get("execution_trace_id"), response=result.json(), http_status=result.status_code)
        output = _compact_output(result.json())
        output["execution_mode"] = "HTTP_TESTCLIENT"
        output["http_status"] = 202
        output["elapsed_harness_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return output


def execute_direct(payload: dict[str, Any], source: LoadedSource) -> dict[str, Any]:
    """Call the real B graph with an explicit, region-matched A source."""
    started = time.perf_counter()
    try:
        request = _request(payload)
        output = execute_task(request, source_override=(source.data, PlanningScenario(**payload["scenario"]), []))
        compact = _compact_output(output)
        compact["execution_mode"] = "DIRECT_B_SOLVER_EXPLICIT_A_ADAPTER"
        compact["elapsed_harness_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return compact
    except PlanningError as exc:
        return _compact_failure(mode="DIRECT_B_SOLVER_EXPLICIT_A_ADAPTER", error={"error_code": exc.error_code, "message": str(exc), "details": exc.details})
    except Exception as exc:  # unexpected runner/infra error, never a PASS
        return _compact_failure(mode="DIRECT_B_SOLVER_EXPLICIT_A_ADAPTER", error={"error_code": "INFRA_ERROR", "message": repr(exc)})


def _option_constraints(options: list[ImprovementOption], selected: tuple[ImprovementOption, ...], scenario: PlanningScenario) -> bool:
    if any(not option.eligible or option.shelter_id in scenario.disabled_shelter_ids for option in selected):
        return False
    if sum(option.cost_units for option in selected) > float(scenario.budget_units or 0) + TOLERANCE:
        return False
    by_shelter: dict[str, list[ImprovementOption]] = {}
    by_group: dict[str, int] = {}
    for option in selected:
        by_shelter.setdefault(option.shelter_id, []).append(option)
        if option.combination_policy == "exclusive_group":
            by_group[option.exclusive_group or ""] = by_group.get(option.exclusive_group or "", 0) + 1
    if any(len(rows) > 1 and any(row.combination_policy != "stackable" for row in rows) for rows in by_shelter.values()):
        return False
    return all(count <= 1 for count in by_group.values())


def enumerate_budget_oracle(data: PlanningInput, scenario: PlanningScenario, options: list[ImprovementOption]) -> dict[str, Any]:
    """Independently enumerate all legal option subsets (<=12 options)."""
    if len(options) > 12:
        return {"status": "NOT_AVAILABLE", "method": "EXHAUSTIVE_OPTION_SUBSET_ENUMERATION", "reason": "option_count_exceeds_12"}
    if scenario.budget_units is None:
        return {"status": "NOT_AVAILABLE", "method": "EXHAUSTIVE_OPTION_SUBSET_ENUMERATION", "reason": "budget_missing"}
    best: tuple[float, float, float, int, tuple[str, ...]] | None = None
    best_key: tuple[float, float, float, int, tuple[str, ...]] | None = None
    feasible_count = 0
    for count in range(len(options) + 1):
        for selected in itertools.combinations(options, count):
            if not _option_constraints(options, selected, scenario):
                continue
            deltas: dict[str, float] = {}
            for option in selected:
                deltas[option.shelter_id] = deltas.get(option.shelter_id, 0.0) + option.delta_capacity_persons
            result = solve_assignment(data, scenario, deltas)
            certificate = verify_assignment(data, scenario, result, deltas)
            if certificate.verification_status == "FAIL":
                continue
            feasible_count += 1
            served = float(result.served_demand)
            weighted = float(result.weighted_walk_time_min) if result.weighted_walk_time_min is not None else float("inf")
            cost = float(sum(option.cost_units for option in selected))
            option_ids = tuple(option.option_id for option in selected)
            key = (served, -weighted, -cost, -count, tuple(reversed(option_ids)))
            if best_key is None or key > best_key:
                best = (served, weighted, cost, count, option_ids)
                best_key = key
    if best is None:
        return {"status": "NOT_AVAILABLE", "method": "EXHAUSTIVE_OPTION_SUBSET_ENUMERATION", "reason": "no_feasible_subset"}
    return {
        "status": "ORACLE_PROVEN",
        "method": "EXHAUSTIVE_OPTION_SUBSET_ENUMERATION",
        "feasible_subset_count": feasible_count,
        "objective": {"served_demand": best[0], "weighted_walk_time_min": None if best[1] == float("inf") else best[1], "cost_units": best[2], "option_count": best[3]},
        "selected_option_ids": list(best[4]),
        "proof_scope": "all legal subsets of the supplied <=12-option set; assignment feasibility independently recomputed",
    }


def safe_stop_capacity_evidence(data: PlanningInput, scenario: PlanningScenario, execution: dict[str, Any]) -> dict[str, Any]:
    """Compute capacity-constrained evidence; no spatial-only shortcut."""
    summary = execution.get("result_summary") or {}
    total = float(summary.get("total_demand") or 0.0)
    served = float(summary.get("served_demand") or 0.0)
    active = [row for row in data.shelters if row.active and row.shelter_id not in scenario.disabled_shelter_ids]
    total_capacity = float(sum(row.capacity_persons for row in active))
    deltas = {row.shelter_id: max(0.0, total - float(row.capacity_persons)) for row in active}
    upper = solve_assignment(data, scenario, deltas)
    residual_upper = max(0.0, total - upper.served_demand)
    return {
        "baseline_served_demand": served,
        "baseline_unserved_demand": max(0.0, total - served),
        "total_demand": total,
        "active_capacity_persons": total_capacity,
        "capacity_constrained_baseline": served < total - TOLERANCE,
        "capacity_unbounded_reachability_upper_bound": upper.served_demand,
        "residual_unserved_at_upper_bound": residual_upper,
        "safe_stop_supported_by_capacity_and_reachability": upper.served_demand <= served + TOLERANCE,
        "upper_bound_solver_status": upper.solver_status,
        "evidence_basis": "B solve_assignment with current capacities and explicit capacity-unbounded counterfactual over the same A v2 OD graph",
    }


def _evaluate_output(task: dict[str, Any], execution: dict[str, Any], *, oracle: dict[str, Any] | None = None, safe_evidence: dict[str, Any] | None = None, state_ok: bool | None = None) -> dict[str, Any]:
    expected = task["expected_behavior"]
    actual_status = execution.get("status")
    has_trace = bool(execution.get("execution_trace_id"))
    if expected == "REJECT":
        passed = actual_status == "FAILED" and bool(execution.get("error") or execution.get("response"))
        return {"status": "PASS" if passed else ("INFRA_ERROR" if actual_status == "FAILED" else "FAIL"), "reason": "actual invalid request was rejected" if passed else "invalid request did not produce a stable rejection", "evidence": {"trace_present": has_trace, "error": execution.get("error"), "response": execution.get("response")}}
    if expected in {"CLARIFY", "SAFE_STOP"}:
        # No LLM/Agent output is supplied by this Pilot run.  A B assess result
        # is evidence for the state probe, not evidence that an Agent asked or
        # stopped correctly.
        if expected == "SAFE_STOP":
            return {"status": "UNSCORABLE", "reason": "no Agent output; capacity evidence is recorded separately", "safe_stop_evidence": safe_evidence, "evidence": {"trace_present": has_trace, "b_result_status": actual_status}}
        return {"status": "UNSCORABLE", "reason": "no Agent output supplied for clarification scoring", "evidence": {"trace_present": has_trace, "b_result_status": actual_status}}
    if task.get("task_type") == "multi_round":
        if actual_status != "SUCCEEDED" or state_ok is not True:
            return {"status": "FAIL" if actual_status == "SUCCEEDED" else "INFRA_ERROR", "reason": "multi-round parent/child state was not established", "evidence": {"trace_present": has_trace, "state_ok": state_ok}}
    if actual_status != "SUCCEEDED":
        return {"status": "INFRA_ERROR" if actual_status == "FAILED" else "NOT_RUN", "reason": "valid execution did not complete", "evidence": execution.get("error") or execution.get("response")}
    if execution.get("verification_status") != "PASS_DEMO":
        return {"status": "FAIL", "reason": "B verification certificate was not PASS_DEMO", "evidence": {"verification_status": execution.get("verification_status"), "certificate": execution.get("certificate")}}
    summary = execution.get("result_summary") or {}
    gt = task["gt_assertions"]
    if execution.get("region_id") != gt["region_id"] or execution.get("data_version") != gt["data_version"]:
        return {"status": "FAIL", "reason": "execution identity differs from GT", "evidence": {"observed_region_id": execution.get("region_id"), "expected_region_id": gt["region_id"], "observed_data_version": execution.get("data_version"), "expected_data_version": gt["data_version"]}}
    if execution.get("data_status") != "DEMO_ONLY":
        return {"status": "FAIL", "reason": "execution data status is outside the Pilot admission contract", "evidence": {"data_status": execution.get("data_status")}}
    selected = set(execution.get("selected_option_ids") or [])
    allowed = set(gt.get("allowed_option_ids") or [])
    if selected - allowed:
        return {"status": "FAIL", "reason": "B selected an option outside the GT candidate set", "evidence": {"unexpected_option_ids": sorted(selected - allowed)}}
    if summary.get("optimized_served_demand") is not None and summary.get("total_demand") is not None and float(summary["optimized_served_demand"]) > float(summary["total_demand"]) + TOLERANCE:
        return {"status": "FAIL", "reason": "optimized served demand exceeds total demand", "evidence": summary}
    if oracle and oracle.get("status") == "ORACLE_PROVEN":
        b_value = float((execution.get("result_summary") or {}).get("optimized_served_demand") or 0.0)
        oracle_value = float((oracle.get("objective") or {}).get("served_demand") or 0.0)
        if abs(b_value - oracle_value) > 1e-3:
            return {"status": "FAIL", "reason": "B result differs from independent exhaustive oracle", "evidence": {"b_served_demand": b_value, "oracle_served_demand": oracle_value}}
    return {"status": "PASS", "reason": "actual B execution and independent checks passed", "evidence": {"trace_present": has_trace, "verification_status": execution.get("verification_status"), "oracle_status": (oracle or {}).get("status", "NOT_RUN"), "state_ok": state_ok}}


def _vets(task: dict[str, Any], execution: dict[str, Any], evaluation: dict[str, Any], oracle: dict[str, Any] | None) -> dict[str, Any]:
    status = evaluation["status"]
    if status == "UNSCORABLE" or status in {"NOT_RUN", "INFRA_ERROR"}:
        return {"status": status, "U": None, "W": None, "P": None, "E": 1.0 if execution.get("execution_trace_id") else 0.0, "overall": None, "cost": {"elapsed_ms": execution.get("elapsed_harness_ms"), "llm_tokens": None, "llm_cost": None}}
    if task["expected_behavior"] == "REJECT":
        values = {"U": 1.0, "W": 1.0, "P": 1.0, "E": 1.0 if execution.get("error") or execution.get("response") else 0.0}
    else:
        summary = execution.get("result_summary") or {}
        oracle_value = float((oracle or {}).get("objective", {}).get("served_demand") or 0.0)
        observed = float(summary.get("optimized_served_demand") or summary.get("served_demand") or 0.0)
        values = {
            "U": min(1.0, observed / oracle_value) if oracle_value > TOLERANCE else None,
            "W": 1.0 if status == "PASS" else 0.0,
            "P": 1.0 if execution.get("verification_status") == "PASS_DEMO" else 0.0,
            "E": 1.0 if execution.get("execution_trace_id") and execution.get("verification_status") == "PASS_DEMO" else 0.0,
        }
    applicable = [value for value in values.values() if value is not None]
    overall = sum(values[key] * weight for key, weight in (("U", .30), ("W", .25), ("P", .25), ("E", .20)) if values[key] is not None) / sum(weight for key, weight in (("U", .30), ("W", .25), ("P", .25), ("E", .20)) if values[key] is not None) if applicable else None
    return {"status": status, **values, "overall": overall, "cost": {"elapsed_ms": execution.get("elapsed_harness_ms"), "llm_tokens": None, "llm_cost": None}}


def _measured_difficulty(task: dict[str, Any], execution: dict[str, Any], evaluation: dict[str, Any], oracle: dict[str, Any] | None, state_ok: bool | None, safe_evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Only emit measured labels when execution evidence supports them."""
    base = {"decision_complexity": "NOT_MEASURED", "workflow_complexity": "NOT_MEASURED", "interaction_complexity": "NOT_MEASURED", "evidence_status": "NO_AGENT_OR_EXECUTION_EVIDENCE"}
    if task["expected_behavior"] in {"CLARIFY", "SAFE_STOP"}:
        base["evidence_status"] = "STATE_PROBE_ONLY_NO_AGENT_OUTPUT"
        if safe_evidence is not None:
            base["state_probe"] = {"capacity_evidence_measured": True, "safe_stop_supported": safe_evidence.get("safe_stop_supported_by_capacity_and_reachability")}
        return base
    if evaluation.get("status") not in {"PASS"}:
        base["evidence_status"] = "EXECUTION_DID_NOT_PASS"
        return base
    base["decision_complexity"] = "MEASURED_ORACLE_BACKED" if oracle and oracle.get("status") == "ORACLE_PROVEN" else "MEASURED_EXECUTION_ONLY"
    base["workflow_complexity"] = "MEASURED_MULTI_ROUND_STATE_INHERITANCE" if state_ok is True else "MEASURED_SINGLE_STAGE"
    base["interaction_complexity"] = "MEASURED_REJECTION" if task["expected_behavior"] == "REJECT" else "MEASURED_VALID_EXECUTION"
    base["evidence_status"] = "REAL_B_TRACE_AND_VERIFIER"
    return base


def run_case(task: dict[str, Any], *, use_http: bool = True, root: Path = ROOT) -> dict[str, Any]:
    source = load_real_source(task["region_id"], root)
    use_http_for_case = use_http and task["http_route_status"] == "SUPPORTED" and task["task_type"] != "multi_round"
    execution: dict[str, Any]
    oracle: dict[str, Any] | None = None
    safe_evidence: dict[str, Any] | None = None
    state_ok: bool | None = None
    if task["task_type"] == "multi_round":
        parent_payload = task["rounds"][0]["payload"]
        child_template = copy.deepcopy(task["rounds"][1]["payload_template"])
        parent = execute_http(parent_payload) if use_http_for_case else execute_direct(parent_payload, source)
        if parent.get("status") != "SUCCEEDED":
            execution = {"status": "FAILED", "rounds": [{"role": "parent", "execution": parent}], "execution_trace_id": parent.get("execution_trace_id"), "error": parent.get("error")}
        else:
            actual_parent_id = parent.get("scenario_id")
            child_template["parent_scenario_id"] = actual_parent_id
            child_template["scenario"]["parent_scenario_id"] = actual_parent_id
            child = execute_http(child_template) if use_http_for_case else execute_direct(child_template, source)
            state_ok = child.get("status") == "SUCCEEDED" and child.get("parent_scenario_id") == actual_parent_id
            execution = {
                "status": "SUCCEEDED" if child.get("status") == "SUCCEEDED" else "FAILED",
                "execution_trace_id": child.get("execution_trace_id"),
                "scenario_id": child.get("scenario_id"),
                "parent_scenario_id": child.get("parent_scenario_id"),
                "result_summary": child.get("result_summary"),
                "solver_status": child.get("solver_status"),
                "verification_status": child.get("verification_status"),
                "certificate": child.get("certificate"),
                "optimization_certificate": child.get("optimization_certificate"),
                "elapsed_harness_ms": (parent.get("elapsed_harness_ms") or 0) + (child.get("elapsed_harness_ms") or 0),
                "rounds": [{"role": "parent", "execution": parent}, {"role": "child", "execution": child}],
            }
            if child.get("status") == "SUCCEEDED":
                oracle = enumerate_budget_oracle(source.data, PlanningScenario(**child_template["scenario"]), _as_source_options(child_template["improvement_options"]))
    else:
        payload = task["b_request_payload"]
        if use_http_for_case:
            execution = execute_http(payload)
        else:
            execution = execute_direct(payload, source)
        if execution.get("status") == "SUCCEEDED" and payload.get("task_type") == "optimize":
            oracle = enumerate_budget_oracle(source.data, PlanningScenario(**payload["scenario"]), _as_source_options(payload.get("improvement_options", [])))
        if task["expected_behavior"] == "SAFE_STOP" and execution.get("status") == "SUCCEEDED":
            safe_evidence = safe_stop_capacity_evidence(source.data, PlanningScenario(**payload["scenario"]), execution)
    evaluation = _evaluate_output(task, execution, oracle=oracle, safe_evidence=safe_evidence, state_ok=state_ok)
    score = _vets(task, execution, evaluation, oracle)
    measured = _measured_difficulty(task, execution, evaluation, oracle, state_ok, safe_evidence)
    return {
        "task_id": task["task_id"],
        "scenario_id": task["scenario_id"],
        "region_id": task["region_id"],
        "expected_behavior": task["expected_behavior"],
        "scenario_family": task["scenario_family"],
        "interaction_type": task["interaction_type"],
        "execution_route": task["execution_route"],
        "http_route_status": task["http_route_status"],
        "execution": execution,
        "oracle": oracle or {"status": "NOT_RUN"},
        "safe_stop_evidence": safe_evidence,
        "evaluation": evaluation,
        "measured_difficulty": measured,
        "vets": score,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(row["evaluation"]["status"] for row in results)
    numeric = {key: [row["vets"][key] for row in results if row["vets"].get(key) is not None] for key in ("U", "W", "P", "E", "overall")}
    return {
        "result_count": len(results),
        "status_counts": dict(statuses),
        "behavior_counts": dict(Counter(row["expected_behavior"] for row in results)),
        "region_counts": dict(Counter(row["region_id"] for row in results)),
        "oracle_counts": dict(Counter((row.get("oracle") or {}).get("status") for row in results)),
        "route_status_counts": dict(Counter(row["http_route_status"] for row in results)),
        "vets_mean": {key: (sum(values) / len(values) if values else None) for key, values in numeric.items()},
        "execution_count": sum(row["execution"].get("status") == "SUCCEEDED" for row in results),
        "trace_count": sum(bool(row["execution"].get("execution_trace_id")) for row in results),
        "measured_difficulty_counts": dict(Counter(row["measured_difficulty"].get("evidence_status") for row in results)),
        "greedy_comparison_count": sum(bool(row["execution"].get("greedy_comparison")) for row in results),
        "llm_cost": {"tokens": None, "currency": None, "reason": "No LLM Agent was configured for this Pilot run"},
    }


def write_report(output_dir: Path, tasks: list[dict[str, Any]], results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# ShelterPlan-Eval Stage 3 Pilot",
        "",
        "本报告复用冻结的 Stage 2.1 24 个 Pilot；未重新随机生成场景，未修改 A v2、旧 Pilot 或 main。",
        "",
        "## Run summary",
        "",
        f"- Cases: {summary['result_count']}",
        f"- Actual B executions: {summary['execution_count']}",
        f"- Trace-bearing results: {summary['trace_count']}",
        f"- Status: `{json.dumps(summary['status_counts'], ensure_ascii=False)}`",
        f"- Oracle: `{json.dumps(summary['oracle_counts'], ensure_ascii=False)}`",
        f"- Measured difficulty evidence: `{json.dumps(summary['measured_difficulty_counts'], ensure_ascii=False)}`",
        f"- Real Greedy comparisons: `{summary['greedy_comparison_count']}`",
        f"- VETS mean: `{json.dumps(summary['vets_mean'], ensure_ascii=False)}`",
        "- LLM: `NOT_RUN`; this run has no Agent/LLM output and therefore CLARIFY/SAFE_STOP are UNSCORABLE.",
        "- VETS means apply only to cases/dimensions with evidence; the two UNSCORABLE cases were not silently converted to zeros.",
        "",
        "## Route policy",
        "",
        "- Shiqi uses the configured Module B HTTP route when enabled.",
        "- East District uses its explicit `configs/module_a_v2_b/east_district.yaml` source with the real B Solver. The checked-in HTTP route remains Shiqi-only and is recorded as `UNSUPPORTED`; there is no silent fallback.",
        "",
        "## Case results",
        "",
        "| scenario | region | family | expected | execution | evaluation | oracle | trace |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        lines.append("| {scenario_id} | {region_id} | {scenario_family} | {expected_behavior} | {execution_status} | {evaluation} | {oracle} | {trace} |".format(
            scenario_id=row["scenario_id"], region_id=row["region_id"], scenario_family=row["scenario_family"], expected_behavior=row["expected_behavior"], execution_status=row["execution"].get("status"), evaluation=row["evaluation"].get("status"), oracle=(row.get("oracle") or {}).get("status"), trace=row["execution"].get("execution_trace_id") or "NONE",
        ))
    lines.extend([
        "",
        "## Measured difficulty and execution evidence",
        "",
        "| scenario | solver | baseline served | optimized/oracle served | greedy served | runtime ms | measured difficulty |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ])
    for row in results:
        result_summary = row["execution"].get("result_summary") or {}
        oracle_objective = (row.get("oracle") or {}).get("objective") or {}
        greedy = row["execution"].get("greedy_comparison") or {}
        optimized = result_summary.get("optimized_served_demand")
        if optimized is None:
            optimized = oracle_objective.get("served_demand")
        lines.append("| {scenario} | {solver} | {baseline} | {optimized} | {greedy} | {runtime} | {difficulty} |".format(
            scenario=row["scenario_id"], solver=row["execution"].get("solver_status", "NOT_RUN"), baseline=result_summary.get("served_demand", "N/A"), optimized=optimized if optimized is not None else "N/A", greedy=greedy.get("served_demand", "N/A"), runtime=row["execution"].get("elapsed_harness_ms", "N/A"), difficulty=(row.get("measured_difficulty") or {}).get("decision_complexity", "NOT_MEASURED"),
        ))
    lines.extend([
        "",
        "### Safety and multi-round evidence",
        "",
    ])
    for row in results:
        evidence = row.get("safe_stop_evidence")
        if evidence is not None:
            lines.append(f"- `{row['scenario_id']}` SAFE_STOP probe: baseline served {evidence['baseline_served_demand']:.3f}/{evidence['total_demand']:.3f}; capacity-unbounded upper bound {evidence['capacity_unbounded_reachability_upper_bound']:.3f}; supported=`{evidence['safe_stop_supported_by_capacity_and_reachability']}`. No Agent score was awarded.")
        if row["interaction_type"] == "parameter_edit":
            rounds = row["execution"].get("rounds") or []
            parent = rounds[0].get("execution", {}) if rounds else {}
            child = rounds[1].get("execution", {}) if len(rounds) > 1 else {}
            lines.append(f"- `{row['scenario_id']}` multi-round: runtime parent=`{parent.get('scenario_id', 'NONE')}`, child parent reference=`{child.get('parent_scenario_id', 'NONE')}`, state_ok=`{row['evaluation'].get('evidence', {}).get('state_ok')}`.")
        if row["expected_behavior"] == "REJECT":
            error = row["execution"].get("error") or {}
            response = row["execution"].get("response") or {}
            observed = error.get("error_code") or (response.get("error") or {}).get("error_code") or "NONE"
            lines.append(f"- `{row['scenario_id']}` REJECT evidence: observed error=`{observed}`, HTTP status=`{row['execution'].get('http_status', 'N/A')}`, trace=`{row['execution'].get('execution_trace_id', 'NONE')}`.")
    lines.extend([
        "",
        "## Scoring contract",
        "",
        "VETS 输出 U/W/P/E：Utility、Workflow、Policy/Safety、Evidence。默认加权为 U 0.30、W 0.25、P 0.25、E 0.20；仅对适用维度求归一化均值。正确 REJECT 可得成功分，但必须有实际 HTTP/B 错误证据；CLARIFY 与 SAFE_STOP 在没有 Agent 输出时保持 UNSCORABLE。",
        "",
        "## Limitations and blockers",
        "",
        "- All real GIS runs remain `DEMO_ONLY`; no result is `VERIFIED`.",
        "- No LLM/Agent baseline was run, so this is not yet a formal Flat Tool Agent vs Fixed Workflow vs Composite Skill + Harness comparison.",
        "- Any East District HTTP route integration must be implemented and tested by B/C; this evaluation does not change B/C configuration.",
    ])
    (output_dir / "stage3_pilot_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_pilot(*, case_ids: tuple[str, ...] = DEFAULT_CASE_IDS, output_dir: Path = ROOT / "reports/stage3_pilot", use_http: bool = True) -> dict[str, Any]:
    scenarios = load_pilot()
    tasks = build_tasks(scenarios, case_ids=case_ids)
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    results = [run_case(task, use_http=use_http) for task in tasks]
    summary = summarize(results)
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "source_pilot": "benchmark/shelterplan_eval/pilot_stage2_1/accepted_scenarios.json",
        "case_ids": list(case_ids),
        "tasks": len(tasks),
        "results": len(results),
        "dataset_content_hashes": sorted({scenario["dataset_content_hash"] for scenario in scenarios if scenario["scenario_id"] in set(case_ids)}),
        "source_manifest_sha256_values": sorted({scenario["source_manifest_sha256"] for scenario in scenarios if scenario["scenario_id"] in set(case_ids)}),
        "summary": summary,
    }
    (output_dir / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "gt_assertions.json").write_text(json.dumps([{"task_id": task["task_id"], "scenario_id": task["scenario_id"], "gt_assertions": task["gt_assertions"]} for task in tasks], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "results.json").write_text(json.dumps({"manifest": manifest, "results": results}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_report(output_dir, tasks, results, summary)
    return {"output_dir": str(output_dir.relative_to(ROOT).as_posix()), "manifest": manifest, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=",".join(DEFAULT_CASE_IDS), help="comma-separated frozen Pilot scenario IDs")
    parser.add_argument("--output-dir", default="reports/stage3_pilot")
    parser.add_argument("--no-http", action="store_true", help="use explicit direct B Solver for all cases")
    args = parser.parse_args()
    case_ids = tuple(value.strip() for value in args.cases.split(",") if value.strip())
    result = run_pilot(case_ids=case_ids, output_dir=Path(args.output_dir), use_http=not args.no_http)
    print(json.dumps({"output_dir": result["output_dir"], "manifest": result["manifest"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
