"""Pure A-side evaluator for normalized task evidence.

The evaluator accepts evidence records rather than calling any transport,
solver, LLM, or Agent.  It is therefore usable for GT regression tests and
for scoring externally produced runs without confusing B execution with Agent
performance.
"""
from __future__ import annotations

from typing import Any


STATUSES = {"PASS", "FAIL", "UNSCORABLE", "NOT_RUN", "INFRA_ERROR"}


def _expected_error(task: dict[str, Any]) -> set[str]:
    return set(((task.get("gt_assertions") or {}).get("reject_contract") or {}).get("expected_error_codes") or [])


def _gt(task: dict[str, Any]) -> dict[str, Any]:
    return task.get("gt_assertions") or {}


def _lineage_ok(task: dict[str, Any], output: dict[str, Any]) -> tuple[bool, str]:
    if not _gt(task).get("parent_state_required"):
        return True, "not_required"
    lineage = output.get("state_lineage") or {}
    if not lineage.get("parent_trace_present") or not lineage.get("child_trace_present"):
        return False, "parent_and_child_trace_required"
    if lineage.get("child_parent_scenario_id") in {None, "", lineage.get("parent_scenario_id")}:
        return False, "child_parent_scenario_id_missing_or_not_runtime_bound"
    if lineage.get("scenario_hash_match") is not True:
        return False, "parent_child_scenario_hash_mismatch"
    if lineage.get("region_match") is not True or lineage.get("data_version_match") is not True:
        return False, "parent_child_identity_mismatch"
    return True, "passed"


def _constraint_fidelity(task: dict[str, Any], output: dict[str, Any]) -> tuple[float, list[str]]:
    gt = _gt(task)
    failures: list[str] = []
    checks = 0
    passed = 0
    for key in ("region_id", "data_version"):
        checks += 1
        if output.get(key) == gt.get(key):
            passed += 1
        else:
            failures.append(f"{key}_mismatch")
    checks += 1
    selected = set(output.get("selected_option_ids") or [])
    allowed = set(gt.get("allowed_option_ids") or [])
    if selected <= allowed:
        passed += 1
    else:
        failures.append("selected_option_outside_gt_candidates")
    checks += 1
    disabled = set(output.get("disabled_shelter_ids") or [])
    if disabled == set(gt.get("disabled_shelter_ids") or []):
        passed += 1
    else:
        failures.append("disabled_shelter_state_mismatch")
    return (passed / checks if checks else 0.0), failures


def _oracle_score(output: dict[str, Any]) -> tuple[float | None, str]:
    status = str(output.get("oracle_status") or "NOT_RUN")
    observed = output.get("served_demand")
    reference = output.get("oracle_served_demand")
    if observed is not None and reference is not None:
        try:
            gap = float(reference) - float(observed)
            tolerance = float(output.get("oracle_tolerance") or 1e-6)
            return (1.0 if abs(gap) <= tolerance else 0.0), f"gap={gap:.6f}"
        except (TypeError, ValueError):
            return None, "non_numeric_oracle_values"
    if status in {"INDEPENDENT_ORACLE_PROVEN", "CONDITIONAL_SUBSET_OPTIMUM"}:
        return None, "oracle_status_present_without_comparable_served_value"
    return None, status


