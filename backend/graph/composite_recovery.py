"""Finite, result-driven orchestration for disaster-recovery planning.

This module composes existing registered Module B tasks. It does not implement
another solver or accept a caller-supplied DAG. Every stage reuses the same
allowlisted source bundle and is persisted as a normal B result document.
"""
from __future__ import annotations

from datetime import datetime, timezone
import platform
import time
import uuid
from typing import Any

from backend.errors import CandidateError, PlanningError, ScenarioError
from backend.graph.executor import execute_task, load_task_source
from backend.models import (
    DisasterRecoveryRequest,
    PlanningScenario,
    RecoveryTaskContract,
    RecoveryStageRecord,
    TaskRequest,
)
from backend.storage import JsonResultStore, canonical_hash, save_scenario_version

_RESULTS = JsonResultStore("outputs/module_b/runs")
_TOLERANCE = 1e-6


class CompositeWorkflowPlanner:
    """Choose the next stage from observed stage output and confirmed inputs."""

    ORDER = ("baseline", "damage", "diagnose", "recovery", "compare", "verify")

    @classmethod
    def next_stage(cls, completed: set[str], *, budget_units: float | None,
                   option_count: int) -> tuple[str | None, str | None]:
        for stage_id in cls.ORDER:
            if stage_id in completed:
                continue
            if stage_id == "damage" and "baseline" not in completed:
                return None, "BASELINE_REQUIRED"
            if stage_id == "diagnose" and "damage" not in completed:
                return None, "DAMAGE_REQUIRED"
            if stage_id == "recovery":
                if "diagnose" not in completed:
                    return None, "DIAGNOSIS_REQUIRED"
                if budget_units is None:
                    return None, "BUDGET_REQUIRED"
                if option_count == 0:
                    return None, "RECOVERY_OPTIONS_REQUIRED"
            if stage_id in {"compare", "verify"} and (
                "recovery" not in completed if stage_id == "compare"
                else "compare" not in completed
            ):
                return None, "RECOVERY_REQUIRED" if stage_id == "compare" else "COMPARE_REQUIRED"
            return stage_id, None
        return None, None


def _record(stage_id: str, status: str, **fields: Any) -> dict[str, Any]:
    return RecoveryStageRecord(stage_id=stage_id, status=status, **fields).model_dump()


def _stage_metrics(document: dict[str, Any]) -> dict[str, Any]:
    detail = document.get("result_detail") or {}
    assignment = detail.get("assignment") or {}
    summary = document.get("result_summary") or {}
    diagnosis = detail.get("diagnosis")
    metrics = {
        "served_demand": summary.get("served_demand"),
        "total_demand": summary.get("total_demand"),
        "weighted_walk_time_min": summary.get("weighted_walk_time_min"),
    }
    if isinstance(diagnosis, list):
        metrics["diagnosed_unserved_nodes"] = len(diagnosis)
    if "optimization" in detail:
        optimization = detail["optimization"]
        optimized_assignment = optimization.get("assignment") or {}
        metrics.update({
            "optimized_served_demand": optimization.get("objective_value"),
            "selected_option_ids": list(optimization.get("selected_option_ids") or []),
            "total_cost_units": optimization.get("total_cost_units"),
            "budget_units": optimization.get("budget_units"),
            "optimized_weighted_walk_time_min": optimized_assignment.get("weighted_walk_time_min"),
        })
    if not metrics.get("total_demand"):
        metrics["total_demand"] = assignment.get("total_demand")
    return metrics


def _constraint(check_id: str, passed: bool, observed: Any, expected: Any) -> dict[str, Any]:
    return {"check_id": check_id, "passed": bool(passed), "observed": observed, "expected": expected}


