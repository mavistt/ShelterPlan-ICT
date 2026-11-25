"""Pre-flight and post-flight guards.

Guards are the only place allowed to say "no". They never compute planning
numbers: they validate shapes, ranges, versions, admission status and the
correspondence between a result and the scenario that produced it.
"""
from __future__ import annotations

from math import isfinite
import re
from typing import Any, Iterable

from agent.config import HarnessConfig
from agent.contracts import SCENARIO_CHANGE_KEYS, ScenarioState
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
from agent.harness.session import Session

TASK_TYPES = (
    "assess", "optimize", "diagnose_intervene", "stress_test",
    "compare", "sensitivity", "explain_result", "disaster_recovery",
)
PARAMETER_ALLOWLIST = {
    "assess": set(),
    "optimize": {"budget_units"},
    "diagnose_intervene": {"budget_units"},
    "stress_test": {"disabled_shelter_ids", "budget_units"},
    "compare": {"scenarios"},
    "sensitivity": {"parameter", "values"},
    "explain_result": set(),
    "disaster_recovery": set(),
}
SENSITIVITY_FIELDS = {"budget_units", "population_demand_ratio", "max_walk_time_min"}
MAX_BUDGET = 1_000_000_000.0
MAX_WALK_MIN = 600.0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


def validate_changes(changes: dict[str, Any] | None) -> dict[str, Any]:
    changes = dict(changes or {})
    unknown = set(changes) - set(SCENARIO_CHANGE_KEYS)
    if unknown:
        raise GuardInvalidParam(
            f"Unsupported scenario change fields: {sorted(unknown)}",
            details={"unsupported": sorted(unknown), "allowed": list(SCENARIO_CHANGE_KEYS)},
        )
    if "population_demand_ratio" in changes:
        value = changes["population_demand_ratio"]
        if not _is_number(value) or not (0.0 <= float(value) <= 1.0):
            raise GuardInvalidParam(
                "population_demand_ratio must be a number in [0, 1].",
                details={"field": "population_demand_ratio", "value": value},
            )
    if "max_walk_time_min" in changes:
        value = changes["max_walk_time_min"]
        if not _is_number(value) or not (0.0 < float(value) <= MAX_WALK_MIN):
            raise GuardInvalidParam(
                f"max_walk_time_min must be a number in (0, {MAX_WALK_MIN:g}] minutes.",
                details={"field": "max_walk_time_min", "value": value},
            )
    if "budget_units" in changes:
        value = changes["budget_units"]
        if value is not None and (not _is_number(value) or not (0.0 <= float(value) <= MAX_BUDGET)):
            raise GuardInvalidParam(
                "budget_units must be null or a finite number in [0, 1e9].",
                details={"field": "budget_units", "value": value},
            )
    if "disabled_shelter_ids" in changes:
        value = changes["disabled_shelter_ids"]
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise GuardInvalidParam(
                "disabled_shelter_ids must be a list of shelter id strings.",
                details={"field": "disabled_shelter_ids"},
            )
        if len(value) > 500:
            raise GuardInvalidParam("disabled_shelter_ids exceeds 500 entries.")
        if len(set(value)) != len(value):
            raise GuardInvalidParam("disabled_shelter_ids contains duplicates.")
    return changes


