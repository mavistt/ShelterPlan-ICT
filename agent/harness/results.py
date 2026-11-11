"""Result projection and bounded evidence access.

Module B returns 5-11 MB documents (8k+ verifier checks). A model context can
never see that. Everything exposed here is a projection; the full document stays
on disk under Module B's own `outputs/module_b/runs/`.
"""
from __future__ import annotations

import json
from typing import Any

from agent.config import HarnessConfig

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
    return {
        "selected_option_ids": list(optimization.get("selected_option_ids") or []),
        "total_cost_units": optimization.get("total_cost_units"),
        "budget_units": optimization.get("budget_units"),
        "served_demand": _dig(optimization, ("assignment", "served_demand")),
        "total_demand": _dig(optimization, ("assignment", "total_demand")),
        "weighted_walk_time_min": _dig(optimization, ("assignment", "weighted_walk_time_min")),
        "solver": optimization.get("solver"),
        "mip_stage_count": len(optimization.get("mip_stages") or []),
        "all_stages_optimal": all(
            bool(stage.get("optimality_proven")) for stage in (optimization.get("mip_stages") or [])
        ) if optimization.get("mip_stages") else None,
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


def _compare_block(full_result: dict[str, Any], keep: int = 12) -> dict[str, Any] | None:
    comparison = _dig(full_result, ("result_detail", "comparison"))
    if not isinstance(comparison, dict):
        return None
    rows = []
    for row in (comparison.get("scenarios") or [])[:keep]:
        rows.append({
            "scenario_id": row.get("scenario_id"),
            "changes": row.get("changes"),
            "served_demand": row.get("served_demand"),
            "weighted_walk_time_min": row.get("weighted_walk_time_min"),
            "solver_status": row.get("solver_status"),
            "verification_status": row.get("verification_status"),
        })
    return {
        "data_version": comparison.get("data_version"),
        "scenario_count": len(comparison.get("scenarios") or []),
        "scenarios": rows,
        "unique_recommendation": comparison.get("unique_recommendation"),
        "note": "No forced winner: the service returns side-by-side rows only.",
    }


def _sensitivity_block(full_result: dict[str, Any], keep: int = 20) -> dict[str, Any] | None:
    sensitivity = _dig(full_result, ("result_detail", "sensitivity"))
    if not isinstance(sensitivity, dict):
        return None
    points = []
    for row in (sensitivity.get("points") or [])[:keep]:
        points.append({
            "value": row.get("value"),
            "served_demand": row.get("served_demand"),
            "weighted_walk_time_min": row.get("weighted_walk_time_min"),
            "selected_option_ids": row.get("selected_option_ids"),
            "verification_status": row.get("verification_status"),
        })
    return {"parameter": sensitivity.get("parameter"), "point_count": len(points), "points": points}


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
            config: HarnessConfig | None = None) -> dict[str, Any]:
    """Build a <= summary_max_bytes summary that is byte-identical in its numbers."""
    config = config or HarnessConfig()
    summary = full_result.get("result_summary") or {}

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
        "metrics": {
            "served_demand": summary.get("served_demand"),
            "total_demand": summary.get("total_demand"),
            "weighted_walk_time_min": summary.get("weighted_walk_time_min"),
            "optimized_served_demand": summary.get("optimized_served_demand"),
            "diagnosed_unserved_nodes": summary.get("diagnosed_unserved_nodes"),
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
            "verified_for_real_planning": bool(
                _dig(full_result, ("certificate", "verified_for_real_planning"))),
            "must_state": "DEMO_ONLY — 未经真实现场核验，不得作为应急执行建议"
            if full_result.get("data_status") == "DEMO_ONLY" else "VERIFIED source",
        },
    }

    detail = _detail_for(task_type, full_result)
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


def _detail_for(task_type: str, full_result: dict[str, Any]) -> dict[str, Any] | None:
    if task_type == "optimize" or _dig(full_result, ("result_detail", "optimization")):
        block = _optimize_block(full_result)
        if block:
            return {"optimization": block}
    if task_type == "stress_test":
        block = _stress_block(full_result)
        if block:
            return {"stress": block}
    if task_type == "compare":
        block = _compare_block(full_result)
        if block:
            return {"comparison": block}
    if task_type == "sensitivity":
        block = _sensitivity_block(full_result)
        if block:
            return {"sensitivity": block}
    if task_type == "diagnose_intervene":
        diagnosis = _diagnosis_digest(full_result)
        intervention = _intervention_block(full_result)
        if diagnosis or intervention:
            return {"diagnosis": diagnosis, "intervention": intervention}
    if task_type in {"assess", "explain_result"}:
        diagnosis = _diagnosis_digest(full_result)
        if diagnosis:
            return {"diagnosis": diagnosis}
    return None


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
