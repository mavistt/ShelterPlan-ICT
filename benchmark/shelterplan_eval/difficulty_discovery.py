"""A-only structural and measured difficulty discovery.

The discovery pass reads the frozen Stage 2.1 scenarios and Module A v2
processed artifacts.  It does not call Module B, an HTTP endpoint, or an
Agent.  Consequently its outputs are structural/reachability evidence and
explicit bounds, never fabricated solver or Agent results.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
SCENARIO_PATH = ROOT / "benchmark/shelterplan_eval/pilot_stage2_1/accepted_scenarios.json"
GENERATOR_VERSION = "a2-difficulty-discovery-1.0"
THRESHOLDS = (10.0, 15.0, 20.0, 30.0)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _geojson_properties(path: Path) -> list[dict[str, Any]]:
    document = _read_json(path)
    return [row.get("properties") or {} for row in document.get("features", [])]


def _powerset_count(options: list[dict[str, Any]], budget: float) -> int:
    count = 0
    for size in range(len(options) + 1):
        for subset in itertools.combinations(options, size):
            if sum(_float(row.get("cost_units")) for row in subset) <= budget + 1e-9:
                count += 1
    return count


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _load_parquet_observation(root: Path, region_id: str) -> dict[str, Any]:
    """Load only A-side OD reachability aggregates.

    The full OD relation is not copied into release artifacts.  We retain
    counts and IDs needed to reproduce overlap and coverage evidence.
    """
    region_root = root / "data/processed/regions" / region_id
    demand_rows = _geojson_properties(region_root / "demand_nodes.geojson")
    shelter_rows = _geojson_properties(region_root / "shelters.geojson")
    demand_population = {
        str(row["demand_id"]): max(0.0, _float(row.get("pop_est")))
        for row in demand_rows
        if row.get("demand_id")
    }
    capacities = {
        str(row["shelter_id"]): max(0.0, _float(row.get("capacity_persons_effective", row.get("capacity_persons"))))
        for row in shelter_rows
        if row.get("shelter_id")
    }
    manifest = _read_json(region_root / "manifest.json")
    quality = _read_json(region_root / "quality_summary.json")
    by_threshold: dict[str, dict[str, set[str]]] = {
        str(threshold): defaultdict(set) for threshold in THRESHOLDS
    }
    try:
        import pyarrow.parquet as pq
    except Exception as exc:  # pragma: no cover - environment-specific
        raise RuntimeError(f"pyarrow is required for A-side OD discovery: {exc}") from exc
    parquet = pq.ParquetFile(region_root / "travel_matrix.parquet")
    columns = ["demand_id", "shelter_id", "time_min", "reachable"]
    for batch in parquet.iter_batches(batch_size=65536, columns=columns):
        values = {name: batch.column(name).to_pylist() for name in columns}
        for demand_id, shelter_id, time_min, reachable in zip(*(values[name] for name in columns)):
            if not reachable or time_min is None:
                continue
            time_value = _float(time_min, -1.0)
            if time_value < 0:
                continue
            for threshold in THRESHOLDS:
                if time_value <= threshold + 1e-9:
                    by_threshold[str(threshold)][str(shelter_id)].add(str(demand_id))
    threshold_summary: dict[str, Any] = {}
    for threshold, shelter_sets in by_threshold.items():
        union = set().union(*shelter_sets.values()) if shelter_sets else set()
        threshold_summary[threshold] = {
            "reachable_demand_nodes": len(union),
            "reachable_population": sum(demand_population.get(did, 0.0) for did in union),
            "by_shelter": {sid: sorted(ids) for sid, ids in sorted(shelter_sets.items())},
        }
    return {
        "region_id": region_id,
        "data_version": str(manifest.get("data_version") or ""),
        "data_status": str(quality.get("data_status") or "DEMO_ONLY"),
        "demand_node_count": len(demand_population),
        "total_population": sum(demand_population.values()),
        "capacities": capacities,
        "thresholds": threshold_summary,
        "source_files": {
            "manifest": (region_root / "manifest.json").relative_to(root).as_posix(),
            "shelters": (region_root / "shelters.geojson").relative_to(root).as_posix(),
            "demand_nodes": (region_root / "demand_nodes.geojson").relative_to(root).as_posix(),
            "travel_matrix": (region_root / "travel_matrix.parquet").relative_to(root).as_posix(),
        },
    }


def _scenario_metrics(scenario: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    options = [row for row in scenario.get("improvement_options", []) if row.get("eligible", True)]
    costs = [_float(row.get("cost_units")) for row in options]
    budget = _float(scenario.get("budget_units"), 0.0)
    total_cost = sum(costs)
    groups = sorted({str(row.get("exclusive_group")) for row in options if row.get("exclusive_group")})
    target_ids = [str(row.get("shelter_id")) for row in options if row.get("shelter_id")]
    duplicate_targets = sorted({sid for sid in target_ids if target_ids.count(sid) > 1})
    threshold = str(_float(scenario.get("max_walk_time_min"), 15.0))
    if threshold.endswith(".0"):
        threshold = threshold[:-2]
    threshold_data = observation["thresholds"].get(threshold) or observation["thresholds"].get("15") or {}
    by_shelter = {sid: set(ids) for sid, ids in (threshold_data.get("by_shelter") or {}).items()}
    target_sets = [by_shelter.get(sid, set()) for sid in target_ids]
    overlaps = [_jaccard(left, right) for left, right in itertools.combinations(target_sets, 2)]
    disabled = {str(value) for value in scenario.get("disabled_shelter_ids", [])}
    damaged = {str(value) for value in scenario.get("damaged_shelter_ids", [])}
    active_ids = set(observation["capacities"]) - disabled - damaged
    active_capacity = sum(observation["capacities"].get(sid, 0.0) for sid in active_ids)
    ratio = _float(scenario.get("population_demand_ratio"), 1.0)
    total_demand = observation["total_population"] * ratio
    reachable_population = _float(threshold_data.get("reachable_population")) * ratio
    flow_upper_bound = min(total_demand, active_capacity, reachable_population)
    feasible_counts = {
        "half_budget": _powerset_count(options, budget * 0.5),
        "base_budget": _powerset_count(options, budget),
        "one_and_half_budget": _powerset_count(options, budget * 1.5),
    }
    family = str(scenario.get("scenario_family") or "")
    expected = str(scenario.get("expected_behavior") or "")
    has_negative_contract = expected != "EXECUTE"
    if family == "multi_stage_recovery" and not (damaged or disabled):
        state_warning = "MULTI_STAGE_STRUCTURE_ONLY_NO_DAMAGE_OR_DISABLED_INPUT"
    else:
        state_warning = None
    if family == "facility_disruption" and not (damaged or disabled):
        disruption_warning = "FACILITY_DISRUPTION_HAS_NO_DISABLED_OR_DAMAGED_SHELTER"
    else:
        disruption_warning = None
    budget_competition = total_cost > budget + 1e-9 and bool(options)
    if budget_competition or groups or duplicate_targets or max(overlaps or [0.0]) >= 0.25:
        decision = "high"
    elif damaged or disabled or has_negative_contract or overlaps:
        decision = "medium"
    else:
        decision = "low"
    if family == "multi_stage_recovery":
        workflow = "multi_stage"
    elif expected in {"CLARIFY", "SAFE_STOP"} or has_negative_contract or damaged or disabled:
        workflow = "conditional_branch"
    else:
        workflow = "single_stage"
    interaction = "negative_contract" if has_negative_contract else "complete_request"
    signature = {
        "family": family,
        "region_id": scenario.get("region_id"),
        "behavior": expected,
        "option_count": len(options),
        "costs": sorted(round(value, 6) for value in costs),
        "exclusive_group_count": len(groups),
        "duplicate_target_count": len(duplicate_targets),
        "damaged_count": len(damaged),
        "disabled_count": len(disabled),
        "has_stages": bool(scenario.get("recovery_stages")),
    }
    return {
        "scenario_id": scenario["scenario_id"],
        "semantic_signature": hashlib.sha256(json.dumps(signature, sort_keys=True).encode("utf-8")).hexdigest(),
        "design_difficulty": scenario.get("design_difficulty") or {},
        "measured_difficulty": {
            "decision_complexity": decision,
            "workflow_complexity": workflow,
            "interaction_complexity": interaction,
            "evidence_status": "A_SIDE_STRUCTURAL_AND_REACHABILITY_EVIDENCE",
        },
        "evidence": {
            "option_count": len(options),
            "total_option_cost_units": round(total_cost, 6),
            "budget_units": budget,
            "budget_slack_units": round(budget - total_cost, 6),
            "budget_competition": budget_competition,
            "feasible_portfolio_count_by_budget": feasible_counts,
            "exclusive_group_count": len(groups),
            "exclusive_groups": groups,
            "duplicate_target_shelter_ids": duplicate_targets,
            "target_overlap_jaccard_max": round(max(overlaps or [0.0]), 6),
            "target_overlap_jaccard_mean": round(sum(overlaps) / len(overlaps), 6) if overlaps else 0.0,
            "walk_threshold_min": _float(scenario.get("max_walk_time_min"), 15.0),
            "reachable_demand_nodes": int(threshold_data.get("reachable_demand_nodes") or 0),
            "reachable_population_share": round(
                _float(threshold_data.get("reachable_population")) / max(observation["total_population"], 1e-12), 6
            ),
            "active_capacity_persons": round(active_capacity, 6),
            "demand_persons_at_ratio": round(total_demand, 6),
            "capacity_reachability_upper_bound_persons": round(flow_upper_bound, 6),
            "damaged_shelter_ids": sorted(damaged),
            "disabled_shelter_ids": sorted(disabled),
            "multi_stage_state_evidence": "STRUCTURAL_ONLY_NO_RUNTIME_LINEAGE" if scenario.get("recovery_stages") else "NOT_APPLICABLE",
            "warnings": [value for value in (state_warning, disruption_warning) if value],
        },
        "oracle": {
            "status": "NOT_PROVEN",
            "proof_level": "NOT_PROVEN",
            "reason": "A-side reachability/capacity bound only; no B solver output and no full integer-scaled portfolio proof on this GIS case.",
        },
        "provenance": {
            "data_status": observation["data_status"],
            "data_version": observation["data_version"],
            "source_files": observation["source_files"],
            "source_data_modified": False,
            "hypothetical_options": True,
            "runtime_execution": "NOT_RUN",
        },
    }


def _compact_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """Keep release evidence auditable without duplicating the full OD ID lists."""
    thresholds: dict[str, Any] = {}
    for threshold, row in observation["thresholds"].items():
        thresholds[threshold] = {
            "reachable_demand_nodes": row["reachable_demand_nodes"],
            "reachable_population": row["reachable_population"],
            "reachable_nodes_by_shelter": {
                shelter_id: len(demand_ids)
                for shelter_id, demand_ids in sorted(row.get("by_shelter", {}).items())
            },
        }
    return {
        "region_id": observation["region_id"],
        "data_version": observation["data_version"],
        "data_status": observation["data_status"],
        "demand_node_count": observation["demand_node_count"],
        "total_population": observation["total_population"],
        "shelter_count": len(observation["capacities"]),
        "capacity_total_persons": sum(observation["capacities"].values()),
        "thresholds": thresholds,
        "source_files": observation["source_files"],
        "note": "Full OD IDs remain in the immutable A v2 travel_matrix.parquet; release stores aggregate counts only.",
    }


def discover(scenarios: list[dict[str, Any]], *, root: Path = ROOT) -> dict[str, Any]:
    regions = sorted({str(row["region_id"]) for row in scenarios})
    observations = {region: _load_parquet_observation(root, region) for region in regions}
    evidence = [_scenario_metrics(row, observations[str(row["region_id"])]) for row in scenarios]
    signatures = Counter(row["semantic_signature"] for row in evidence)
    for row in evidence:
        row["similarity_group_size"] = signatures[row["semantic_signature"]]
        if row["similarity_group_size"] > 1:
            row["selection_warning"] = "STRUCTURALLY_REPETITIVE_WITHIN_FROZEN_POOL"
    return {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "source_scenario_file": SCENARIO_PATH.relative_to(root).as_posix(),
        "scenario_count": len(scenarios),
        "region_observations": {region: _compact_observation(observation) for region, observation in observations.items()},
        "evidence": evidence,
        "summary": {
            "measured_difficulty_counts": dict(Counter(row["measured_difficulty"]["decision_complexity"] for row in evidence)),
            "expected_behavior_counts": dict(Counter(str(row.get("expected_behavior")) for row in scenarios)),
            "oracle_status_counts": dict(Counter(row["oracle"]["status"] for row in evidence)),
            "repetitive_scenario_count": sum(row["similarity_group_size"] > 1 for row in evidence),
            "state_dependency_warnings": [row["scenario_id"] for row in evidence if row["evidence"]["warnings"]],
        },
    }


def _markdown(document: dict[str, Any]) -> str:
    summary = document["summary"]
    lines = [
        "# A2 Difficulty Discovery",
        "",
        "This is an A-only structural/reachability pass over the frozen Stage 2.1 pool.",
        "It does not run B, C, LLM, or an Agent and therefore does not report Agent scores.",
        "",
        f"- Scenarios: `{document['scenario_count']}`",
        f"- Measured decision labels: `{json.dumps(summary['measured_difficulty_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Expected behaviors: `{json.dumps(summary['expected_behavior_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Oracle statuses: `{json.dumps(summary['oracle_status_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Structurally repetitive scenarios: `{summary['repetitive_scenario_count']}`",
        "",
        "## Evidence boundary",
        "",
        "The measured labels are based on option/budget/exclusivity structure and the A v2 travel matrix. "
        "Real GIS cases remain `NOT_PROVEN` for optimality; hypothetical investment costs/capacities remain `DEMO_ONLY`.",
        "",
        "## Recovery warning",
        "",
    ]
    warning_ids = summary["state_dependency_warnings"]
    if warning_ids:
        lines.append("The following cases require a runtime parent/child trace before their recovery difficulty can be called measured:")
        lines.extend(f"- `{value}`" for value in warning_ids)
    else:
        lines.append("No structural recovery warning was emitted.")
    lines.extend(["", "## Scenario evidence", "", "| scenario | decision | workflow | options | budget competition | overlap max | warnings |", "|---|---|---|---:|---|---:|---|"])
    for row in document["evidence"]:
        evidence = row["evidence"]
        lines.append(
            f"| `{row['scenario_id']}` | `{row['measured_difficulty']['decision_complexity']}` | "
            f"`{row['measured_difficulty']['workflow_complexity']}` | `{evidence['option_count']}` | "
            f"`{evidence['budget_competition']}` | `{evidence['target_overlap_jaccard_max']}` | "
            f"{'; '.join(evidence['warnings']) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def run(*, root: Path = ROOT, output_dir: Path | None = None) -> dict[str, Any]:
    output_dir = output_dir or root / "reports/final_release"
    scenarios = _read_json(root / "benchmark/shelterplan_eval/pilot_stage2_1/accepted_scenarios.json")
    document = discover(scenarios, root=root)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "difficulty_discovery.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "difficulty_discovery.md").write_text(_markdown(document), encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    document = run(root=args.root.resolve(), output_dir=args.output_dir.resolve() if args.output_dir else None)
    print(json.dumps(document["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