def validate_improvement_options(options: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = list(options or [])
    if len(rows) > 12:
        raise GuardInvalidOption("Module B accepts at most 12 improvement options per task.",
                                 details={"count": len(rows)})
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise GuardInvalidOption("Each improvement option must be an object.")
        option_id = row.get("option_id")
        if not isinstance(option_id, str) or not option_id:
            raise GuardInvalidOption("Each improvement option needs a non-empty option_id.")
        if option_id in seen:
            raise GuardInvalidOption(f"Duplicate option_id: {option_id}", details={"option_id": option_id})
        seen.add(option_id)
        for field in ("delta_capacity_persons", "cost_units"):
            value = row.get(field)
            if not _is_number(value) or float(value) < 0:
                raise GuardInvalidOption(
                    f"{field} must be a finite non-negative number.",
                    details={"option_id": option_id, "field": field, "value": value},
                )
        if row.get("hypothetical") is False and row.get("human_confirmed") is False:
            raise GuardInvalidOption(
                "An option must be either hypothetical or human-confirmed.",
                details={"option_id": option_id},
            )
        if row.get("combination_policy") == "exclusive_group" and not row.get("exclusive_group"):
            raise GuardInvalidOption(
                "exclusive_group policy requires an exclusive_group name.",
                details={"option_id": option_id},
            )
    return rows


def preflight(
    *,
    task_type: str,
    state: ScenarioState,
    config: HarnessConfig,
    session: Session,
    parameters: dict[str, Any] | None = None,
    improvement_options: Iterable[dict[str, Any]] | None = None,
    result_id: str | None = None,
) -> dict[str, Any]:
    """Validate everything that can be judged without calling Module B."""
    if task_type not in TASK_TYPES:
        raise GuardInvalidParam(f"Unknown task_type: {task_type}", details={"allowed": list(TASK_TYPES)})

    parameters = dict(parameters or {})
    unknown = set(parameters) - PARAMETER_ALLOWLIST[task_type]
    if unknown:
        raise GuardInvalidParam(
            f"Task parameters not allowed for {task_type}: {sorted(unknown)}",
            details={"unsupported": sorted(unknown), "allowed": sorted(PARAMETER_ALLOWLIST[task_type])},
        )

    # Version and admission -------------------------------------------------
    if session.defaults.region_id is not None or state.params.region_id is not None:
        if state.params.region_id != session.defaults.region_id:
            raise GuardVersionMismatch(
                "Scenario region_id does not match the session's bound region.",
                details={
                    "expected_region_id": session.defaults.region_id,
                    "received_region_id": state.params.region_id,
                },
            )
        expected_version = session.defaults.data_version
    else:
        expected_version = config.data_version

    if state.params.data_version != expected_version:
        raise GuardVersionMismatch(
            "Scenario data_version does not match the loaded Module A source.",
            details={"expected": expected_version, "received": state.params.data_version},
        )
    if state.params.analysis_mode == "verified" and config.expected_data_status != "VERIFIED":
        raise GuardNotVerifiedSource(
            "Verified planning is blocked: the loaded source is not an accepted Module A release.",
            details={"data_status": config.expected_data_status, "analysis_mode": state.params.analysis_mode},
        )

    # Business parameters ---------------------------------------------------
    if task_type in {"optimize"}:
        budget = parameters.get("budget_units", state.params.budget_units)
        if budget is None:
            raise GuardMissingParam(
                "A confirmed budget is required before optimizing improvements.",
                details={"field": "budget_units"},
            )
        if not _is_number(budget) or not (0.0 <= float(budget) <= MAX_BUDGET):
            raise GuardInvalidParam("budget_units must be a finite number in [0, 1e9].",
                                    details={"field": "budget_units", "value": budget})

    if task_type == "stress_test":
        disabled = parameters.get("disabled_shelter_ids", list(state.params.disabled_shelter_ids))
        if not disabled:
            raise GuardMissingParam(
                "A stress test needs at least one disabled shelter id.",
                details={"field": "disabled_shelter_ids"},
            )
        parameters["disabled_shelter_ids"] = list(disabled)

    if task_type == "disaster_recovery":
        disabled = list(state.params.disabled_shelter_ids)
        if not disabled:
            raise GuardMissingParam(
                "A disaster-recovery task needs at least one explicitly disabled shelter.",
                details={"field": "disabled_shelter_ids"},
            )
        budget = state.params.budget_units
        if budget is None:
            raise GuardMissingParam(
                "A confirmed budget is required before recovery optimization.",
                details={"field": "budget_units"},
            )
        if not _is_number(budget) or not (0.0 <= float(budget) <= MAX_BUDGET):
            raise GuardInvalidParam("budget_units must be a finite number in [0, 1e9].",
                                    details={"field": "budget_units", "value": budget})

    if task_type == "compare":
        scenarios = parameters.get("scenarios") or []
        if not isinstance(scenarios, list) or not (2 <= len(scenarios) <= 12):
            raise GuardInvalidParam(
                "compare requires 2-12 explicit scenario overrides.",
                details={"count": len(scenarios) if isinstance(scenarios, list) else None},
            )
        for row in scenarios:
            if not isinstance(row, dict) or set(row) - set(SCENARIO_CHANGE_KEYS):
                raise GuardInvalidParam(
                    "Each comparison override must use only the allowlisted scenario fields.",
                    details={"row": row if isinstance(row, dict) else str(row)},
                )
        parameters = {**parameters, "scenarios": scenarios}

    if task_type == "sensitivity":
        field = parameters.get("parameter")
        values = parameters.get("values")
        if field not in SENSITIVITY_FIELDS:
            raise GuardInvalidParam(
                "sensitivity parameter must be one of the allowlisted knobs.",
                details={"parameter": field, "allowed": sorted(SENSITIVITY_FIELDS)},
            )
        if not isinstance(values, list) or not (1 <= len(values) <= 20):
            raise GuardInvalidParam("sensitivity requires 1-20 values.", details={"count": None})
        if any(not _is_number(value) for value in values):
            raise GuardInvalidParam("sensitivity values must all be finite numbers.")

    if task_type == "explain_result":
        if not result_id:
            raise GuardMissingParam("explain_result requires a result_id.", details={"field": "result_id"})
        if not session.has_result(result_id):
            raise GuardUnknownResult(
                "result_id was not produced by this session.",
                details={"result_id": result_id, "known": session.known_result_ids()[-5:]},
            )

    options = validate_improvement_options(improvement_options)
    if options and task_type not in {"optimize", "diagnose_intervene", "stress_test", "sensitivity", "disaster_recovery"}:
        raise GuardInvalidOption(f"improvement_options are not used by task_type={task_type}.")

    if task_type == "disaster_recovery" and not options:
        raise GuardMissingParam(
            "At least one legal recovery expansion option is required.",
            details={"field": "improvement_options"},
        )
    if task_type == "disaster_recovery":
        disabled = set(state.params.disabled_shelter_ids)
        invalid = [row for row in options
                   if not row.get("eligible", True)
                   or not isinstance(row.get("shelter_id"), str)
                   or not row.get("shelter_id")
                   or row.get("shelter_id") in disabled
                   or (session.valid_shelter_ids is not None
                       and row.get("shelter_id") not in session.valid_shelter_ids)]
        if invalid:
            raise GuardInvalidOption(
                "Recovery options must be eligible and target an active shelter in this region.",
                details={"reason_code": "INVALID_RECOVERY_OPTIONS",
                         "invalid_option_ids": [row.get("option_id") for row in invalid],
                         "disabled_shelter_ids": sorted(disabled)},
            )

    if session.valid_shelter_ids is not None:
        disabled_check = list(state.params.disabled_shelter_ids)
        for sid in parameters.get("disabled_shelter_ids") or []:
            if sid not in disabled_check:
                disabled_check.append(sid)
        if task_type == "compare":
            for row in parameters.get("scenarios") or []:
                for sid in (row.get("disabled_shelter_ids") or []):
                    if sid not in disabled_check:
                        disabled_check.append(sid)
        unknown_shelters = [sid for sid in disabled_check if sid not in session.valid_shelter_ids]
        if unknown_shelters:
            raise GuardInvalidParam(
                "Shelter id does not belong to the bound region.",
                details={
                    "region_id": session.defaults.region_id,
                    "unknown_shelter_ids": unknown_shelters,
                },
            )
        for row in options:
            sid = row.get("shelter_id")
            if sid and sid not in session.valid_shelter_ids:
                raise GuardInvalidOption(
                    "Improvement option shelter_id does not belong to the bound region.",
                    details={
                        "region_id": session.defaults.region_id,
                        "option_id": row.get("option_id"),
                        "shelter_id": sid,
                    },
                )

    # Parent consistency ----------------------------------------------------
    if state.parent_scenario_id and session.find_scenario(state.parent_scenario_id) is None:
        raise GuardInvalidParam(
            "parent_scenario_id is not present in this session's history.",
            details={"parent_scenario_id": state.parent_scenario_id},
        )

    return {"parameters": parameters, "improvement_options": options, "result_id": result_id}


def postflight(
    *,
    full_result: dict[str, Any],
    state: ScenarioState,
    config: HarnessConfig,
    claim: str | None = None,
) -> dict[str, Any]:
    """Validate the result before any of it is shown to a user or a model."""
    if not isinstance(full_result, dict):
        raise ResultMismatch("Module B returned a non-object result document.")

    returned_scenario = full_result.get("scenario_id")
    if state.scenario_id != returned_scenario:
        raise ResultMismatch(
            "Returned result belongs to a different scenario.",
            details={"expected": state.scenario_id, "received": returned_scenario,
                     "result_id": full_result.get("result_id")},
        )
    returned_version = full_result.get("data_version")
    if returned_version != state.params.data_version:
        raise ResultMismatch(
            "Returned result was computed on a different data version.",
            details={"expected": state.params.data_version, "received": returned_version},
        )

    verification_status = full_result.get("verification_status")
    if verification_status not in {"PASS_DEMO", "PASS_VERIFIED"}:
        raise ResultMismatch(
            "Result is not publishable: verifier did not pass.",
            details={"verification_status": verification_status,
                     "checks_failed": _failed_check_ids(full_result)},
        )

    data_status = full_result.get("data_status")
    if state.params.analysis_mode == "verified" and data_status != "VERIFIED":
        raise GuardNotVerifiedSource(
            "A verified-mode request produced a demo-only result.",
            details={"data_status": data_status},
        )

    solver_status = full_result.get("solver_status")
    if solver_status in {"SOLVER_TIMEOUT", "SOLVER_ERROR", "NO_FEASIBLE_SOLUTION"}:
        raise ResultMismatch(
            "Solver did not produce a usable plan.",
            details={"solver_status": solver_status},
        )

    from agent.harness.results import resolve_optimality
    proven = bool(resolve_optimality(full_result))
    if claim == "optimal" and not proven:
        raise GuardClaimViolation(
            "The result cannot be described as proven optimal.",
            details={"solver_status": solver_status, "optimality_proven": proven},
        )

    return {
        "solver_status": solver_status,
        "optimality_proven": proven,
        "verification_status": verification_status,
        "data_status": data_status,
    }


def postflight_disaster_recovery(
    *, full_result: dict[str, Any], state: ScenarioState, config: HarnessConfig,
    session: Session, expected_baseline_disabled: Iterable[str] = (),
) -> dict[str, Any]:
    """Independent C-side check of B's stage chain before it is publishable."""
    if not isinstance(full_result, dict) or full_result.get("task_type") != "disaster_recovery":
        raise ResultMismatch("Module B returned a non-composite result for a recovery task.")
    if full_result.get("status") != "COMPLETED":
        raise ResultMismatch("Composite workflow did not complete all required stages.", details={
            "status": full_result.get("status"),
        })
    if full_result.get("region_id") != state.params.region_id:
        raise ResultMismatch("Composite result region differs from the bound C session.", details={
            "expected": state.params.region_id, "received": full_result.get("region_id"),
            "reason_code": "REGION_MISMATCH",
        })
    if full_result.get("data_version") != state.params.data_version:
        raise GuardVersionMismatch("Composite result data version differs from the C scenario.", details={
            "expected": state.params.data_version, "received": full_result.get("data_version"),
            "reason_code": "DATA_VERSION_MISMATCH",
        })
    if full_result.get("data_status") != config.expected_data_status:
        raise GuardVersionMismatch("Composite result source status differs from the admitted C source.", details={
            "expected": config.expected_data_status, "received": full_result.get("data_status"),
            "reason_code": "DATA_VERSION_MISMATCH",
        })
    if state.params.analysis_mode == "verified" and full_result.get("data_status") != "VERIFIED":
        raise GuardNotVerifiedSource("Composite result is not from an admitted verified source.")

    composite = ((full_result.get("result_detail") or {}).get("composite") or {})
    contract = composite.get("contract") or {}
    stage_rows = [row for row in contract.get("stage_results") or [] if isinstance(row, dict)]
    stage_order = [row.get("stage_id") for row in stage_rows]
    stages = {row.get("stage_id"): row for row in stage_rows}
    required = ("baseline", "damage", "diagnose", "recovery", "compare", "verify")
    missing = [stage for stage in required if stage not in stages]
    if missing:
        raise ResultMismatch("Composite result omitted required stage records.", details={
            "missing_stages": missing, "reason_code": "COMPOSITE_STAGE_MISMATCH",
        })
    if any(stages[stage].get("status") != "COMPLETED" for stage in required):
        raise ResultMismatch("Composite result contains a skipped or failed required stage.", details={
            "statuses": {stage: stages[stage].get("status") for stage in required},
            "reason_code": "STAGE_VERIFICATION_FAILED",
        })
    if stage_order != list(required):
        raise ResultMismatch("Composite stages were not executed in the registered order.", details={
            "expected": list(required), "received": stage_order,
            "reason_code": "COMPOSITE_STAGE_MISMATCH",
        })
    if contract.get("client_session_id") != session.session_id:
        raise ResultMismatch("Composite result is bound to another C session.", details={
            "expected_session_id": session.session_id,
            "received_session_id": contract.get("client_session_id"),
            "reason_code": "RESULT_BINDING_MISMATCH",
        })
    if (contract.get("task_id") != full_result.get("task_id")
            or contract.get("client_turn_id") != full_result.get("task_id")):
        raise ResultMismatch("Composite result is not bound to the submitted C turn.", details={
            "contract_task_id": contract.get("task_id"),
            "client_turn_id": contract.get("client_turn_id"),
            "result_task_id": full_result.get("task_id"),
            "reason_code": "RESULT_BINDING_MISMATCH",
        })
    if contract.get("data_version") != full_result.get("data_version"):
        raise ResultMismatch("Composite contract data version differs from its result.", details={
            "reason_code": "DATA_VERSION_MISMATCH",
        })
    if (not contract.get("source_manifest_digest")
            or contract.get("source_manifest_digest") != full_result.get("source_manifest_digest")):
        raise ResultMismatch("Composite result source digest is missing or inconsistent.", details={
            "reason_code": "RESULT_BINDING_MISMATCH",
        })
    expected_disabled = sorted(set(expected_baseline_disabled) | set(state.params.disabled_shelter_ids))
    baseline = stages["baseline"]
    damage = stages["damage"]
    diagnosis = stages["diagnose"]
    recovery = stages["recovery"]
    _assert_stage_parent(damage, baseline.get("scenario_id"), "DAMAGE_STATE_LOST")
    _assert_stage_parent(recovery, damage.get("scenario_id"), "DAMAGE_STATE_LOST")
    if sorted(baseline.get("effective_parameters", {}).get("disabled_shelter_ids") or []) != sorted(set(expected_baseline_disabled)):
        raise ResultMismatch("Baseline stage changed the inherited facility state.", details={
            "expected": sorted(set(expected_baseline_disabled)),
            "received": baseline.get("effective_parameters", {}).get("disabled_shelter_ids"),
            "reason_code": "DAMAGE_STATE_LOST",
        })
    if baseline.get("effective_parameters", {}).get("budget_units") is not None:
        raise ResultMismatch("Recovery budget leaked into the baseline stage.", details={
            "reason_code": "PARAMETER_DRIFT",
        })
    for stage_id in ("baseline", "damage", "recovery"):
        effective = stages[stage_id].get("effective_parameters") or {}
        if effective.get("region_id") != state.params.region_id:
            raise ResultMismatch("Scenario region drifted inside the composite workflow.", details={
                "stage_id": stage_id, "reason_code": "REGION_MISMATCH",
            })
        if effective.get("data_version") != state.params.data_version:
            raise ResultMismatch("Scenario data version drifted inside the composite workflow.", details={
                "stage_id": stage_id, "reason_code": "DATA_VERSION_MISMATCH",
            })
        if float(effective.get("population_demand_ratio", -1)) != float(state.params.population_demand_ratio):
            raise ResultMismatch("Demand ratio drifted between composite stages.", details={
                "stage_id": stage_id, "reason_code": "PARAMETER_DRIFT",
                "expected": state.params.population_demand_ratio,
                "received": effective.get("population_demand_ratio"),
            })
        if float(effective.get("max_walk_time_min", -1)) != float(state.params.max_walk_time_min):
            raise ResultMismatch("Walking threshold drifted between composite stages.", details={
                "stage_id": stage_id, "reason_code": "PARAMETER_DRIFT",
                "expected": state.params.max_walk_time_min,
                "received": effective.get("max_walk_time_min"),
            })
    if sorted(damage.get("effective_parameters", {}).get("disabled_shelter_ids") or []) != expected_disabled:
        raise ResultMismatch("Requested damaged shelters were not carried into the damage stage.", details={
            "expected": expected_disabled,
            "received": damage.get("effective_parameters", {}).get("disabled_shelter_ids"),
            "reason_code": "DAMAGE_STATE_LOST",
        })
    if sorted(recovery.get("effective_parameters", {}).get("disabled_shelter_ids") or []) != expected_disabled:
        raise ResultMismatch("Recovery stage silently changed disabled shelter state.", details={
            "expected": expected_disabled,
            "received": recovery.get("effective_parameters", {}).get("disabled_shelter_ids"),
            "reason_code": "DAMAGE_STATE_LOST",
        })
    if recovery.get("effective_parameters", {}).get("budget_units") != state.params.budget_units:
        raise ResultMismatch("Recovery stage did not receive the confirmed budget.", details={
            "expected": state.params.budget_units,
            "received": recovery.get("effective_parameters", {}).get("budget_units"),
            "reason_code": "PARAMETER_DRIFT",
        })
    result_ids = [stages[name].get("result_id") for name in ("baseline", "damage", "recovery")]
    if any(not value for value in result_ids) or len(set(result_ids)) != len(result_ids):
        raise ResultMismatch("Composite stage results are missing or ambiguously bound.", details={
            "result_ids": result_ids, "reason_code": "RESULT_BINDING_MISMATCH",
        })
    if diagnosis.get("result_id") != damage.get("result_id") or "S05" not in diagnosis.get("skill_ids", []):
        raise ResultMismatch("Diagnosis is not bound to the damaged scenario's S05 result.", details={
            "reason_code": "COMPOSITE_STAGE_MISMATCH",
        })
    expected_inputs = {
        "damage": [baseline.get("result_id")],
        "diagnose": [damage.get("result_id")],
        "recovery": [damage.get("result_id"), diagnosis.get("result_id")],
        "compare": [baseline.get("result_id"), damage.get("result_id"), recovery.get("result_id")],
        "verify": [baseline.get("result_id"), damage.get("result_id"),
                   recovery.get("result_id"), recovery.get("result_id")],
    }
    for stage_id, expected_ids in expected_inputs.items():
        if stages[stage_id].get("input_result_ids") != expected_ids:
            raise ResultMismatch("Composite stage has missing or misbound result inputs.", details={
                "stage_id": stage_id, "expected": expected_ids,
                "received": stages[stage_id].get("input_result_ids"),
                "reason_code": "RESULT_BINDING_MISMATCH",
            })
    for name in ("baseline", "damage", "recovery"):
        if stages[name].get("verification_status") not in {"PASS_DEMO", "PASS_VERIFIED"}:
            raise ResultMismatch("An executed stage did not pass its verifier.", details={
                "stage_id": name, "verification_status": stages[name].get("verification_status"),
                "reason_code": "STAGE_VERIFICATION_FAILED",
            })
    if full_result.get("scenario_id") != recovery.get("scenario_id"):
        raise ResultMismatch("Final result is not bound to the recovered scenario.", details={
            "reason_code": "RESULT_BINDING_MISMATCH",
        })
    if full_result.get("execution_trace_id") != contract.get("execution_trace_id"):
        raise ResultMismatch("Composite trace id differs from the submitted execution.", details={
            "reason_code": "RESULT_BINDING_MISMATCH",
        })
    status = full_result.get("verification_status")
    if status not in {"PASS_DEMO", "PASS_VERIFIED"}:
        raise ResultMismatch("Composite verifier blocked publication.", details={
            "verification_status": status, "reason_code": "STAGE_VERIFICATION_FAILED",
        })
    if full_result.get("solver_status") not in {"OPTIMAL", "FEASIBLE"}:
        raise ResultMismatch("Recovery solver did not return a publishable result.", details={
            "solver_status": full_result.get("solver_status"),
        })
    if bool(full_result.get("optimality_proven")) != (full_result.get("solver_status") == "OPTIMAL"):
        raise ResultMismatch("Optimality claim does not match the recovery solver status.", details={
            "solver_status": full_result.get("solver_status"),
            "optimality_proven": full_result.get("optimality_proven"),
        })
    submitted_options = contract.get("improvement_options") or []
    if len(submitted_options) > 12:
        raise ResultMismatch("Composite result exceeds the public recovery option limit.", details={
            "option_count": len(submitted_options), "limit": 12,
            "reason_code": "INVALID_RECOVERY_OPTIONS",
        })
    submitted_by_id = {row.get("option_id"): row for row in submitted_options if isinstance(row, dict)}
    recovery_metrics = recovery.get("metrics") or {}
    selected_ids = recovery_metrics.get("selected_option_ids") or []
    if any(option_id not in submitted_by_id for option_id in selected_ids):
        raise ResultMismatch("Recovery selected an option that was not submitted by the user.", details={
            "selected_option_ids": selected_ids,
            "submitted_option_ids": list(submitted_by_id),
            "reason_code": "INVALID_RECOVERY_OPTIONS",
        })
    total_cost = recovery_metrics.get("total_cost_units")
    if (not _is_finite_num(total_cost) or total_cost > float(state.params.budget_units) + 1e-6
            or abs(float(total_cost) - sum(float(submitted_by_id[key]["cost_units"])
                                           for key in selected_ids)) > 1e-6):
        raise ResultMismatch("Recovery cost does not match selected options and the confirmed budget.", details={
            "budget_units": state.params.budget_units, "total_cost_units": total_cost,
            "selected_option_ids": selected_ids,
            "reason_code": "INVALID_RECOVERY_OPTIONS",
        })
    _assert_composite_comparison(full_result, baseline, damage, recovery, state.params.budget_units)
    return {"solver_status": full_result["solver_status"],
            "optimality_proven": bool(full_result.get("optimality_proven")),
            "verification_status": status,
            "data_status": full_result.get("data_status")}


def _is_finite_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


def _assert_composite_comparison(full_result: dict[str, Any], baseline: dict[str, Any],
                                 damage: dict[str, Any], recovery: dict[str, Any],
                                 budget_units: float | None) -> None:
    composite = ((full_result.get("result_detail") or {}).get("composite") or {})
    comparison = composite.get("comparison") or {}
    rows = {key: comparison.get(key) or {} for key in ("baseline", "damaged", "recovered")}
    expected = {
        "baseline": (baseline.get("metrics") or {}).get("served_demand"),
        "damaged": (damage.get("metrics") or {}).get("served_demand"),
        "recovered": (recovery.get("metrics") or {}).get("optimized_served_demand"),
    }
    for key, value in expected.items():
        observed = (rows[key].get("optimized_served_demand") if key == "recovered"
                    else rows[key].get("served_demand"))
        if not _is_finite_num(value) or not _is_finite_num(observed) or abs(float(value) - float(observed)) > 1e-6:
            raise ResultMismatch("Composite comparison metric does not match its bound stage result.", details={
                "stage": key, "expected": value, "received": observed,
                "reason_code": "RESULT_BINDING_MISMATCH",
            })
    baseline_served = float(expected["baseline"])
    damaged_served = float(expected["damaged"])
    recovered_served = float(expected["recovered"])
    loss, gain = baseline_served - damaged_served, recovered_served - damaged_served
    if (not _is_finite_num(comparison.get("damage_service_loss"))
            or abs(float(comparison["damage_service_loss"]) - loss) > 1e-6
            or not _is_finite_num(comparison.get("recovery_service_gain"))
            or abs(float(comparison["recovery_service_gain"]) - gain) > 1e-6
            or comparison.get("budget_units") != budget_units):
        raise ResultMismatch("Composite comparison deltas or budget do not match stage results.", details={
            "reason_code": "RESULT_BINDING_MISMATCH",
        })
    ratio = comparison.get("recovery_ratio") or {}
    if loss > 1e-6:
        if (ratio.get("status") != "APPLICABLE" or not _is_finite_num(ratio.get("value"))
                or abs(float(ratio["value"]) - gain / loss) > 1e-6):
            raise ResultMismatch("Recovery ratio is inconsistent with the measured service loss and gain.", details={
                "reason_code": "RESULT_BINDING_MISMATCH",
            })
    elif ratio.get("status") != "NOT_APPLICABLE" or ratio.get("value") is not None:
        raise ResultMismatch("Recovery ratio must be not applicable when no service loss occurred.", details={
            "reason_code": "RESULT_BINDING_MISMATCH",
        })


def _assert_stage_parent(stage: dict[str, Any], expected: str | None, reason_code: str) -> None:
    if stage.get("parent_scenario_id") != expected:
        raise ResultMismatch("Composite stage has invalid scenario lineage.", details={
            "stage_id": stage.get("stage_id"), "expected_parent": expected,
            "received_parent": stage.get("parent_scenario_id"), "reason_code": reason_code,
        })


def _failed_check_ids(full_result: dict[str, Any]) -> list[str]:
    certificate = full_result.get("certificate") or {}
    checks = certificate.get("checks") or []
    return [row.get("check_id") for row in checks if isinstance(row, dict) and not row.get("passed")][:10]


# --------------------------------------------------------------------------- #
# Claim checking: the explanation must quote the service, not the model.
# --------------------------------------------------------------------------- #
def check_claims(summary: dict[str, Any], claims: dict[str, Any]) -> list[dict[str, Any]]:
    try:  # reuse Module B's helper when the repository is importable
        from backend.verifier.outcome import verify_summary_claims  # type: ignore
        return verify_summary_claims(summary, claims)
    except Exception:  # pragma: no cover - fallback for a detached install
        rows = []
        for key in ("served_demand", "total_demand", "weighted_walk_time_min"):
            if key not in claims:
                continue
            expected, actual = summary.get(key), claims[key]
            try:
                passed = abs(float(expected) - float(actual)) <= max(1e-6, abs(float(expected)) * 1e-6)
            except (TypeError, ValueError):
                passed = expected is actual
            rows.append({"check_id": f"CLAIM_{key.upper()}", "passed": passed,
                         "observed": actual, "limit": expected})
        return rows


def assert_claims_consistent(summary: dict[str, Any], claims: dict[str, Any] | None) -> None:
    if not claims:
        return
    results = check_claims(summary, claims)
    bad = [row for row in results if not row.get("passed")]
    if bad:
        raise GuardClaimViolation(
            "Explanation contains numbers that do not match the verified result.",
            details={"mismatches": bad, "allowed_summary": {
                key: summary.get(key) for key in
                ("served_demand", "total_demand", "weighted_walk_time_min")}},
        )


def assert_demo_disclosure(full_result: dict[str, Any], text: str | None) -> None:
    """A DEMO_ONLY result must never be described as field-verified."""
    if not text:
        return
    if full_result.get("data_status") != "DEMO_ONLY":
        return
    cleaned = re.sub(
        r"(?:未经(?:真实)?|未经过(?:真实)?|未|非|不是|不可|不得|不能)\s*"
        r"(?:真实验证|已核验|现场核实|现场核验|可执行建议|可直接执行|现场应急指引)",
        "",
        text,
    )
    forbidden = (
        "真实验证", "已核验", "现场核实", "现场核验", "可执行建议",
        "可直接执行", "现场应急指引", "operationally verified",
    )
    hits = [token for token in forbidden if token in cleaned]
    if hits:
        raise GuardClaimViolation(
            "A DEMO_ONLY result cannot be described as verified or operational.",
            details={"matched": hits, "data_status": "DEMO_ONLY"},
        )