def _failure(exc: PlanningError, contract: dict[str, Any], *, stage_id: str,
             reason_code: str | None = None) -> PlanningError:
    stages = contract.setdefault("stage_results", [])
    running = next((row for row in reversed(stages)
                    if row.get("stage_id") == stage_id and row.get("status") == "RUNNING"), None)
    if running is not None:
        running["status"] = "FAILED"
        running["reason_code"] = reason_code or exc.error_code
    elif not any(row.get("stage_id") == stage_id for row in stages):
        stages.append(_record(stage_id, "FAILED", reason_code=reason_code or exc.error_code))
    contract["status"] = "FAILED"
    contract.setdefault("branch_decisions", []).append({
        "stage_id": stage_id, "decision": "STOP", "reason_code": reason_code or exc.error_code,
        "evidence": "Module B stage returned an error; downstream stages were not submitted.",
    })
    details = {**exc.details, "composite_task": contract, "cause_error_code": exc.error_code}
    return type(exc)(str(exc), details=details)


def _check(passed: bool, check_id: str, observed: Any, expected: Any,
           checks: list[dict[str, Any]]) -> None:
    row = _constraint(check_id, passed, observed, expected)
    checks.append(row)
    if not passed:
        raise ScenarioError(
            "Composite workflow constraint check failed.",
            details={"error_code": "COMPOSITE_CONSTRAINT_FAILED", "failed_check": row},
        )


