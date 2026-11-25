"""Counterfactual/metamorphic checks with explicit applicability conditions."""
from __future__ import annotations


def verify_monotonicity(
    baseline: dict,
    changed: dict,
    *,
    relation: str,
    same_data_version: bool,
    both_optimal: bool,
    parameter_change_valid: bool,
    tolerance: float = 1e-6,
) -> dict:
    """Check monotonicity only when both results prove the same model objective."""
    if relation not in {"nondecreasing", "nonincreasing"}:
        raise ValueError("unsupported monotonicity relation")
    applicable = same_data_version and both_optimal and parameter_change_valid
    left, right = float(baseline["served_demand"]), float(changed["served_demand"])
    passed = None
    if applicable:
        passed = right + tolerance >= left if relation == "nondecreasing" else right <= left + tolerance
    return {
        "check_id": "COUNTERFACTUAL_MONOTONICITY",
        "applicable": applicable,
        "passed": passed,
        "observed": {"baseline": left, "changed": right},
        "limit": relation,
        "preconditions": {"same_data_version": same_data_version, "both_optimal": both_optimal, "parameter_change_valid": parameter_change_valid},
        "interpretation": "Model-relative comparison only; it is not real-world causal evidence.",
    }
