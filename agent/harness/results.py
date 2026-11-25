"""Result projection and bounded evidence access.

Module B returns 5-11 MB documents (8k+ verifier checks). A model context can
never see that. Everything exposed here is a projection; the full document stays
on disk under Module B's own `outputs/module_b/runs/`.
"""
from __future__ import annotations

import json
from typing import Any

from agent.config import HarnessConfig


def _is_finite_num(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def _verify_monotonicity(
    baseline: dict[str, Any],
    changed: dict[str, Any],
    *,
    relation: str,
    same_data_version: bool,
    both_optimal: bool,
    parameter_change_valid: bool,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    try:
        from backend.verifier.counterfactual import verify_monotonicity  # type: ignore

        return verify_monotonicity(
            baseline,
            changed,
            relation=relation,
            same_data_version=same_data_version,
            both_optimal=both_optimal,
            parameter_change_valid=parameter_change_valid,
            tolerance=tolerance,
        )
    except Exception:  # pragma: no cover - detached fallback matching B's contract
        if relation not in {"nondecreasing", "nonincreasing"}:
            raise ValueError("unsupported monotonicity relation")
        applicable = same_data_version and both_optimal and parameter_change_valid
        left, right = float(baseline["served_demand"]), float(changed["served_demand"])
        passed = None
        if applicable:
            passed = (
                right + tolerance >= left
                if relation == "nondecreasing"
                else right <= left + tolerance
            )
        return {
            "check_id": "COUNTERFACTUAL_MONOTONICITY",
            "applicable": applicable,
            "passed": passed,
            "observed": {"baseline": left, "changed": right},
            "limit": relation,
            "preconditions": {
                "same_data_version": same_data_version,
                "both_optimal": both_optimal,
                "parameter_change_valid": parameter_change_valid,
            },
            "interpretation": "Model-relative comparison only; it is not real-world causal evidence.",
        }


def resolve_primary_stage_optimality(full_result: dict[str, Any]) -> dict[str, Any]:
    """Verify stage-1 (`maximize_served_demand`) optimality using native B evidence."""
    optimization = _dig(full_result, ("result_detail", "optimization"))
    if not isinstance(optimization, dict):
        return {
            "status": "UNKNOWN",
            "reason": "no_optimization_block",
            "stage_name": None,
            "stage_status": None,
            "stage_objective_value": None,
            "final_served_demand": None,
        }

    final_served = _dig(optimization, ("assignment", "served_demand"))
    if final_served is None:
        final_served = optimization.get("objective_value")
    final_num = float(final_served) if _is_finite_num(final_served) else None

    stages = optimization.get("mip_stages")
    if not isinstance(stages, list) or not stages:
        return {
            "status": "UNKNOWN",
            "reason": "missing_mip_stages",
            "stage_name": None,
            "stage_status": None,
            "stage_objective_value": None,
            "final_served_demand": final_num,
        }

    stage1 = next(
        (
            row
            for row in stages
            if isinstance(row, dict) and row.get("stage") == "maximize_served_demand"
        ),
        None,
    )
    if stage1 is None:
        return {
            "status": "UNKNOWN",
            "reason": "missing_maximize_served_demand_stage",
            "stage_name": None,
            "stage_status": None,
            "stage_objective_value": None,
            "final_served_demand": final_num,
        }

    s_status = stage1.get("status")
    s_proven = stage1.get("optimality_proven")
    stage1_obj = stage1.get("objective_value")
    if stage1_obj is None:
        stage1_obj = stage1.get("served_demand")
    if stage1_obj is None:
        stage1_obj = optimization.get("primary_stage_objective_value")
    stage1_num = float(stage1_obj) if _is_finite_num(stage1_obj) else None

    if (
        s_proven is False
        or (isinstance(s_status, int) and not isinstance(s_status, bool) and s_status != 0)
        or s_status == "TIME_LIMIT_NO_SOLVE"
    ):
        return {
            "status": "FALSE",
            "reason": "primary_stage_not_optimal",
            "stage_name": "maximize_served_demand",
            "stage_status": s_status,
            "stage_objective_value": stage1_num,
            "final_served_demand": final_num,
        }

    if s_proven is not True:
        return {
            "status": "UNKNOWN",
            "reason": "primary_stage_optimality_unrecorded",
            "stage_name": "maximize_served_demand",
            "stage_status": s_status,
            "stage_objective_value": stage1_num,
            "final_served_demand": final_num,
        }

    if stage1_num is None or final_num is None:
        return {
            "status": "UNKNOWN",
            "reason": "stage1_objective_value_missing_in_b_evidence",
            "stage_name": "maximize_served_demand",
            "stage_status": s_status,
            "stage_objective_value": stage1_num,
            "final_served_demand": final_num,
        }

    tol = max(1e-6, abs(stage1_num) * 1e-9)
    if abs(final_num - stage1_num) <= tol:
        return {
            "status": "TRUE",
            "reason": "verified_against_stage1_objective",
            "stage_name": "maximize_served_demand",
            "stage_status": s_status,
            "stage_objective_value": stage1_num,
            "final_served_demand": final_num,
        }
    return {
        "status": "FALSE",
        "reason": "final_served_diverges_from_stage1_objective",
        "stage_name": "maximize_served_demand",
        "stage_status": s_status,
        "stage_objective_value": stage1_num,
        "final_served_demand": final_num,
    }

SECTION_ALIASES = {
    "assignments": ("result_detail", "assignment", "assignments"),
    "deficits": ("result_detail", "assignment", "deficits"),
    "shelter_loads": ("result_detail", "assignment", "shelter_loads"),
    "diagnosis": ("result_detail", "diagnosis"),
    "checks": ("certificate", "checks"),
    "optimization_checks": ("optimization_certificate", "checks"),
    "trace": ("trace",),
    "compare_rows": ("result_detail", "comparison", "scenarios"),
    "sensitivity_points": ("result_detail", "sensitivity", "points"),
    "composite_stages": ("result_detail", "composite", "stage_results"),
    "constraint_checks": ("result_detail", "composite", "contract", "constraint_checks"),
    "branch_decisions": ("result_detail", "composite", "contract", "branch_decisions"),
}

MAX_WARNINGS = 5
MAX_WARNING_CHARS = 240


def json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _dig(value: Any, path: tuple[str, ...], default: Any = None) -> Any:
    cursor = value
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            return default
        cursor = cursor[key]
    return cursor


def resolve_optimality(full_result: dict[str, Any]) -> bool | None:
    """Module B does not put `optimality_proven` at the document root.

    It lives inside `result_detail.optimization` (budget tasks) or
    `result_detail.assignment` (pure assignment). Never infer optimality from
    `solver_status` alone - a time-limited incumbent is `FEASIBLE`, not `OPTIMAL`.
    """
    root = full_result.get("optimality_proven")
    if root is not None:
        return bool(root)
    for path in (
        ("result_detail", "optimization", "optimality_proven"),
        ("result_detail", "assignment", "optimality_proven"),
        ("result_detail", "stress", "fixed_plan_stress", "assignment", "optimality_proven"),
    ):
        value = _dig(full_result, path)
        if value is not None:
            return bool(value)
    return None


def _warning_list(full_result: dict[str, Any]) -> list[str]:
    rows = []
    for row in (full_result.get("warnings") or [])[:MAX_WARNINGS]:
        text = str(row)
        rows.append(text if len(text) <= MAX_WARNING_CHARS else text[:MAX_WARNING_CHARS] + "…")
    return rows


def _diagnosis_digest(full_result: dict[str, Any]) -> dict[str, Any] | None:
    rows = _dig(full_result, ("result_detail", "diagnosis"))
    if not isinstance(rows, list):
        return None
    by_cause: dict[str, int] = {}
    unserved_by_cause: dict[str, float] = {}
    for row in rows:
        cause = str(row.get("diagnosis", "UNKNOWN"))
        by_cause[cause] = by_cause.get(cause, 0) + 1
        unserved_by_cause[cause] = round(
            unserved_by_cause.get(cause, 0.0) + float(row.get("unserved_persons") or 0.0), 3)
    return {
        "unserved_node_count": len(rows),
        "by_cause": by_cause,
        "unserved_persons_by_cause": unserved_by_cause,
        "note": "Aggregated only; per-node evidence is available through the paginated evidence reader.",
    }


def _optimize_block(full_result: dict[str, Any]) -> dict[str, Any] | None:
    optimization = _dig(full_result, ("result_detail", "optimization"))
    if not isinstance(optimization, dict):
        return None
    opt_served = _dig(optimization, ("assignment", "served_demand"))
    base_served = _dig(full_result, ("result_summary", "served_demand"))
    delta_served = (
        float(opt_served) - float(base_served)
        if _is_finite_num(opt_served) and _is_finite_num(base_served)
        else None
    )
    primary_ev = resolve_primary_stage_optimality(full_result)
    selected_options = [
        {
            "option_id": row.get("option_id"),
            "shelter_id": row.get("shelter_id"),
            "delta_capacity_persons": row.get("delta_capacity_persons"),
            "cost_units": row.get("cost_units"),
            "combination_policy": row.get("combination_policy"),
            "exclusive_group": row.get("exclusive_group"),
            "hypothetical": row.get("hypothetical"),
        }
        for row in (optimization.get("selected_options") or [])
        if isinstance(row, dict)
    ]

    raw_greedy = optimization.get("greedy_comparison") or _dig(
        full_result, ("result_detail", "greedy_baseline")
    )
    if isinstance(raw_greedy, dict) and _is_finite_num(raw_greedy.get("served_demand")):
        g_served = float(raw_greedy["served_demand"])
        greedy_block: dict[str, Any] = {
            "status": "available",
            "greedy_served_demand": g_served,
            "mip_served_demand": float(opt_served) if _is_finite_num(opt_served) else None,
            "served_demand_gap": (
                float(opt_served) - g_served if _is_finite_num(opt_served) else None
            ),
            "greedy_selected_option_ids": list(raw_greedy.get("selected_option_ids") or []),
            "greedy_cost_units": raw_greedy.get("cost_units", raw_greedy.get("total_cost_units")),
        }
    else:
        greedy_block = {
            "status": "unavailable",
            "reason": "no_b_greedy_baseline_in_result",
        }

    stages = optimization.get("mip_stages") or []
    return {
        "selected_option_ids": list(optimization.get("selected_option_ids") or []),
        "selected_options": selected_options,
        "total_cost_units": optimization.get("total_cost_units"),
        "budget_units": optimization.get("budget_units"),
        "served_demand": opt_served,
        "baseline_served_demand": base_served,
        "served_demand_delta": delta_served,
        "total_demand": _dig(optimization, ("assignment", "total_demand")),
        "weighted_walk_time_min": _dig(optimization, ("assignment", "weighted_walk_time_min")),
        "solver": optimization.get("solver"),
        "mip_stage_count": len(stages),
        "all_stages_optimal": all(
            bool(stage.get("optimality_proven")) for stage in stages
        ) if stages else None,
        "primary_stage_optimal": primary_ev["status"],
        "primary_stage_evidence": primary_ev,
        "greedy_comparison": greedy_block,
    }


def _stress_block(full_result: dict[str, Any]) -> dict[str, Any] | None:
    stress = _dig(full_result, ("result_detail", "stress"))
    if not isinstance(stress, dict):
        return None
    fixed = stress.get("fixed_plan_stress") or {}
    adaptive = stress.get("adaptive_replan") or {}
    return {
        "stressed_scenario_id": stress.get("scenario_id"),
        "disabled_shelter_ids": _dig(stress, ("changes", "disabled_shelter_ids")),
        "fixed_plan": {
            "selected_option_ids": fixed.get("selected_option_ids") or [],
            "served_demand": _dig(fixed, ("assignment", "served_demand")),
            "verification_status": _dig(fixed, ("verification", "verification_status")),
        },
        "adaptive_replan": {
            "served_demand": _dig(adaptive, ("assignment", "served_demand")),
            "selected_option_ids": adaptive.get("selected_option_ids") or [],
            "verification_status": _dig(adaptive, ("verification", "verification_status")),
        } if adaptive else None,
        "comparison_semantics": stress.get("comparison_semantics"),
    }


def evaluate_series_monotonicity(
    points: list[dict[str, Any]],
    *,
    parameter: str | None,
    parent_optimal: bool,
) -> dict[str, Any]:
    """Tri-state monotonicity check (`PASS` | `FAIL` | `INCONCLUSIVE`) over a 1D sweep."""
    supported = {"budget_units", "max_walk_time_min", "population_demand_ratio"}
    if parameter not in supported:
        return {
            "status": "INCONCLUSIVE",
            "applicable": False,
            "passed": None,
            "parameter": parameter,
            "relation": None,
            "reason": "unsupported_parameter",
            "pairs_checked": 0,
            "violations": [],
        }
    relation = "nondecreasing"
    if not isinstance(points, list) or len(points) < 2:
        return {
            "status": "INCONCLUSIVE",
            "applicable": False,
            "passed": None,
            "parameter": parameter,
            "relation": relation,
            "reason": "insufficient_points",
            "pairs_checked": 0,
            "violations": [],
        }

    verified_statuses = {"PASS_DEMO", "PASS_VERIFIED"}
    parsed: list[tuple[float, int, dict[str, Any]]] = []
    for idx, row in enumerate(points):
        if not isinstance(row, dict):
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": parameter,
                "relation": relation,
                "reason": "invalid_point_entry",
                "pairs_checked": 0,
                "violations": [],
            }
        val = row.get("value")
        served = row.get("served_demand")
        if not (_is_finite_num(val) and _is_finite_num(served)):
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": parameter,
                "relation": relation,
                "reason": "non_numeric_point_values",
                "pairs_checked": 0,
                "violations": [],
            }
        if row.get("verification_status") not in verified_statuses:
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": parameter,
                "relation": relation,
                "reason": "point_not_verified",
                "pairs_checked": 0,
                "violations": [],
            }
        if "optimality_proven" in row and row.get("optimality_proven") is not None:
            if row.get("optimality_proven") is not True:
                return {
                    "status": "INCONCLUSIVE",
                    "applicable": False,
                    "passed": None,
                    "parameter": parameter,
                    "relation": relation,
                    "reason": "point_not_proven_optimal",
                    "pairs_checked": 0,
                    "violations": [],
                }
        elif row.get("primary_stage_optimal") == "TRUE":
            pass
        elif parameter == "budget_units":
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": parameter,
                "relation": relation,
                "reason": "point_optimality_unrecorded_for_budget_sensitivity",
                "pairs_checked": 0,
                "violations": [],
            }
        else:
            if row.get("solver_status") is not None and row.get("solver_status") != "OPTIMAL":
                return {
                    "status": "INCONCLUSIVE",
                    "applicable": False,
                    "passed": None,
                    "parameter": parameter,
                    "relation": relation,
                    "reason": "point_solver_not_optimal",
                    "pairs_checked": 0,
                    "violations": [],
                }
            if not parent_optimal:
                return {
                    "status": "INCONCLUSIVE",
                    "applicable": False,
                    "passed": None,
                    "parameter": parameter,
                    "relation": relation,
                    "reason": "parent_optimality_not_proven",
                    "pairs_checked": 0,
                    "violations": [],
                }
        parsed.append((float(val), idx, row))

    parsed.sort(key=lambda item: (item[0], item[1]))
    violations: list[dict[str, Any]] = []
    pairs_checked = 0
    for (v1, _i1, r1), (v2, _i2, r2) in zip(parsed, parsed[1:]):
        s1, s2 = float(r1["served_demand"]), float(r2["served_demand"])
        tol = max(1e-6, max(abs(s1), abs(s2)) * 1e-9)
        chk = _verify_monotonicity(
            {"served_demand": s1},
            {"served_demand": s2},
            relation=relation,
            same_data_version=True,
            both_optimal=True,
            parameter_change_valid=True,
            tolerance=tol,
        )
        pairs_checked += 1
        if not chk.get("passed"):
            violations.append({
                "left_value": v1,
                "right_value": v2,
                "left_served": s1,
                "right_served": s2,
                "tolerance": tol,
            })

    if violations:
        return {
            "status": "FAIL",
            "applicable": True,
            "passed": False,
            "parameter": parameter,
            "relation": relation,
            "reason": "monotonicity_violation_detected",
            "pairs_checked": pairs_checked,
            "violations": violations,
        }
    return {
        "status": "PASS",
        "applicable": True,
        "passed": True,
        "parameter": parameter,
        "relation": relation,
        "reason": "all_ordered_pairs_monotonic",
        "pairs_checked": pairs_checked,
        "violations": [],
    }