def execute_disaster_recovery(
    request: DisasterRecoveryRequest,
    *,
    execution_trace_id: str | None = None,
) -> dict[str, Any]:
    """Run baseline, damage, diagnosis, recovery, comparison and verification.

    Missing budget/options are a planned stop: no recovery solver call is made.
    Invalid options or a failed verifier stop the chain with the real partial
    stage references attached to the planning error.
    """
    started = time.perf_counter()
    task_id = request.task_id or str(uuid.uuid4())
    trace_id = execution_trace_id or str(uuid.uuid4())
    stage_task_ids = {
        "baseline": f"{task_id[:115]}:baseline",
        "damage": f"{task_id[:120]}:damage",
        "recovery": f"{task_id[:119]}:recovery",
    }
    data, data_defaults, fixture_options = load_task_source(
        request.fixture_id, request.region_id,
        request.data_version or (request.scenario.data_version if request.scenario else None),
    )
    if request.region_id is not None and request.region_id != data.region_id:
        raise ScenarioError("Request region does not match the selected source.", details={
            "error_code": "REGION_MISMATCH", "expected": data.region_id, "received": request.region_id,
        })
    if request.data_version is not None and request.data_version != data.data_version:
        raise ScenarioError("Request data version does not match the selected source.", details={
            "error_code": "DATA_VERSION_MISMATCH", "expected": data.data_version,
            "received": request.data_version,
        })
    source_digest = data.source_manifest_hash or canonical_hash(data.model_dump())
    if request.source_manifest_digest and request.source_manifest_digest != source_digest:
        raise ScenarioError("Source manifest digest changed before execution.", details={
            "error_code": "DATA_VERSION_MISMATCH", "expected": source_digest,
            "received": request.source_manifest_digest,
        })

    base = request.scenario or data_defaults
    if base.data_version != data.data_version:
        raise ScenarioError("Baseline scenario uses a different data version.", details={
            "error_code": "DATA_VERSION_MISMATCH", "expected": data.data_version,
            "received": base.data_version,
        })
    if data.region_id is not None and base.region_id not in (None, data.region_id):
        raise ScenarioError("Baseline scenario uses a different region.", details={
            "error_code": "REGION_MISMATCH", "expected": data.region_id,
            "received": base.region_id,
        })

    options = request.improvement_options or []
    if len(options) > 12:
        raise CandidateError("Recovery candidate set exceeds the public 12-option limit.", details={
            "error_code": "CANDIDATE_LIMIT_EXCEEDED", "max_options": 12,
        })

    composite_task_id = task_id
    baseline_id = f"{composite_task_id[:112]}-baseline"
    damaged_id = f"{composite_task_id[:111]}-damaged"
    recovered_id = f"{composite_task_id[:110]}-recovered"
    baseline = PlanningScenario.model_validate({
        **base.model_dump(),
        "scenario_id": baseline_id,
        "parent_scenario_id": request.baseline_parent_scenario_id,
        "region_id": data.region_id,
        "analysis_mode": request.analysis_mode,
        "budget_units": None,
        "changes": {},
    })
    damaged_ids = list(dict.fromkeys(
        [*baseline.disabled_shelter_ids, *request.damaged_shelter_ids]
    ))
    damaged = PlanningScenario.model_validate({
        **baseline.model_dump(),
        "scenario_id": damaged_id,
        "parent_scenario_id": baseline_id,
        "disabled_shelter_ids": damaged_ids,
        "changes": {"disabled_shelter_ids": damaged_ids},
    })
    recovered = PlanningScenario.model_validate({
        **damaged.model_dump(),
        "scenario_id": recovered_id,
        "parent_scenario_id": damaged_id,
        "budget_units": request.budget_units,
        "changes": {"budget_units": request.budget_units},
    })
    contract: dict[str, Any] = {
        "task_id": composite_task_id,
        "client_session_id": request.client_session_id,
        "client_turn_id": request.client_turn_id,
        "region_id": data.region_id,
        "data_version": data.data_version,
        "source_manifest_digest": source_digest,
        "user_goal": request.user_goal,
        "population_demand_ratio": baseline.population_demand_ratio,
        "max_walk_time_min": baseline.max_walk_time_min,
        "baseline_disabled_shelter_ids": list(baseline.disabled_shelter_ids),
        "disabled_shelter_ids": damaged_ids,
        "budget_units": request.budget_units,
        "improvement_options": [row.model_dump() for row in options],
        "baseline_scenario_id": baseline_id,
        "damaged_scenario_id": damaged_id,
        "recovered_scenario_id": recovered_id,
        "stage_results": [],
        "branch_decisions": [],
        "constraint_checks": [],
        "verification_results": [],
        "execution_trace_id": trace_id,
        "status": "COMPLETED",
    }
    source_override = (data, data_defaults, fixture_options)
    completed: set[str] = set()
    stage_docs: dict[str, dict[str, Any]] = {}
    trace_rows: list[dict[str, Any]] = []
    stage_inputs = {"baseline": baseline, "damage": damaged, "recovery": recovered}
    active_stage_id = "baseline"

    def run_b_stage(stage_id: str, task: TaskRequest) -> dict[str, Any]:
        nonlocal active_stage_id
        active_stage_id = stage_id
        contract["stage_results"].append(_record(
            stage_id, "RUNNING", input_scenario_id=task.parent_scenario_id,
            scenario_id=task.scenario_id or (task.scenario.scenario_id if task.scenario else None),
            parent_scenario_id=task.parent_scenario_id,
            effective_parameters=task.scenario.model_dump() if task.scenario else {},
        ))
        document = execute_task(task, source_override=source_override)
        _RESULTS.save(document["result_id"], document)
        stage_docs[stage_id] = document
        trace_rows.extend({"stage_id": stage_id, **row} for row in document.get("trace", []))
        if document.get("verification_status") not in {"PASS_DEMO", "PASS_VERIFIED"}:
            contract["stage_results"][-1].update({
                "scenario_id": document.get("scenario_id"),
                "parent_scenario_id": document.get("parent_scenario_id"),
                "result_id": document.get("result_id"),
                "execution_trace_id": document.get("execution_trace_id"),
                "skill_ids": [row.get("skill_id") for row in document.get("trace", [])],
                "solver_status": document.get("solver_status"),
                "verification_status": document.get("verification_status"),
                "effective_parameters": document.get("effective_parameters") or {},
                "metrics": _stage_metrics(document),
                "duration_ms": document.get("elapsed_ms"),
            })
            raise ScenarioError("A stage verifier blocked the composite workflow.", details={
                "error_code": "STAGE_VERIFICATION_FAILED",
                "stage_id": stage_id,
                "result_id": document.get("result_id"),
                "verification_status": document.get("verification_status"),
                "trace": document.get("trace", []),
            })
        return document

    # The finite planner consults only real prior output and confirmed inputs.
    try:
        next_stage, reason = CompositeWorkflowPlanner.next_stage(completed, budget_units=request.budget_units,
                                                                 option_count=len(options))
        if next_stage != "baseline":
            raise ScenarioError("Baseline stage was not selected by the composite planner.")
        baseline_doc = run_b_stage("baseline", TaskRequest(
            task_id=stage_task_ids["baseline"], task_type="assess", scenario=baseline,
            scenario_id=baseline_id,
            region_id=data.region_id, data_version=data.data_version,
            analysis_mode=request.analysis_mode, fixture_id=request.fixture_id,
            parent_scenario_id=request.baseline_parent_scenario_id,
        ))
        completed.add("baseline")
        contract["stage_results"][-1].update({
            "status": "COMPLETED", "reason_code": None,
            "scenario_id": baseline_doc["scenario_id"],
            "parent_scenario_id": baseline_doc.get("parent_scenario_id"),
            "result_id": baseline_doc["result_id"],
            "execution_trace_id": baseline_doc.get("execution_trace_id"),
            "skill_ids": [row["skill_id"] for row in baseline_doc.get("trace", []) if row.get("skill_id") in {"S01", "S02", "S03", "S04"}],
            "solver_status": baseline_doc.get("solver_status"),
            "verification_status": baseline_doc.get("verification_status"),
            "effective_parameters": baseline_doc.get("effective_parameters") or baseline.model_dump(),
            "metrics": _stage_metrics(baseline_doc),
            "duration_ms": baseline_doc.get("elapsed_ms"),
        })
        contract["branch_decisions"].append({"after_stage": "baseline", "decision": "CONTINUE",
                                              "reason_code": "BASELINE_RESULT_AVAILABLE",
                                              "result_id": baseline_doc["result_id"]})

        next_stage, reason = CompositeWorkflowPlanner.next_stage(completed, budget_units=request.budget_units,
                                                                 option_count=len(options))
        if next_stage != "damage":
            raise ScenarioError("Damage stage was not selected after baseline.")
        damage_doc = run_b_stage("damage", TaskRequest(
            task_id=stage_task_ids["damage"], task_type="assess", scenario=damaged,
            scenario_id=damaged_id,
            region_id=data.region_id, data_version=data.data_version,
            analysis_mode=request.analysis_mode, fixture_id=request.fixture_id,
            parent_scenario_id=baseline_id,
        ))
        completed.add("damage")
        contract["stage_results"][-1].update({
            "status": "COMPLETED", "reason_code": None,
            "scenario_id": damage_doc["scenario_id"],
            "parent_scenario_id": damage_doc.get("parent_scenario_id"),
            "result_id": damage_doc["result_id"],
            "execution_trace_id": damage_doc.get("execution_trace_id"),
            "skill_ids": [row["skill_id"] for row in damage_doc.get("trace", []) if row.get("skill_id") in {"S01", "S02", "S03", "S04"}],
            "solver_status": damage_doc.get("solver_status"),
            "verification_status": damage_doc.get("verification_status"),
            "effective_parameters": damage_doc.get("effective_parameters") or damaged.model_dump(),
            "metrics": _stage_metrics(damage_doc),
            "duration_ms": damage_doc.get("elapsed_ms"),
        })
        contract["branch_decisions"].append({"after_stage": "damage", "decision": "CONTINUE",
                                              "reason_code": "DAMAGED_RESULT_AVAILABLE",
                                              "result_id": damage_doc["result_id"]})

        next_stage, reason = CompositeWorkflowPlanner.next_stage(completed, budget_units=request.budget_units,
                                                                 option_count=len(options))
        if next_stage != "diagnose":
            raise ScenarioError("Diagnosis stage was not selected after damage.")
        diagnosis_rows = (damage_doc.get("result_detail") or {}).get("diagnosis") or []
        diagnose_trace = [row for row in damage_doc.get("trace", []) if row.get("skill_id") == "S05"]
        if not diagnose_trace:
            raise ScenarioError("Damage result omitted the registered S05 diagnosis trace.", details={
                "error_code": "STAGE_VERIFICATION_FAILED", "result_id": damage_doc["result_id"],
            })
        contract["stage_results"].append(_record(
            "diagnose", "COMPLETED", input_scenario_id=damaged_id,
            scenario_id=damage_doc["scenario_id"], parent_scenario_id=baseline_id,
            result_id=damage_doc["result_id"], execution_trace_id=damage_doc.get("execution_trace_id"),
            skill_ids=["S05"], solver_status=damage_doc.get("solver_status"),
            verification_status=damage_doc.get("verification_status"),
            effective_parameters=damage_doc.get("effective_parameters") or damaged.model_dump(),
            metrics={"diagnosed_unserved_nodes": len(diagnosis_rows),
                     "diagnosis_by_cause": _diagnosis_counts(diagnosis_rows)},
            duration_ms=sum(float(row.get("duration_ms") or 0) for row in diagnose_trace),
        ))
        completed.add("diagnose")

        next_stage, reason = CompositeWorkflowPlanner.next_stage(completed, budget_units=request.budget_units,
                                                                 option_count=len(options))
        if next_stage is None and reason in {"BUDGET_REQUIRED", "RECOVERY_OPTIONS_REQUIRED"}:
            contract["status"] = "NEEDS_INPUT"
            contract["recovered_scenario_id"] = None
            contract["branch_decisions"].append({"after_stage": "diagnose", "decision": "STOP",
                                                  "reason_code": reason,
                                                  "evidence": "No recovery solver request was submitted."})
            for skipped in ("recovery", "compare", "verify"):
                contract["stage_results"].append(_record(skipped, "SKIPPED", reason_code=reason))
            return _assemble_result(request, contract, stage_docs, trace_rows, data,
                                    source_digest, started, trace_id, status="NEEDS_INPUT")
        if next_stage != "recovery":
            raise ScenarioError("Recovery stage was not selected after diagnosis.")

        active_stage_id = "recovery"
        disabled_set = set(damaged.disabled_shelter_ids)
        invalid_options = [row for row in options
                           if not row.eligible or row.shelter_id in disabled_set]
        if invalid_options:
            raise CandidateError("Recovery options must be eligible and target an active shelter.", details={
                "error_code": "INVALID_RECOVERY_OPTIONS",
                "invalid_option_ids": [row.option_id for row in invalid_options],
                "disabled_shelter_ids": sorted(disabled_set),
            })
        recovery_doc = run_b_stage("recovery", TaskRequest(
            task_id=stage_task_ids["recovery"], task_type="optimize", scenario=recovered,
            scenario_id=recovered_id,
            region_id=data.region_id, data_version=data.data_version,
            analysis_mode=request.analysis_mode, fixture_id=request.fixture_id,
            improvement_options=options, parent_scenario_id=damaged_id,
            parameters={"budget_units": request.budget_units},
        ))
        completed.add("recovery")
        contract["stage_results"][-1].update({
            "status": "COMPLETED", "reason_code": None,
            "scenario_id": recovery_doc["scenario_id"],
            "parent_scenario_id": recovery_doc.get("parent_scenario_id"),
            "result_id": recovery_doc["result_id"],
            "execution_trace_id": recovery_doc.get("execution_trace_id"),
            "skill_ids": [row["skill_id"] for row in recovery_doc.get("trace", [])],
            "solver_status": recovery_doc.get("solver_status"),
            "verification_status": recovery_doc.get("verification_status"),
            "effective_parameters": recovery_doc.get("effective_parameters") or recovered.model_dump(),
            "metrics": _stage_metrics(recovery_doc),
            "duration_ms": recovery_doc.get("elapsed_ms"),
        })
        contract["branch_decisions"].append({"after_stage": "recovery", "decision": "CONTINUE",
                                              "reason_code": "RECOVERY_RESULT_AVAILABLE",
                                              "solver_status": recovery_doc.get("solver_status"),
                                              "verification_status": recovery_doc.get("verification_status"),
                                              "result_id": recovery_doc["result_id"]})

        next_stage, reason = CompositeWorkflowPlanner.next_stage(completed, budget_units=request.budget_units,
                                                                 option_count=len(options))
        if next_stage != "compare":
            raise ScenarioError("Comparison stage was not selected after recovery.")
        active_stage_id = "compare"
        baseline_metrics = _stage_metrics(baseline_doc)
        damage_metrics = _stage_metrics(damage_doc)
        recovery_metrics = _stage_metrics(recovery_doc)
        baseline_served = float(baseline_metrics["served_demand"])
        damaged_served = float(damage_metrics["served_demand"])
        recovered_served = float(recovery_metrics.get("optimized_served_demand") or 0.0)
        service_loss = baseline_served - damaged_served
        recovery_gain = recovered_served - damaged_served
        recovery_ratio = ({"status": "APPLICABLE", "value": recovery_gain / service_loss}
                          if service_loss > _TOLERANCE else {
                              "status": "NOT_APPLICABLE", "value": None,
                              "reason_code": "NO_DAMAGE_SERVICE_LOSS",
                          })
        opt_detail = (recovery_doc.get("result_detail", {}).get("optimization") or {})
        comparison = {
            "baseline": {**baseline_metrics, "result_id": baseline_doc["result_id"]},
            "damaged": {**damage_metrics, "result_id": damage_doc["result_id"]},
            "recovered": {**recovery_metrics, "result_id": recovery_doc["result_id"]},
            "damage_service_loss": service_loss,
            "recovery_service_gain": recovery_gain,
            "recovery_ratio": recovery_ratio,
            "budget_units": request.budget_units,
            "selected_option_ids": list(opt_detail.get("selected_option_ids") or []),
            "greedy_comparison": opt_detail.get("greedy_comparison"),
        }
        contract["stage_results"].append(_record(
            "compare", "COMPLETED", input_scenario_id=damaged_id,
            scenario_id=recovered_id, parent_scenario_id=damaged_id,
            result_id=recovery_doc["result_id"], execution_trace_id=trace_id,
            metrics={"baseline_result_id": baseline_doc["result_id"],
                     "damaged_result_id": damage_doc["result_id"],
                     "recovered_result_id": recovery_doc["result_id"],
                     "damage_service_loss": service_loss,
                     "recovery_service_gain": recovery_gain,
                     "recovery_ratio": recovery_ratio},
        ))
        completed.add("compare")

        next_stage, reason = CompositeWorkflowPlanner.next_stage(completed, budget_units=request.budget_units,
                                                                 option_count=len(options))
        if next_stage != "verify":
            raise ScenarioError("Verification stage was not selected after comparison.")
        active_stage_id = "verify"
        checks = contract["constraint_checks"]
        _check(baseline_doc.get("region_id") == data.region_id, "REGION_MISMATCH",
               baseline_doc.get("region_id"), data.region_id, checks)
        for name, document in (("baseline", baseline_doc), ("damage", damage_doc), ("recovery", recovery_doc)):
            expected_scenario = stage_inputs[name]
            echoed = document.get("effective_parameters") or {}
            _check(document.get("data_version") == data.data_version,
                   "DATA_VERSION_MISMATCH", document.get("data_version"), data.data_version, checks)
            _check(document.get("region_id") == data.region_id,
                   "REGION_MISMATCH", document.get("region_id"), data.region_id, checks)
            _check(echoed == expected_scenario.model_dump(), "PARAMETER_DRIFT",
                   echoed, expected_scenario.model_dump(), checks)
            expected_hash = canonical_hash({"scenario": expected_scenario.model_dump(),
                                            "data_version": data.data_version})
            _check(document.get("scenario_hash") == expected_hash, "RESULT_BINDING_MISMATCH",
                   document.get("scenario_hash"), expected_hash, checks)
            _check(bool(document.get("result_id")) and document.get("task_id") == stage_task_ids[name],
                   "RESULT_BINDING_MISMATCH",
                   {"result_id": document.get("result_id"), "task_id": document.get("task_id")},
                   {"result_id": "non-empty", "task_id": stage_task_ids[name]}, checks)
        _check(damage_doc["effective_parameters"]["disabled_shelter_ids"] == damaged_ids,
               "DAMAGE_STATE_LOST", damage_doc["effective_parameters"].get("disabled_shelter_ids"),
               damaged_ids, checks)
        _check(recovery_doc["effective_parameters"]["disabled_shelter_ids"] == damaged_ids,
               "DAMAGE_STATE_LOST", recovery_doc["effective_parameters"].get("disabled_shelter_ids"),
               damaged_ids, checks)
        _check(abs(float(recovery_doc["effective_parameters"]["budget_units"]) - float(request.budget_units)) <= _TOLERANCE,
               "INVALID_RECOVERY_OPTIONS", recovery_doc["effective_parameters"].get("budget_units"),
               request.budget_units, checks)
        for stage_id, document in (("baseline", baseline_doc), ("damage", damage_doc), ("recovery", recovery_doc)):
            passed = document.get("verification_status") in {"PASS_DEMO", "PASS_VERIFIED"}
            contract["verification_results"].append({
                "stage_id": stage_id, "result_id": document.get("result_id"),
                "status": document.get("verification_status"), "passed": passed,
            })
            _check(passed, "STAGE_VERIFICATION_FAILED", document.get("verification_status"),
                   ["PASS_DEMO", "PASS_VERIFIED"], checks)
        contract["stage_results"].append(_record(
            "verify", "COMPLETED", input_scenario_id=recovered_id,
            scenario_id=recovered_id, parent_scenario_id=damaged_id,
            result_id=recovery_doc["result_id"], execution_trace_id=trace_id,
            skill_ids=[], solver_status=recovery_doc.get("solver_status"),
            verification_status="PASS_DEMO" if data.data_status != "VERIFIED" else "PASS_VERIFIED",
            metrics={"constraint_check_count": len(checks),
                     "passed_constraint_check_count": sum(1 for row in checks if row["passed"])},
        ))
        completed.add("verify")
        contract["branch_decisions"].append({"after_stage": "compare", "decision": "PUBLISH",
                                              "reason_code": "ALL_STAGE_AND_CONSTRAINT_CHECKS_PASSED",
                                              "constraint_check_count": len(checks)})
        stage_records = {row["stage_id"]: row for row in contract["stage_results"]}
        for row in contract["stage_results"]:
            row["input_result_ids"] = _input_result_ids(row["stage_id"], stage_records)
        contract = RecoveryTaskContract.model_validate(contract).model_dump()
        return _assemble_result(request, contract, stage_docs, trace_rows, data,
                                source_digest, started, trace_id, status="COMPLETED",
                                comparison=comparison)
    except PlanningError as exc:
        if isinstance(exc, ScenarioError) and exc.details.get("error_code") == "COMPOSITE_CONSTRAINT_FAILED":
            contract["constraint_checks"].append(exc.details["failed_check"])
            contract["verification_results"].append({"stage_id": "verify", "status": "FAIL", "passed": False})
        stage_id = ((exc.details or {}).get("stage_id") or active_stage_id)
        enriched = _failure(exc, contract, stage_id=stage_id,
                            reason_code=(exc.details or {}).get("error_code"))
        raise enriched


