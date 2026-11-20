"""Pre-flight and post-flight guards.

Guards are the only place allowed to say "no". They never compute planning
numbers: they validate shapes, ranges, versions, admission status and the
correspondence between a result and the scenario that produced it.
"""
from __future__ import annotations

from math import isfinite
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
    "compare", "sensitivity", "explain_result",
)
PARAMETER_ALLOWLIST = {
    "assess": set(),
    "optimize": {"budget_units"},
    "diagnose_intervene": {"budget_units"},
    "stress_test": {"disabled_shelter_ids", "budget_units"},
    "compare": {"scenarios"},
    "sensitivity": {"parameter", "values"},
    "explain_result": set(),
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
    if options and task_type not in {"optimize", "diagnose_intervene", "stress_test", "sensitivity"}:
        raise GuardInvalidOption(f"improvement_options are not used by task_type={task_type}.")

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
    forbidden = ("真实验证", "已核验", "现场核实", "可执行建议", "operationally verified")
    hits = [token for token in forbidden if token in text]
    if hits:
        raise GuardClaimViolation(
            "A DEMO_ONLY result cannot be described as verified or operational.",
            details={"matched": hits, "data_status": "DEMO_ONLY"},
        )