def evaluate_compare_monotonicity(
    scenarios: list[dict[str, Any]],
    *,
    data_version: str | None,
    baseline_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tri-state monotonicity check across `compare` scenario rows."""
    if not data_version or not isinstance(scenarios, list) or len(scenarios) < 2:
        return {
            "status": "INCONCLUSIVE",
            "applicable": False,
            "passed": None,
            "parameter": None,
            "relation": None,
            "reason": "insufficient_scenarios_or_missing_version",
            "pairs_checked": 0,
            "violations": [],
        }

    verified_statuses = {"PASS_DEMO", "PASS_VERIFIED"}
    all_keys: set[str] = set()
    for row in scenarios:
        if not isinstance(row, dict):
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": None,
                "relation": None,
                "reason": "invalid_scenario_entry",
                "pairs_checked": 0,
                "violations": [],
            }
        if row.get("solver_status") != "OPTIMAL" or row.get("verification_status") not in verified_statuses:
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": None,
                "relation": None,
                "reason": "scenario_not_optimal_or_verified",
                "pairs_checked": 0,
                "violations": [],
            }
        if not _is_finite_num(row.get("served_demand")):
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": None,
                "relation": None,
                "reason": "missing_served_demand",
                "pairs_checked": 0,
                "violations": [],
            }
        changes = row.get("changes")
        if not isinstance(changes, dict):
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": None,
                "relation": None,
                "reason": "missing_changes_dict",
                "pairs_checked": 0,
                "violations": [],
            }
        all_keys.update(changes.keys())

    if len(all_keys) != 1:
        return {
            "status": "INCONCLUSIVE",
            "applicable": False,
            "passed": None,
            "parameter": None,
            "relation": None,
            "reason": "multi_axis_or_empty_compare_not_comparable",
            "pairs_checked": 0,
            "violations": [],
        }

    axis = next(iter(all_keys))
    if axis not in {"max_walk_time_min", "population_demand_ratio", "disabled_shelter_ids"}:
        return {
            "status": "INCONCLUSIVE",
            "applicable": False,
            "passed": None,
            "parameter": axis,
            "relation": None,
            "reason": "unsupported_or_inactive_compare_axis",
            "pairs_checked": 0,
            "violations": [],
        }

    resolved_values: list[Any] = []
    for row in scenarios:
        changes = row["changes"]
        if axis in changes:
            resolved_values.append(changes[axis])
        elif isinstance(baseline_params, dict) and axis in baseline_params:
            resolved_values.append(baseline_params[axis])
        else:
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": axis,
                "relation": None,
                "reason": "missing_axis_value_in_scenario_changes",
                "pairs_checked": 0,
                "violations": [],
            }

    if axis in {"max_walk_time_min", "population_demand_ratio"}:
        if not all(_is_finite_num(v) for v in resolved_values):
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": axis,
                "relation": "nondecreasing",
                "reason": "non_numeric_compare_values",
                "pairs_checked": 0,
                "violations": [],
            }
        paired = sorted(
            [(float(v), idx, scenarios[idx]) for idx, v in enumerate(resolved_values)],
            key=lambda item: (item[0], item[1]),
        )
        violations: list[dict[str, Any]] = []
        for (v1, _i1, r1), (v2, _i2, r2) in zip(paired, paired[1:]):
            s1, s2 = float(r1["served_demand"]), float(r2["served_demand"])
            tol = max(1e-6, max(abs(s1), abs(s2)) * 1e-9)
            chk = _verify_monotonicity(
                {"served_demand": s1},
                {"served_demand": s2},
                relation="nondecreasing",
                same_data_version=True,
                both_optimal=True,
                parameter_change_valid=True,
                tolerance=tol,
            )
            if not chk.get("passed"):
                violations.append({
                    "left_value": v1,
                    "right_value": v2,
                    "left_served": s1,
                    "right_served": s2,
                    "tolerance": tol,
                })
        return {
            "status": "FAIL" if violations else "PASS",
            "applicable": True,
            "passed": not violations,
            "parameter": axis,
            "relation": "nondecreasing",
            "reason": "monotonicity_violation_detected" if violations else "all_ordered_pairs_monotonic",
            "pairs_checked": len(paired) - 1,
            "violations": violations,
        }

    # `disabled_shelter_ids`: comparable only when sets form a subset chain
    sets_indexed = []
    for idx, val in enumerate(resolved_values):
        if not isinstance(val, (list, tuple)):
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": axis,
                "relation": "nonincreasing",
                "reason": "invalid_disabled_shelter_list",
                "pairs_checked": 0,
                "violations": [],
            }
        sets_indexed.append((frozenset(str(x) for x in val), idx, scenarios[idx]))
    sets_indexed.sort(key=lambda item: (len(item[0]), item[1]))
    for (s1_set, _i1, _r1), (s2_set, _i2, _r2) in zip(sets_indexed, sets_indexed[1:]):
        if not (s1_set <= s2_set):
            return {
                "status": "INCONCLUSIVE",
                "applicable": False,
                "passed": None,
                "parameter": axis,
                "relation": "nonincreasing",
                "reason": "incomparable_disabled_shelter_sets",
                "pairs_checked": 0,
                "violations": [],
            }
    violations = []
    for (s1_set, _i1, r1), (s2_set, _i2, r2) in zip(sets_indexed, sets_indexed[1:]):
        d1, d2 = float(r1["served_demand"]), float(r2["served_demand"])
        tol = max(1e-6, max(abs(d1), abs(d2)) * 1e-9)
        chk = _verify_monotonicity(
            {"served_demand": d1},
            {"served_demand": d2},
            relation="nonincreasing",
            same_data_version=True,
            both_optimal=True,
            parameter_change_valid=True,
            tolerance=tol,
        )
        if not chk.get("passed"):
            violations.append({
                "left_value": sorted(s1_set),
                "right_value": sorted(s2_set),
                "left_served": d1,
                "right_served": d2,
                "tolerance": tol,
            })
    return {
        "status": "FAIL" if violations else "PASS",
        "applicable": True,
        "passed": not violations,
        "parameter": axis,
        "relation": "nonincreasing",
        "reason": "monotonicity_violation_detected" if violations else "all_ordered_pairs_monotonic",
        "pairs_checked": len(sets_indexed) - 1,
        "violations": violations,
    }


def evaluate_pair_monotonicity(
    parent_summary: dict[str, Any],
    current_summary: dict[str, Any],
    *,
    same_options: bool,
) -> dict[str, Any]:
    """Tri-state monotonicity check between two comparable parent/child scenario summaries."""
    def _inconclusive(reason: str, parameter: str | None = None, relation: str | None = None) -> dict[str, Any]:
        return {
            "status": "INCONCLUSIVE",
            "applicable": False,
            "passed": None,
            "parameter": parameter,
            "relation": relation,
            "reason": reason,
            "pairs_checked": 0,
            "violations": [],
        }

    if not isinstance(parent_summary, dict) or not isinstance(current_summary, dict):
        return _inconclusive("missing_summary")
    p_ver, c_ver = parent_summary.get("data_version"), current_summary.get("data_version")
    if not p_ver or p_ver != c_ver:
        return _inconclusive("data_version_mismatch")

    p_params = parent_summary.get("scenario_params") or {}
    c_params = current_summary.get("scenario_params") or {}
    if p_params.get("region_id") != c_params.get("region_id"):
        return _inconclusive("region_mismatch")
    if not same_options:
        return _inconclusive("options_differ")

    p_task, c_task = parent_summary.get("task_type"), current_summary.get("task_type")
    if p_task != c_task or p_task not in {"assess", "optimize"}:
        return _inconclusive("incomparable_task_type")

    if p_task == "optimize":
        p_opt = _dig(parent_summary, ("detail", "optimization")) or {}
        c_opt = _dig(current_summary, ("detail", "optimization")) or {}
        p_ok = bool(parent_summary.get("optimality_proven")) or p_opt.get("primary_stage_optimal") == "TRUE"
        c_ok = bool(current_summary.get("optimality_proven")) or c_opt.get("primary_stage_optimal") == "TRUE"
        p_served = _dig(parent_summary, ("metrics", "optimized_served_demand"))
        if p_served is None:
            p_served = p_opt.get("served_demand")
        c_served = _dig(current_summary, ("metrics", "optimized_served_demand"))
        if c_served is None:
            c_served = c_opt.get("served_demand")
    else:
        p_ok = bool(parent_summary.get("optimality_proven")) and parent_summary.get("solver_status") == "OPTIMAL"
        c_ok = bool(current_summary.get("optimality_proven")) and current_summary.get("solver_status") == "OPTIMAL"
        p_served = _dig(parent_summary, ("metrics", "served_demand"))
        c_served = _dig(current_summary, ("metrics", "served_demand"))

    if not (p_ok and c_ok):
        return _inconclusive("optimality_not_proven_for_both")
    if not (_is_finite_num(p_served) and _is_finite_num(c_served)):
        return _inconclusive("missing_served_demand")

    diff_keys: list[str] = []
    for key in ("population_demand_ratio", "max_walk_time_min", "budget_units", "disabled_shelter_ids"):
        pv, cv = p_params.get(key), c_params.get(key)
        if key == "disabled_shelter_ids":
            if frozenset(str(x) for x in (pv or [])) != frozenset(str(x) for x in (cv or [])):
                diff_keys.append(key)
        elif pv is None or cv is None:
            if pv is not cv:
                diff_keys.append(key)
        elif abs(float(pv) - float(cv)) > 1e-12:
            diff_keys.append(key)

    if len(diff_keys) != 1:
        return _inconclusive("zero_or_multiple_changed_knobs")

    knob = diff_keys[0]
    if knob == "budget_units" and p_task != "optimize":
        return _inconclusive("budget_not_active_for_assess", parameter=knob)

    p_s, c_s = float(p_served), float(c_served)
    tol = max(1e-6, max(abs(p_s), abs(c_s)) * 1e-9)

    if knob == "disabled_shelter_ids":
        p_set = frozenset(str(x) for x in (p_params.get(knob) or []))
        c_set = frozenset(str(x) for x in (c_params.get(knob) or []))
        if p_set < c_set:
            base_val, chg_val = {"served_demand": p_s}, {"served_demand": c_s}
            l_val, r_val = sorted(p_set), sorted(c_set)
        elif c_set < p_set:
            base_val, chg_val = {"served_demand": c_s}, {"served_demand": p_s}
            l_val, r_val = sorted(c_set), sorted(p_set)
        else:
            return _inconclusive("incomparable_disabled_shelter_sets", parameter=knob, relation="nonincreasing")
        relation = "nonincreasing"
    else:
        pv, cv = p_params.get(knob), c_params.get(knob)
        if not (_is_finite_num(pv) and _is_finite_num(cv)):
            return _inconclusive("non_numeric_knob_value", parameter=knob)
        pv_f, cv_f = float(pv), float(cv)
        if pv_f <= cv_f:
            base_val, chg_val = {"served_demand": p_s}, {"served_demand": c_s}
            l_val, r_val = pv_f, cv_f
        else:
            base_val, chg_val = {"served_demand": c_s}, {"served_demand": p_s}
            l_val, r_val = cv_f, pv_f
        relation = "nondecreasing"

    chk = _verify_monotonicity(
        base_val,
        chg_val,
        relation=relation,
        same_data_version=True,
        both_optimal=True,
        parameter_change_valid=True,
        tolerance=tol,
    )
    passed = bool(chk.get("passed"))
    return {
        "status": "PASS" if passed else "FAIL",
        "applicable": True,
        "passed": passed,
        "parameter": knob,
        "relation": relation,
        "reason": "pair_monotonic" if passed else "monotonicity_violation_detected",
        "pairs_checked": 1,
        "violations": [] if passed else [{
            "left_value": l_val,
            "right_value": r_val,
            "left_served": base_val["served_demand"],
            "right_served": chg_val["served_demand"],
            "tolerance": tol,
        }],
    }


def _compare_block(full_result: dict[str, Any], keep: int = 12, *,
                   baseline_params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    comparison = _dig(full_result, ("result_detail", "comparison"))
    if not isinstance(comparison, dict):
        return None
    raw_scenarios = comparison.get("scenarios") or []
    rows = []
    for row in raw_scenarios[:keep]:
        rows.append({
            "scenario_id": row.get("scenario_id"),
            "changes": row.get("changes"),
            "served_demand": row.get("served_demand"),
            "weighted_walk_time_min": row.get("weighted_walk_time_min"),
            "solver_status": row.get("solver_status"),
            "verification_status": row.get("verification_status"),
        })
    monotonicity = evaluate_compare_monotonicity(
        raw_scenarios,
        data_version=comparison.get("data_version") or full_result.get("data_version"),
        baseline_params=baseline_params,
    )
    return {
        "data_version": comparison.get("data_version"),
        "scenario_count": len(raw_scenarios),
        "scenarios": rows,
        "unique_recommendation": comparison.get("unique_recommendation"),
        "monotonicity_check": monotonicity,
        "note": "No forced winner: the service returns side-by-side rows only.",
    }


def _sensitivity_block(full_result: dict[str, Any], keep: int = 20) -> dict[str, Any] | None:
    sensitivity = _dig(full_result, ("result_detail", "sensitivity"))
    if not isinstance(sensitivity, dict):
        return None
    raw_points = sensitivity.get("points") or []
    parameter = sensitivity.get("parameter")
    points = []
    for row in raw_points[:keep]:
        points.append({
            "value": row.get("value"),
            "served_demand": row.get("served_demand"),
            "weighted_walk_time_min": row.get("weighted_walk_time_min"),
            "selected_option_ids": row.get("selected_option_ids"),
            "verification_status": row.get("verification_status"),
        })
    parent_optimal = bool(resolve_optimality(full_result))
    monotonicity = evaluate_series_monotonicity(
        raw_points,
        parameter=parameter,
        parent_optimal=parent_optimal,
    )
    return {
        "parameter": parameter,
        "point_count": len(points),
        "points": points,
        "monotonicity_check": monotonicity,
    }


def _intervention_block(full_result: dict[str, Any]) -> dict[str, Any] | None:
    intervention = _dig(full_result, ("result_detail", "intervention"))
    return intervention if isinstance(intervention, dict) else None


def _evidence_counts(full_result: dict[str, Any]) -> dict[str, int]:
    return {
        "assignments": len(_dig(full_result, ("result_detail", "assignment", "assignments")) or []),
        "deficits": len(_dig(full_result, ("result_detail", "assignment", "deficits")) or []),
        "diagnosis_rows": len(_dig(full_result, ("result_detail", "diagnosis")) or []),
        "certificate_checks": len(_dig(full_result, ("certificate", "checks")) or []),
        "trace_rows": len(full_result.get("trace") or []),
    }


def project(full_result: dict[str, Any], *, task_type: str,
            config: HarnessConfig | None = None,
            scenario_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a <= summary_max_bytes summary that is byte-identical in its numbers."""
    config = config or HarnessConfig()
    summary = full_result.get("result_summary") or {}
    metrics = {
        "served_demand": summary.get("served_demand"),
        "total_demand": summary.get("total_demand"),
        "weighted_walk_time_min": summary.get("weighted_walk_time_min"),
        "optimized_served_demand": summary.get("optimized_served_demand"),
        "diagnosed_unserved_nodes": summary.get("diagnosed_unserved_nodes"),
    }
    if task_type == "disaster_recovery":
        metrics.update({
            "baseline_served_demand": summary.get("baseline_served_demand"),
            "damaged_served_demand": summary.get("damaged_served_demand"),
            "recovered_served_demand": summary.get("recovered_served_demand"),
        })

    document: dict[str, Any] = {
        "result_id": full_result.get("result_id"),
        "task_id": full_result.get("task_id"),
        "task_type": task_type,
        "scenario_id": full_result.get("scenario_id"),
        "parent_scenario_id": full_result.get("parent_scenario_id"),
        "scenario_changes": full_result.get("scenario_changes") or {},
        "data_version": full_result.get("data_version"),
        "data_status": full_result.get("data_status"),
        "analysis_mode": full_result.get("analysis_mode"),
        "solver_status": full_result.get("solver_status"),
        "optimality_proven": resolve_optimality(full_result),
        "verification_status": full_result.get("verification_status"),
        "metrics": metrics,
        # The authoritative full parameter snapshot comes from the caller (the
        # harness session); scenario_changes alone is only the per-turn delta.
        "scenario_params": scenario_params or {
            key: _dig(full_result, ("scenario_changes", key))
            for key in ("population_demand_ratio", "max_walk_time_min",
                        "budget_units", "disabled_shelter_ids")
        },
        "warnings": _warning_list(full_result),
        "evidence": _evidence_counts(full_result),
        "provenance": {
            "skill_graph_version": full_result.get("skill_graph_version"),
            "execution_trace_id": full_result.get("execution_trace_id"),
            "scenario_hash": (full_result.get("scenario_hash") or "")[:16] or None,
            "solver_version": _dig(full_result, ("provenance", "solver_version")),
            "source_hash": (_dig(full_result, ("provenance", "source_hash")) or "")[:16] or None,
        },
        "disclosure": {
            "demo_only": full_result.get("data_status") == "DEMO_ONLY",
            "synthetic_fixture": full_result.get("data_status") == "SYNTHETIC_FIXTURE",
            "verified_for_real_planning": bool(
                _dig(full_result, ("certificate", "verified_for_real_planning"))),
            "must_state": (
                "SYNTHETIC_FIXTURE: test fixture only; not Module A data and not a real-world planning result."
                if full_result.get("data_status") == "SYNTHETIC_FIXTURE"
                else "DEMO_ONLY: not field verified; not an operational planning recommendation."
                if full_result.get("data_status") == "DEMO_ONLY"
                else "VERIFIED source"
            ),
        },
    }

    detail = _detail_for(task_type, full_result, scenario_params=document.get("scenario_params"))
    if detail:
        document["detail"] = detail

    budget = config.summary_max_bytes
    # Reserve room for the self-describing size field so the final document can
    # never exceed the budget by the width of its own metadata.
    document["summary_bytes"] = 0
    if json_bytes(document) > budget:
        document["detail"] = _shrink_detail(document.get("detail") or {})
    if json_bytes(document) > budget:
        document["detail_omitted"] = True
        document["detail_note"] = "Detail omitted to keep the summary within the size budget; use the evidence reader."
        document.pop("detail", None)
    if json_bytes(document) > budget:
        document["warnings"] = document["warnings"][:2]
        document["evidence"] = {"note": "counts omitted"}
    if json_bytes(document) > budget:  # final guard: never exceed the contract
        document.pop("provenance", None)
    if json_bytes(document) > budget:
        document = {
            "result_id": document.get("result_id"),
            "scenario_id": document.get("scenario_id"),
            "data_version": document.get("data_version"),
            "data_status": document.get("data_status"),
            "solver_status": document.get("solver_status"),
            "optimality_proven": document.get("optimality_proven"),
            "verification_status": document.get("verification_status"),
            "metrics": document.get("metrics"),
            "summary_bytes": 0,
            "truncated": True,
        }
    for _ in range(3):
        measured = json_bytes(document)
        if document.get("summary_bytes") == measured:
            break
        document["summary_bytes"] = measured
    if json_bytes(document) > budget:  # last resort: numbers only
        document = {"result_id": document.get("result_id"),
                    "verification_status": document.get("verification_status"),
                    "metrics": document.get("metrics"), "truncated": True,
                    "summary_bytes": 0}
        document["summary_bytes"] = json_bytes(document)
    return document


def _detail_for(task_type: str, full_result: dict[str, Any], *,
                scenario_params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if task_type == "disaster_recovery":
        block = _composite_block(full_result)
        return {"composite": block} if block else None
    if task_type == "diagnose_intervene":
        out: dict[str, Any] = {}
        diagnosis = _diagnosis_digest(full_result)
        if diagnosis is not None:
            out["diagnosis"] = diagnosis
        optimization = _optimize_block(full_result)
        if optimization is not None:
            out["optimization"] = optimization
        intervention = _intervention_block(full_result)
        if intervention is not None:
            out["intervention"] = intervention
        return out or None
    if task_type == "explain_result":
        out = {}
        explanation = _dig(full_result, ("result_detail", "explanation"))
        if isinstance(explanation, dict):
            out["explanation"] = dict(explanation)
        diagnosis = _diagnosis_digest(full_result)
        if diagnosis is not None:
            out["diagnosis"] = diagnosis
        return out or None
    if task_type == "optimize" or _dig(full_result, ("result_detail", "optimization")):
        block = _optimize_block(full_result)
        if block:
            return {"optimization": block}
    if task_type == "stress_test":
        block = _stress_block(full_result)
        if block:
            return {"stress": block}
    if task_type == "compare":
        block = _compare_block(full_result, baseline_params=scenario_params)
        if block:
            return {"comparison": block}
    if task_type == "sensitivity":
        block = _sensitivity_block(full_result)
        if block:
            return {"sensitivity": block}
    if task_type == "assess":
        diagnosis = _diagnosis_digest(full_result)
        if diagnosis:
            return {"diagnosis": diagnosis}
    return None


def _composite_block(full_result: dict[str, Any]) -> dict[str, Any] | None:
    source = _dig(full_result, ("result_detail", "composite"))
    if not isinstance(source, dict):
        return None
    contract = source.get("contract") or {}
    stages = source.get("stage_results") or []
    compact_stages = []
    for row in stages:
        if not isinstance(row, dict):
            continue
        compact_stages.append({key: row.get(key) for key in (
            "stage_id", "status", "reason_code", "input_scenario_id", "scenario_id",
            "parent_scenario_id", "result_id", "input_result_ids", "execution_trace_id",
            "skill_ids", "solver_status", "verification_status", "metrics", "duration_ms",
        )})
    return {
        "status": full_result.get("status"),
        "user_goal": contract.get("user_goal"),
        "region_id": full_result.get("region_id"),
        "data_version": full_result.get("data_version"),
        "source_manifest_digest": contract.get("source_manifest_digest"),
        "stages": compact_stages,
        "comparison": source.get("comparison"),
        "branch_decisions": contract.get("branch_decisions") or [],
        "constraint_checks": contract.get("constraint_checks") or [],
        "verification_results": contract.get("verification_results") or [],
        "execution_trace_id": full_result.get("execution_trace_id"),
        "disclosure": source.get("historical_or_demo_disclosure"),
    }


def _shrink_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Drop the largest list-bearing sub-blocks first."""
    shrunk: dict[str, Any] = {}
    for key, value in detail.items():
        if isinstance(value, dict) and "scenarios" in value:
            rows = value.get("scenarios") or []
            shrunk[key] = {**value, "scenarios": rows[:3], "scenarios_truncated": len(rows) > 3}
        elif isinstance(value, dict) and "points" in value:
            rows = value.get("points") or []
            shrunk[key] = {**value, "points": rows[:5], "points_truncated": len(rows) > 5}
        elif isinstance(value, dict) and "selected_options" in value:
            rows = value.get("selected_options") or []
            shrunk[key] = {**value, "selected_options": rows[:4]}
        else:
            shrunk[key] = value
    return shrunk


# --------------------------------------------------------------------------- #
# Bounded evidence reader (not part of the default model-visible tool list).
# --------------------------------------------------------------------------- #
def paginate_evidence(full_result: dict[str, Any], *, section: str, offset: int = 0,
                      limit: int = 50, config: HarnessConfig | None = None) -> dict[str, Any]:
    config = config or HarnessConfig()
    if section not in SECTION_ALIASES:
        raise ValueError(f"unknown evidence section '{section}'; allowed: {sorted(SECTION_ALIASES)}")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    limit = max(1, min(int(limit), config.evidence_page_max))
    rows = _dig(full_result, SECTION_ALIASES[section])
    if isinstance(rows, dict):  # e.g. shelter_loads
        items = list(rows.items())
        total = len(items)
        page = [{"key": key, "value": value} for key, value in items[offset:offset + limit]]
    elif isinstance(rows, list):
        total = len(rows)
        page = rows[offset:offset + limit]
    elif rows is None:
        total, page = 0, []
    else:
        total, page = 1, [rows]
    return {
        "section": section,
        "offset": offset,
        "limit": limit,
        "total": total,
        "has_more": offset + len(page) < total,
        "items": page,
    }