def evaluate_task(task: dict[str, Any], output: dict[str, Any] | None) -> dict[str, Any]:
    """Evaluate one task without invoking external code.

    Output records use normalized evidence keys: ``behavior``, ``status``,
    ``solver_started``, ``error_code``, ``trace_present``, ``verified``,
    ``state_lineage`` and optional resource metrics.
    """
    expected = str(task.get("expected_behavior") or _gt(task).get("expected_behavior") or "")
    output = output or {}
    run_status = str(output.get("status") or "NOT_RUN")
    behavior = str(output.get("behavior") or "")
    if run_status in {"NOT_RUN", "PENDING"} or not output:
        status = "NOT_RUN"
        reason = "no normalized execution evidence"
    elif run_status in {"INFRA_ERROR", "TIMEOUT"}:
        status = "INFRA_ERROR"
        reason = "execution infrastructure did not produce a scoreable result"
    elif expected in {"REJECT", "CLARIFY"}:
        if behavior != expected:
            status, reason = "FAIL", f"expected behavior {expected}, observed {behavior or 'NONE'}"
        elif expected == "REJECT":
            error_ok = bool(_expected_error(task) & {str(output.get("error_code") or "")})
            solver_ok = output.get("solver_started") is False
            status = "PASS" if error_ok and solver_ok else "FAIL"
            reason = "reject contract passed" if status == "PASS" else "reject error/solver contract failed"
        else:
            status = "PASS" if output.get("solver_started") is False else "FAIL"
            reason = "clarification contract passed" if status == "PASS" else "clarification must stop before solver"
    elif expected == "SAFE_STOP":
        if behavior != "SAFE_STOP":
            status, reason = "FAIL", f"expected SAFE_STOP, observed {behavior or 'NONE'}"
        elif output.get("safe_stop_supported") is not True:
            status, reason = "UNSCORABLE", "capacity/reachability evidence was not supplied or did not support SAFE_STOP"
        else:
            status, reason = "PASS", "SAFE_STOP supported by supplied A-side evidence"
    elif expected == "EXECUTE":
        if behavior and behavior != "EXECUTE":
            status, reason = "FAIL", f"expected EXECUTE, observed {behavior}"
        elif run_status != "SUCCEEDED":
            status, reason = "INFRA_ERROR" if run_status == "FAILED" else "NOT_RUN", "execution did not complete"
        elif output.get("solver_started") is not True:
            status, reason = "FAIL", "EXECUTE evidence does not show Solver start"
        elif output.get("trace_present") is not True:
            status, reason = "FAIL", "complete execution requires a trace"
        elif output.get("verified") is not True:
            status, reason = "FAIL", "complete execution requires independent verification evidence"
        else:
            lineage_ok, lineage_reason = _lineage_ok(task, output)
            if not lineage_ok:
                status, reason = "FAIL", lineage_reason
            else:
                fidelity, failures = _constraint_fidelity(task, output)
                status = "PASS" if fidelity == 1.0 else "FAIL"
                reason = "execute hard gate passed" if status == "PASS" else "; ".join(failures)
    else:
        status, reason = "UNSCORABLE", f"unsupported GT behavior: {expected or 'EMPTY'}"

    fidelity, fidelity_failures = _constraint_fidelity(task, output) if output else (None, [])
    oracle_score, oracle_reason = _oracle_score(output)
    evidence_score = None
    if output:
        evidence_checks = [output.get("trace_present") is True, output.get("verified") is True]
        evidence_score = sum(evidence_checks) / len(evidence_checks)
    utility = 1.0 if status == "PASS" else (0.0 if status == "FAIL" else None)
    components = [value for value in (utility, fidelity, oracle_score, evidence_score) if value is not None]
    aggregate = sum(components) / len(components) if components and status == "PASS" else None
    return {
        "status": status if status in STATUSES else "UNSCORABLE",
        "reason": reason,
        "task_id": task.get("task_id"),
        "expected_behavior": expected,
        "observed_behavior": behavior or None,
        "hard_gate_passed": status == "PASS",
        "dimensions": {"U": utility, "W": fidelity, "P": oracle_score, "E": evidence_score},
        "aggregate_score": aggregate,
        "oracle": {"status": output.get("oracle_status", "NOT_RUN"), "comparison": oracle_reason},
        "failure_categories": fidelity_failures if status == "FAIL" else [],
        "resource_usage": {
            "token_count": output.get("token_count"),
            "tool_calls": output.get("tool_calls"),
            "runtime_ms": output.get("runtime_ms"),
            "cost_usd": output.get("cost_usd"),
        },
    }


def aggregate_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: sum(row.get("status") == status for row in rows) for status in sorted(STATUSES)}
    scoreable = [row for row in rows if row.get("status") in {"PASS", "FAIL"}]
    dimension_scores = {
        key: [row["dimensions"][key] for row in scoreable if row.get("dimensions", {}).get(key) is not None]
        for key in ("U", "W", "P", "E")
    }
    return {
        "task_count": len(rows),
        "status_counts": counts,
        "verified_end_to_end_success_rate": counts["PASS"] / len(rows) if rows else None,
        "scoreable_success_rate": counts["PASS"] / len(scoreable) if scoreable else None,
        "dimension_means": {key: sum(values) / len(values) if values else None for key, values in dimension_scores.items()},
        "unscorable_count": counts["UNSCORABLE"] + counts["NOT_RUN"] + counts["INFRA_ERROR"],
        "resource_totals": {
            key: sum((row.get("resource_usage", {}).get(key) or 0) for row in rows)
            for key in ("token_count", "tool_calls", "runtime_ms", "cost_usd")
        },
        "note": "This evaluator scores supplied normalized evidence; it does not create Agent or LLM results.",
    }
