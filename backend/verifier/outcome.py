"""Outcome and explanation contract checks independent of solver claims."""
from __future__ import annotations

from math import isclose


def verify_result_identity(expected_result_id: str, returned_result_id: str) -> dict:
    return {"check_id": "RESULT_IDENTITY", "passed": expected_result_id == returned_result_id, "observed": returned_result_id, "limit": expected_result_id}


def verify_summary_claims(summary: dict, claims: dict, tolerance: float = 1e-6) -> list[dict]:
    checks = []
    for key in ("served_demand", "total_demand", "weighted_walk_time_min"):
        if key not in claims:
            continue
        expected, actual = summary.get(key), claims[key]
        if expected is None or actual is None:
            passed = expected is actual
        else:
            try:
                passed = isclose(float(expected), float(actual), rel_tol=tolerance, abs_tol=tolerance)
            except (TypeError, ValueError):
                passed = False
        checks.append({"check_id": f"CLAIM_{key.upper()}", "passed": passed, "observed": actual, "limit": expected})
    return checks


def verify_assumptions_disclosed(required: list[str], disclosed: list[str]) -> dict:
    missing = sorted(set(required) - set(disclosed))
    return {"check_id": "ASSUMPTIONS_DISCLOSED", "passed": not missing, "observed": disclosed, "limit": required, "missing": missing}