def _diagnosis_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        cause = str(row.get("diagnosis") or "UNKNOWN")
        counts[cause] = counts.get(cause, 0) + 1
    return counts


def _assemble_result(request: DisasterRecoveryRequest, contract: dict[str, Any],
                     stage_docs: dict[str, dict[str, Any]], trace_rows: list[dict[str, Any]],
                     data, source_digest: str, started: float, trace_id: str,
                     *, status: str, comparison: dict[str, Any] | None = None) -> dict[str, Any]:
    stages = {row["stage_id"]: row for row in contract["stage_results"]}
    baseline = stages.get("baseline") or {}
    damage = stages.get("damage") or {}
    recovery = stages.get("recovery") or {}
    baseline_served = (baseline.get("metrics") or {}).get("served_demand")
    damaged_served = (damage.get("metrics") or {}).get("served_demand")
    recovered_metrics = recovery.get("metrics") or {}
    recovered_served = recovered_metrics.get("optimized_served_demand")
    recovered_total = recovered_metrics.get("total_demand") or (baseline.get("metrics") or {}).get("total_demand")
    verification_status = "BLOCKED"
    if status == "COMPLETED":
        verification_status = "PASS_VERIFIED" if data.data_status == "VERIFIED" else "PASS_DEMO"
    recovery_solver = recovery.get("solver_status") or "NOT_RUN"
    result_id = str(uuid.uuid4())
    detail = {
        "composite": {
            "contract": contract,
            "comparison": comparison,
            "stage_results": [
                {**row, "input_result_ids": _input_result_ids(row["stage_id"], stages)}
                for row in contract["stage_results"]
            ],
            "historical_or_demo_disclosure": _source_disclosure(data.data_status),
        }
    }
    return {
        "task_id": contract["task_id"], "job_id": str(uuid.uuid4()), "result_id": result_id,
        "task_type": "disaster_recovery", "status": status,
        "scenario_id": ((contract.get("recovered_scenario_id") if status == "COMPLETED" else None)
                         or contract.get("damaged_scenario_id") or contract.get("baseline_scenario_id")),
        "parent_scenario_id": request.baseline_parent_scenario_id,
        "region_id": data.region_id, "data_version": data.data_version,
        "source_manifest_digest": source_digest, "data_status": data.data_status,
        "analysis_mode": request.analysis_mode,
        "skill_graph_version": "module-b-skillgraph-0.1.0",
        "execution_trace_id": trace_id,
        "solver_status": recovery_solver,
        "optimality_proven": recovery.get("solver_status") == "OPTIMAL",
        "verification_status": verification_status,
        "certificate": {"verification_status": verification_status,
                         "verified_for_demo": data.data_status != "VERIFIED",
                         "verified_for_real_planning": data.data_status == "VERIFIED",
                         "checks": contract.get("constraint_checks", [])},
        "warnings": (["DEMO_ONLY: this result is not field-verified."]
                     if data.data_status != "VERIFIED" else []),
        "result_summary": {
            "served_demand": damaged_served if damaged_served is not None else baseline_served,
            "total_demand": recovered_total,
            "weighted_walk_time_min": recovered_metrics.get("optimized_weighted_walk_time_min"),
            "optimized_served_demand": recovered_served,
            "diagnosed_unserved_nodes": (stages.get("diagnose") or {}).get("metrics", {}).get("diagnosed_unserved_nodes"),
            "baseline_served_demand": baseline_served,
            "damaged_served_demand": damaged_served,
            "recovered_served_demand": recovered_served,
        },
        "result_detail": detail,
        "trace": trace_rows,
        "provenance": {"source_hash": source_digest,
                       "source_manifest_hash": data.source_manifest_hash,
                       "runtime": {"python": platform.python_version(), "deterministic": True}},
        "cache_hit": any(row.get("cache_hit") for row in trace_rows),
        "recomputed_nodes": [row["skill_id"] for row in trace_rows if not row.get("cache_hit")],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _input_result_ids(stage_id: str, stages: dict[str, dict[str, Any]]) -> list[str]:
    prerequisites = {
        "baseline": [], "damage": ["baseline"], "diagnose": ["damage"],
        "recovery": ["damage", "diagnose"], "compare": ["baseline", "damage", "recovery"],
        "verify": ["baseline", "damage", "recovery", "compare"],
    }
    return [stages[key]["result_id"] for key in prerequisites.get(stage_id, [])
            if stages.get(key, {}).get("result_id")]


def _source_disclosure(data_status: str) -> str | None:
    if data_status == "SYNTHETIC_FIXTURE":
        return ("SYNTHETIC_FIXTURE: this is a test fixture, not Module A data "
                "and not a real-world planning result.")
    if data_status == "DEMO_ONLY":
        return "DEMO_ONLY: this model result is not operationally or field verified."
    return None
