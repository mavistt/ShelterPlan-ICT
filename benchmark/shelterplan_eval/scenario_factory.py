"""Stage 2.1 Scenario Factory.

The factory is deliberately independent of historical ``merge_data``. It
describes a real Module A GIS environment, then adds explicitly labelled
hypothetical capacity-expansion options that match Module B's
``ImprovementOption`` contract. It never claims that a scenario was executed
or that an optimum was independently proven.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.models import ImprovementOption, TaskRequest
from sources.preflight import ROOT, sha256_file


SCHEMA_VERSION = 2
GENERATOR_VERSION = "stage2-pilot-2.1"
ALLOWED_REGIONS = ("shiqi", "east_district")
ALL_REGION_IDS = ("shiqi", "east_district", "xiaolan")
MAX_API_CANDIDATES = 12
SUPPORTED_PROJECT_ACTION = "capacity_expansion"
VALID_BEHAVIORS = {"EXECUTE", "CLARIFY", "REJECT", "SAFE_STOP"}

TASK_FAMILIES = (
    {
        "family": "coverage_baseline",
        "design_difficulty": {"decision_complexity": "low", "workflow_complexity": "single_stage"},
        "design_goal": "measure a normal existing-shelter baseline before hypothetical investment",
    },
    {
        "family": "budget_tradeoff",
        "design_difficulty": {"decision_complexity": "high", "workflow_complexity": "conditional_branch"},
        "design_goal": "force budget competition between legal capacity expansions",
    },
    {
        "family": "capacity_competition",
        "design_difficulty": {"decision_complexity": "high", "workflow_complexity": "multi_stage"},
        "design_goal": "exercise exclusive alternatives targeting the same existing shelter",
    },
    {
        "family": "facility_disruption",
        "design_difficulty": {"decision_complexity": "medium", "workflow_complexity": "conditional_branch"},
        "design_goal": "remove service from a real shelter while retaining the regional GIS environment",
    },
    {
        "family": "multi_stage_recovery",
        "design_difficulty": {"decision_complexity": "high", "workflow_complexity": "multi_stage"},
        "design_goal": "carry a parent scenario and staged capacity-expansion state forward",
    },
    {
        "family": "clarification_and_safe_stop",
        "design_difficulty": {"decision_complexity": "medium", "workflow_complexity": "safe_stop"},
        "design_goal": "separate missing-input clarification from data-grounded safe stopping",
    },
)

REQUIRED_ASSUMPTIONS = {
    "region_id", "data_version", "source_manifest_sha256", "dataset_content_hash",
    "a_v2_manifest_sha256", "shelter_ids", "base_shelter_ids", "damaged_shelter_ids",
    "disabled_shelter_ids", "population_demand_ratio", "max_walk_time_min",
    "recovery_projects", "improvement_options", "budget_units", "scenario_seed",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assumption(parameter: str, value: Any, source_label: str, source_detail: str) -> dict[str, Any]:
    return {"parameter": parameter, "value": value, "source_label": source_label, "source_detail": source_detail}


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


@dataclass(frozen=True)
class RegionSnapshot:
    region_id: str
    data_version: str
    source_manifest_sha256: str
    dataset_content_hash: str
    a_v2_manifest_sha256: str
    a_v2_manifest_match: bool
    a_v2_content_match: bool
    enabled: bool
    boundary_status: str
    data_status: str
    all_shelter_ids: tuple[str, ...]
    base_shelter_ids: tuple[str, ...]
    normal_candidate_ids: tuple[str, ...]
    capacity_by_shelter: dict[str, float]
    source_status_by_shelter: dict[str, str]
    coverage_by_threshold: dict[str, float]


def load_snapshots(root: Path = ROOT) -> dict[str, RegionSnapshot]:
    source_manifest_path = root / "sources/source_manifest.json"
    source_manifest = read_json(source_manifest_path)
    source_manifest_sha256 = sha256_file(source_manifest_path)
    dataset_content_hash = str(source_manifest.get("dataset_content_hash") or "")
    if not dataset_content_hash:
        raise ValueError("source_manifest.json lacks stable dataset_content_hash; run sources.preflight first")
    eligibility = read_csv(root / "sources/eligibility.csv")
    eligibility_by_region: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in eligibility:
        eligibility_by_region[row.get("region_id", "")].append(row)
    manifest_checks = {item["region_id"]: item for item in source_manifest.get("a_v2_manifests", [])}
    region_checks = {item["region_id"]: item for item in source_manifest.get("regions", [])}
    snapshots: dict[str, RegionSnapshot] = {}
    for region_id in ALL_REGION_IDS:
        region_root = root / "data/processed/regions" / region_id
        region_manifest = read_json(region_root / "manifest.json")
        quality = read_json(region_root / "quality_summary.json")
        region_check = region_checks.get(region_id, {})
        manifest_check = manifest_checks.get(region_id, {})
        rows = read_csv(region_root / "shelters_all.csv")
        all_ids = tuple(sorted(row.get("shelter_id", "") for row in rows if row.get("shelter_id")))
        base_ids = tuple(sorted(row["shelter_id"] for row in rows if row.get("shelter_id") and _truthy(row.get("active", "true"))))
        eligible_rows = {row["shelter_id"]: row for row in eligibility_by_region.get(region_id, []) if row.get("shelter_id")}
        normal_ids = tuple(sorted(
            shelter_id for shelter_id in base_ids
            if _truthy(eligible_rows.get(shelter_id, {}).get("eligible_for_normal_planning", ""))
            or _truthy(eligible_rows.get(shelter_id, {}).get("eligible_for_demo", ""))
        ))
        capacities: dict[str, float] = {}
        source_status: dict[str, str] = {}
        for row in rows:
            try:
                capacities[row["shelter_id"]] = float(row.get("capacity_persons_effective") or row.get("capacity_persons") or 0)
            except (TypeError, ValueError):
                capacities[row["shelter_id"]] = 0.0
            source_status[row["shelter_id"]] = str(row.get("data_status") or "DEMO_ONLY")
        coverage = quality.get("spatial_analysis", {}).get("coverage", {})
        coverage_by_threshold = {
            str(key): float(value.get("population_share", 0.0))
            for key, value in coverage.items()
            if isinstance(value, dict) and value.get("population_share") is not None
        }
        snapshots[region_id] = RegionSnapshot(
            region_id=region_id,
            data_version=str(region_manifest.get("data_version", "")),
            source_manifest_sha256=source_manifest_sha256,
            dataset_content_hash=dataset_content_hash,
            a_v2_manifest_sha256=sha256_file(region_root / "manifest.json"),
            a_v2_manifest_match=bool(manifest_check.get("all_declared_files_match", False)),
            a_v2_content_match=bool(manifest_check.get("all_content_match", False)),
            enabled=bool(region_check.get("enabled", False)),
            boundary_status=str(region_check.get("boundary_status") or "unknown"),
            data_status=str(quality.get("data_status", "unknown")),
            all_shelter_ids=all_ids,
            base_shelter_ids=base_ids,
            normal_candidate_ids=normal_ids,
            capacity_by_shelter=capacities,
            source_status_by_shelter=source_status,
            coverage_by_threshold=coverage_by_threshold,
        )
    return snapshots


def _family(index: int) -> dict[str, Any]:
    return dict(TASK_FAMILIES[index % len(TASK_FAMILIES)])


def _safe_stop_target(snapshot: RegionSnapshot) -> dict[str, Any] | None:
    observed = snapshot.coverage_by_threshold.get("10")
    if observed is None or observed <= 0:
        return None
    return {
        "threshold_min": 10,
        "target_population_share": round(max(0.01, observed - 0.01), 2),
        "observed_population_share": observed,
        "evidence_source": "A_V2_QUALITY_SPATIAL_COVERAGE",
        "evidence_note": "reachability only; no capacity allocation claim",
    }


def _build_options(snapshot: RegionSnapshot, family: str, index: int, damaged: set[str]) -> list[dict[str, Any]]:
    candidates = [sid for sid in snapshot.normal_candidate_ids if sid not in damaged]
    if len(candidates) < 3:
        raise ValueError(f"Not enough legal expansion candidates for {snapshot.region_id}")
    count = 2 if family == "coverage_baseline" else 4
    primary = candidates[index % len(candidates)]
    targets = [primary] + [candidates[(index + offset) % len(candidates)] for offset in range(1, count)]
    options: list[dict[str, Any]] = []
    for option_index, shelter_id in enumerate(targets):
        if family == "capacity_competition" and option_index == 1:
            shelter_id = primary
        redundant = family == "capacity_competition" and option_index in {0, 1}
        options.append({
            "option_id": f"OPT-{snapshot.region_id}-{index:03d}-{option_index:02d}",
            "shelter_id": shelter_id,
            "action": "capacity_expansion",
            "delta_capacity_persons": 120.0 if redundant else float(120 + option_index * 80),
            "cost_units": 4.0 if redundant else float(5 + option_index * 2),
            "eligible": True,
            "source_status": f"{snapshot.source_status_by_shelter.get(shelter_id, 'DEMO_ONLY')}; hypothetical expansion",
            "hypothetical": True,
            "human_confirmed": False,
            "combination_policy": "exclusive_group" if redundant else "standalone",
            "exclusive_group": f"capacity-alternative-{primary}" if redundant else None,
        })
    return options


def _interaction_for(index: int, family: str, snapshot: RegionSnapshot) -> tuple[str, str, dict[str, Any] | None]:
    if index % 11 == 5:
        return "invalid_id", "REJECT", None
    if index % 11 == 6:
        return "illegal_constraint", "REJECT", None
    if index % 11 == 9:
        return "illegal_budget", "REJECT", None
    if index % 11 == 10:
        return "data_version_mismatch", "REJECT", None
    if family == "clarification_and_safe_stop":
        # The family recurs every six candidates; alternate the family-local
        # variant so both CLARIFY and data-grounded SAFE_STOP are generated.
        if (index // len(TASK_FAMILIES)) % 2 == 0:
            return "missing_parameter", "CLARIFY", None
        evidence = _safe_stop_target(snapshot)
        if evidence:
            return "safe_stop", "SAFE_STOP", evidence
        return "missing_parameter", "CLARIFY", None
    if family == "multi_stage_recovery":
        return "parameter_edit", "EXECUTE", None
    return "complete_request", "EXECUTE", None


def _request_payload(scenario: dict[str, Any], interaction: str, index: int) -> dict[str, Any]:
    payload = {
        "region_id": scenario["region_id"],
        "data_version": scenario["data_version"],
        "shelter_ids": list(scenario["base_shelter_ids"]),
        "disabled_shelter_ids": list(scenario["disabled_shelter_ids"]),
        "population_demand_ratio": scenario["population_demand_ratio"],
        "max_walk_time_min": scenario["max_walk_time_min"],
        "improvement_options": scenario["improvement_options"],
        "budget_units": scenario["budget_units"],
    }
    if interaction == "missing_parameter":
        payload.pop("budget_units")
    elif interaction == "invalid_id":
        payload["improvement_options"] = [dict(option) for option in payload["improvement_options"]]
        payload["improvement_options"][0]["shelter_id"] = f"invalid-{index:03d}"
    elif interaction == "illegal_constraint":
        payload["max_walk_time_min"] = -5
    elif interaction == "illegal_budget":
        payload["budget_units"] = -1
    elif interaction == "data_version_mismatch":
        payload["data_version"] = "shelterplan-historical-v2"
    elif interaction == "parameter_edit":
        payload["parent_scenario_id"] = f"ST21-parent-{index:03d}"
        payload["population_demand_ratio"] = min(1.0, float(payload["population_demand_ratio"]) + 0.05)
    elif interaction == "safe_stop":
        payload["safe_stop_reason"] = scenario["safe_stop_evidence"]["evidence_note"]
        payload["target_population_share"] = scenario["safe_stop_evidence"]["target_population_share"]
    return payload


def _b_api_request(scenario: dict[str, Any], expected_behavior: str) -> dict[str, Any]:
    scenario_doc = {
        "scenario_id": scenario["scenario_id"],
        "region_id": scenario["region_id"],
        "data_version": scenario["data_version"],
        "analysis_mode": "demo",
        "population_demand_ratio": scenario["population_demand_ratio"],
        "max_walk_time_min": scenario["max_walk_time_min"],
        "budget_units": scenario["budget_units"],
        "disabled_shelter_ids": scenario["disabled_shelter_ids"],
        "changes": {"dataset_content_hash": scenario["dataset_content_hash"], "scenario_family": scenario["scenario_family"]},
    }
    task_type = "assess" if expected_behavior in {"CLARIFY", "SAFE_STOP"} else "optimize"
    return {
        "task_type": task_type,
        "scenario_id": scenario["scenario_id"],
        "region_id": scenario["region_id"],
        "data_version": scenario["data_version"],
        "analysis_mode": "demo",
        "scenario": scenario_doc,
        "improvement_options": scenario["improvement_options"],
        "parameters": {"budget_units": scenario["budget_units"]} if task_type == "optimize" else {},
    }


def _semantic_option(option: dict[str, Any], shelter_rank: dict[str, int]) -> dict[str, Any]:
    return {
        "target_shelter_rank": shelter_rank.get(option["shelter_id"]),
        "action": option["action"],
        "delta_capacity_persons": option["delta_capacity_persons"],
        "cost_units": option["cost_units"],
        "combination_policy": option["combination_policy"],
        "exclusive": bool(option.get("exclusive_group")),
    }


def _attach_signatures(scenario: dict[str, Any]) -> None:
    shelter_rank = {shelter_id: index for index, shelter_id in enumerate(sorted(scenario["base_shelter_ids"]))}
    semantic_identity = {
        "region_id": scenario["region_id"],
        "data_version": scenario["data_version"],
        "dataset_content_hash": scenario["dataset_content_hash"],
        "scenario_family": scenario["scenario_family"],
        "base_shelter_count": len(scenario["base_shelter_ids"]),
        "damaged_shelter_ranks": sorted(shelter_rank.get(sid) for sid in scenario["damaged_shelter_ids"]),
        "options": [_semantic_option(option, shelter_rank) for option in scenario["improvement_options"]],
        "budget_units": scenario["budget_units"],
        "interaction": scenario["interaction_type"],
        "expected_behavior": scenario["expected_behavior"],
        "negative_type": scenario["negative_type"],
        "safe_stop_evidence": scenario.get("safe_stop_evidence"),
    }
    family_identity = {key: semantic_identity[key] for key in ("region_id", "data_version", "dataset_content_hash", "scenario_family", "options", "budget_units")}
    scenario["scenario_family_signature"] = stable_hash(family_identity)
    scenario["semantic_signature"] = stable_hash(semantic_identity)
    scenario["fingerprint"] = scenario["semantic_signature"]


def generate_candidate(snapshot: RegionSnapshot, *, index: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    family_spec = _family(index)
    family = family_spec["family"]
    if not snapshot.normal_candidate_ids:
        raise ValueError(f"No normal DEMO_ONLY candidate shelters for {snapshot.region_id}")
    base_ids = list(snapshot.base_shelter_ids)
    rng.shuffle(base_ids)
    damaged = set()
    if family == "facility_disruption":
        damaged.add(sorted(snapshot.normal_candidate_ids)[index % len(snapshot.normal_candidate_ids)])
    options = _build_options(snapshot, family, index, damaged)
    total_cost = sum(float(option["cost_units"]) for option in options)
    budget = total_cost + 1.0 if family == "coverage_baseline" else max(1.0, round(total_cost * 0.45, 2))
    interaction, expected_behavior, safe_stop_evidence = _interaction_for(index, family, snapshot)
    ratio = round(0.2 + 0.05 * (index % 3), 2)
    walk_time = (10, 15, 20, 30)[index % 4]
    if interaction == "safe_stop" and safe_stop_evidence:
        walk_time = safe_stop_evidence["threshold_min"]
    stage_map = {}
    if family == "multi_stage_recovery":
        midpoint = max(1, len(options) // 2)
        stage_map = {"stage_1": [option["option_id"] for option in options[:midpoint]], "stage_2": [option["option_id"] for option in options[midpoint:]]}
    redundant_ids = [option["option_id"] for option in options[:2]] if family == "capacity_competition" else []
    scenario: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "scenario_id": f"ST21-{snapshot.region_id}-{index:03d}",
        "scenario_family": family,
        "region_id": snapshot.region_id,
        "data_version": snapshot.data_version,
        "source_manifest_sha256": snapshot.source_manifest_sha256,
        "dataset_content_hash": snapshot.dataset_content_hash,
        "a_v2_manifest_sha256": snapshot.a_v2_manifest_sha256,
        "shelter_ids": sorted(base_ids),
        "base_shelter_ids": sorted(base_ids),
        "improvement_candidate_shelter_ids": sorted({option["shelter_id"] for option in options}),
        "damaged_shelter_ids": sorted(damaged),
        "disabled_shelter_ids": sorted(damaged),
        "population_demand_ratio": ratio,
        "max_walk_time_min": walk_time,
        "recovery_projects": options,
        "improvement_options": options,
        "budget_units": budget,
        "scenario_seed": seed,
        "design_difficulty": {**family_spec["design_difficulty"], "interaction_complexity": interaction, "design_goal": family_spec["design_goal"]},
        "measured_difficulty": {
            "decision_complexity": "NOT_MEASURED", "workflow_complexity": "NOT_MEASURED",
            "interaction_complexity": "NOT_MEASURED", "evidence_status": "NO_SOLVER_TRACE_OR_INDEPENDENT_ORACLE",
        },
        "difficulty_evidence": {
            "total_option_cost_units": total_cost, "budget_units": budget,
            "budget_competition": budget < total_cost,
            "budget_shortfall_units": round(max(0.0, total_cost - budget), 2),
            "exclusive_group_count": len({option["exclusive_group"] for option in options if option.get("exclusive_group")}),
            "redundant_investment_option_ids": redundant_ids,
            "spatial_overlap": {"status": "NOT_MEASURED", "reason": "No independent spatial project model"},
            "greedy_mip_gap": {"status": "NOT_MEASURED", "value": None, "proof_level": "NONE"},
            "damaged_service_loss": {"status": "DESIGN_ESTIMATE", "capacity_removed_persons": sum(snapshot.capacity_by_shelter.get(sid, 0.0) for sid in damaged)},
            "conditional_branch_required": interaction in {"parameter_edit", "safe_stop"},
            "multi_round_state_inheritance": family == "multi_stage_recovery",
            "negative_request_correctness": "NOT_EXECUTED",
        },
        "recovery_stages": stage_map,
        "selected_project_option_ids": [],
        "selection_status": "NOT_EXECUTED",
        "expected_behavior": expected_behavior,
        "interaction_type": interaction,
        "negative_type": "valid_negative_benchmark" if expected_behavior in {"REJECT", "CLARIFY"} else None,
        "safe_stop_evidence": safe_stop_evidence,
        "execution_status": "NOT_EXECUTED",
        "oracle_status": "NOT_AVAILABLE",
        "provenance": {
            "data_status": snapshot.data_status, "boundary_status": snapshot.boundary_status,
            "real_gis_inputs": True,
            "hypothetical_inputs": ["population_demand_ratio", "recovery_projects", "budget_units"],
            "temporary_site_supported": False, "verified_for_release": False,
            "independent_oracle": False, "audit_snapshot_sha256_is_run_metadata": True,
        },
        "assumptions": [
            assumption("region_id", snapshot.region_id, "A_V2_MANIFEST", "A v2 region manifest"),
            assumption("data_version", snapshot.data_version, "A_V2_MANIFEST", "A v2 region manifest"),
            assumption("source_manifest_sha256", snapshot.source_manifest_sha256, "EVAL_AUDIT_RUN", "raw audit snapshot SHA-256; not stable identity"),
            assumption("dataset_content_hash", snapshot.dataset_content_hash, "EVAL_DATASET_IDENTITY", "stable input content identity"),
            assumption("a_v2_manifest_sha256", snapshot.a_v2_manifest_sha256, "A_V2_MANIFEST", "region manifest raw SHA-256"),
            assumption("shelter_ids", sorted(base_ids), "A_V2_DERIVED", "complete active regional GIS shelter environment"),
            assumption("base_shelter_ids", sorted(base_ids), "A_V2_DERIVED", "base shelter environment; not the B option limit"),
            assumption("damaged_shelter_ids", sorted(damaged), "SCENARIO_PARAMETER", "existing shelter disruption input"),
            assumption("disabled_shelter_ids", sorted(damaged), "SCENARIO_PARAMETER", "disabled for this scenario only"),
            assumption("population_demand_ratio", ratio, "SCENARIO_PARAMETER", "pilot parameter"),
            assumption("max_walk_time_min", walk_time, "SCENARIO_PARAMETER", "pilot parameter"),
            assumption("recovery_projects", options, "HYPOTHETICAL_RECOVERY_PROJECT", "B-compatible existing-shelter capacity options"),
            assumption("improvement_options", options, "MODULE_B_CONTRACT", "ImprovementOption-compatible; max 12 applies here"),
            assumption("budget_units", budget, "SCENARIO_PARAMETER", "pilot budget units"),
            assumption("scenario_seed", seed, "GENERATOR_SEED", "deterministic pilot seed"),
        ],
    }
    scenario["request_payload"] = _request_payload(scenario, interaction, index)
    scenario["b_api_request"] = _b_api_request(scenario, expected_behavior)
    _attach_signatures(scenario)
    return scenario


def _validate_option_contract(option: dict[str, Any]) -> str | None:
    try:
        ImprovementOption(**option)
    except Exception as exc:
        return f"improvement_option_schema_invalid:{type(exc).__name__}"
    if float(option.get("delta_capacity_persons", 0)) <= 0 or float(option.get("cost_units", 0)) <= 0:
        return "improvement_option_nonpositive_delta_or_cost"
    if option.get("action") != SUPPORTED_PROJECT_ACTION:
        return "unsupported_recovery_project_type"
    if not option.get("eligible") or not option.get("hypothetical") or option.get("human_confirmed"):
        return "improvement_option_provenance_invalid"
    return None


def validate_scenario(
    scenario: dict[str, Any], snapshot: RegionSnapshot, *,
    seen_signatures: set[str] | None = None,
    seen_fingerprints: set[str] | None = None,
    allow_manifest_blocked_demo: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    structural_errors: list[str] = []
    warnings: list[str] = []
    if scenario.get("region_id") != snapshot.region_id:
        structural_errors.append("region_id_mismatch")
    if snapshot.region_id not in ALLOWED_REGIONS or not snapshot.enabled:
        structural_errors.append("disabled_region")
    if scenario.get("data_version") != snapshot.data_version:
        structural_errors.append("data_version_mismatch")
    if scenario.get("dataset_content_hash") != snapshot.dataset_content_hash:
        structural_errors.append("dataset_content_hash_mismatch")
    if scenario.get("source_manifest_sha256") != snapshot.source_manifest_sha256:
        warnings.append("source_manifest_audit_snapshot_changed; dataset_content_hash is the stable identity")
    if scenario.get("a_v2_manifest_sha256") != snapshot.a_v2_manifest_sha256:
        structural_errors.append("a_v2_manifest_mismatch")
    if not str(scenario.get("data_version", "")).startswith("shelterplan_module_a_v2.0-"):
        structural_errors.append("non_a_v2_or_historical_data_version")
    base_ids = list(scenario.get("base_shelter_ids", scenario.get("shelter_ids", [])))
    shelter_ids = list(scenario.get("shelter_ids", []))
    disabled_ids = list(scenario.get("disabled_shelter_ids", []))
    options = scenario.get("improvement_options", scenario.get("recovery_projects", []))
    if not base_ids or base_ids != shelter_ids:
        structural_errors.append("shelter_ids_semantics_invalid")
    if len(base_ids) != len(set(base_ids)):
        structural_errors.append("duplicate_base_shelter_id")
    if not set(base_ids).issubset(set(snapshot.base_shelter_ids)):
        structural_errors.append("invalid_or_non_admissible_base_shelter_id")
    if not set(disabled_ids).issubset(set(snapshot.base_shelter_ids)):
        structural_errors.append("invalid_disabled_shelter_id")
    if not isinstance(options, list) or not options:
        structural_errors.append("improvement_options_invalid")
        options = []
    if len(options) > MAX_API_CANDIDATES:
        structural_errors.append("api_candidate_limit_exceeded")
    option_ids = []
    for option in options:
        option_error = _validate_option_contract(option)
        if option_error:
            structural_errors.append(option_error)
        option_ids.append(option.get("option_id"))
        shelter_id = option.get("shelter_id")
        if shelter_id not in snapshot.normal_candidate_ids:
            structural_errors.append("improvement_option_shelter_not_admissible")
        if shelter_id in disabled_ids:
            structural_errors.append("improvement_option_targets_disabled_shelter")
    if len(option_ids) != len(set(option_ids)):
        structural_errors.append("duplicate_improvement_option_id")
    if scenario.get("recovery_projects") != options:
        structural_errors.append("recovery_projects_not_bound_to_improvement_options")
    if any(option.get("action") == "temporary_site" for option in options):
        structural_errors.append("temporary_site_not_supported_without_complete_gis_od")
    try:
        ratio = float(scenario["population_demand_ratio"])
        if not 0 < ratio <= 1:
            structural_errors.append("population_demand_ratio_out_of_range")
    except (KeyError, TypeError, ValueError):
        structural_errors.append("population_demand_ratio_invalid")
    try:
        walk_time = float(scenario["max_walk_time_min"])
        if not 0 < walk_time <= 120:
            structural_errors.append("max_walk_time_invalid")
    except (KeyError, TypeError, ValueError):
        structural_errors.append("max_walk_time_invalid")
    try:
        if float(scenario["budget_units"]) <= 0:
            structural_errors.append("budget_constraint_invalid")
    except (KeyError, TypeError, ValueError):
        structural_errors.append("budget_constraint_invalid")
    if not isinstance(scenario.get("scenario_seed"), int):
        structural_errors.append("scenario_seed_invalid")
    assumption_names = {item.get("parameter") for item in scenario.get("assumptions", []) if item.get("source_label")}
    missing_assumptions = REQUIRED_ASSUMPTIONS - assumption_names
    if missing_assumptions:
        structural_errors.append("missing_assumption_source:" + ",".join(sorted(missing_assumptions)))

    interaction = scenario.get("interaction_type")
    expected = scenario.get("expected_behavior")
    payload = scenario.get("request_payload", {})
    valid_request = False
    request_errors: list[str] = []
    if expected not in VALID_BEHAVIORS:
        structural_errors.append("unknown_expected_behavior")
    if interaction == "missing_parameter":
        valid_request = "budget_units" not in payload and expected == "CLARIFY"
    elif interaction == "invalid_id":
        invalid_options = payload.get("improvement_options", [])
        valid_request = bool(invalid_options) and invalid_options[0].get("shelter_id") not in snapshot.base_shelter_ids and expected == "REJECT"
        if valid_request:
            request_errors.append("invalid_shelter_id_in_request")
    elif interaction == "illegal_constraint":
        try:
            valid_request = float(payload.get("max_walk_time_min")) <= 0 and expected == "REJECT"
        except (TypeError, ValueError):
            valid_request = False
        if valid_request:
            request_errors.append("illegal_constraint_in_request")
    elif interaction == "illegal_budget":
        try:
            valid_request = float(payload.get("budget_units")) <= 0 and expected == "REJECT"
        except (TypeError, ValueError):
            valid_request = False
        if valid_request:
            request_errors.append("illegal_budget_in_request")
    elif interaction == "data_version_mismatch":
        valid_request = payload.get("data_version") != snapshot.data_version and expected == "REJECT"
    elif interaction == "parameter_edit":
        valid_request = bool(payload.get("parent_scenario_id")) and expected == "EXECUTE"
    elif interaction == "safe_stop":
        evidence = scenario.get("safe_stop_evidence") or {}
        valid_request = expected == "SAFE_STOP" and float(evidence.get("observed_population_share", 0)) >= float(evidence.get("target_population_share", 1))
    elif interaction == "complete_request":
        valid_request = expected == "EXECUTE"
    else:
        structural_errors.append("unknown_interaction_type")
    if not valid_request:
        structural_errors.append("request_behavior_contract_invalid")
    if expected in {"REJECT", "CLARIFY"} and scenario.get("negative_type") != "valid_negative_benchmark":
        structural_errors.append("negative_case_not_labelled_valid_negative_benchmark")

    signature = scenario.get("semantic_signature") or scenario.get("fingerprint") or stable_hash(scenario)
    active_seen = seen_signatures if seen_signatures is not None else seen_fingerprints
    duplicate = bool(active_seen is not None and signature in active_seen)
    if duplicate:
        errors.append("duplicate_or_high_similarity_scenario")
    if snapshot.boundary_status != "official":
        warnings.append("boundary_not_official")
    if snapshot.data_status != "VERIFIED":
        warnings.append("data_status_not_verified")
    if not snapshot.a_v2_manifest_match:
        warnings.append("a_v2_manifest_raw_mismatch")
        if not allow_manifest_blocked_demo:
            errors.append("a_v2_manifest_mismatch")
    if not snapshot.a_v2_content_match:
        errors.append("a_v2_manifest_content_mismatch")
    errors.extend(structural_errors)
    errors = list(dict.fromkeys(errors))
    strict_validation_passed = not errors
    structural_valid = not structural_errors and not duplicate
    pilot_accepted = structural_valid and (allow_manifest_blocked_demo or snapshot.a_v2_manifest_match) and snapshot.a_v2_content_match
    manifest_gate = "PASS" if snapshot.a_v2_manifest_match else "BLOCKED_CRLF_FORMAT" if snapshot.a_v2_content_match else "BLOCKED_CONTENT"
    generated_status = "invalid_generated" if structural_errors else "duplicate" if duplicate else "valid"
    if expected in {"REJECT", "CLARIFY"} and structural_valid:
        generated_status = "valid_negative_benchmark"
    return {
        "scenario_id": scenario.get("scenario_id"),
        "semantic_signature": signature,
        "scenario_family_signature": scenario.get("scenario_family_signature"),
        "fingerprint": signature,
        "validation_passed": pilot_accepted,
        "strict_validation_passed": strict_validation_passed,
        "structural_valid": structural_valid,
        "pilot_accepted": pilot_accepted,
        "generated_status": generated_status,
        "valid_negative_benchmark": expected in {"REJECT", "CLARIFY"} and structural_valid,
        "acceptance_class": "accepted_demo_only_manifest_blocked_crlf" if pilot_accepted and manifest_gate == "BLOCKED_CRLF_FORMAT" else "accepted_demo_only" if pilot_accepted else "rejected",
        "eligible_for_verified": False,
        "verified_for_release": False,
        "errors": errors,
        "request_errors": request_errors,
        "warnings": list(dict.fromkeys(warnings)),
        "manifest_gate": manifest_gate,
        "negative_type": scenario.get("negative_type"),
        "expected_behavior": expected,
    }


def select_diverse(validated: list[tuple[dict[str, Any], dict[str, Any]]], *, target: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    accepted = [item for item in validated if item[1]["pilot_accepted"]]
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for item in accepted:
        scenario = item[0]
        key = (scenario["region_id"], scenario["scenario_family"], scenario["expected_behavior"])
        if key not in seen_keys:
            selected.append(item)
            seen_keys.add(key)
    for item in accepted:
        if len(selected) >= target:
            break
        if item not in selected:
            selected.append(item)
    return selected[:target]


def _contract_status(scenario: dict[str, Any]) -> str:
    try:
        TaskRequest(**scenario["b_api_request"])
    except Exception:
        return "NOT_CONTRACT_VALID"
    return "CONTRACT_VALIDATED"


def generate_pilot(*, root: Path = ROOT, candidate_count: int = 48, target_accepted: int = 24, scenario_seed: int = 20261125, allow_manifest_blocked_demo: bool = True, output_dir: str | Path = "benchmark/shelterplan_eval/pilot_stage2_1") -> dict[str, Any]:
    if not 30 <= candidate_count <= 60:
        raise ValueError("candidate_count must be between 30 and 60 for the Stage 2.1 pilot")
    snapshots = load_snapshots(root)
    validated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for index in range(candidate_count):
        region_id = ALLOWED_REGIONS[index % len(ALLOWED_REGIONS)]
        scenario = generate_candidate(snapshots[region_id], index=index, seed=scenario_seed + index)
        result = validate_scenario(scenario, snapshots[region_id], seen_signatures=seen, allow_manifest_blocked_demo=allow_manifest_blocked_demo)
        if result["structural_valid"] and not result["errors"]:
            seen.add(result["semantic_signature"])
        scenario["b_contract_status"] = _contract_status(scenario)
        scenario["validation"] = result
        validated.append((scenario, result))
    selected = select_diverse(validated, target=target_accepted)
    selected_ids = {scenario["scenario_id"] for scenario, _ in selected}
    invalid_generated = [{"scenario_id": s["scenario_id"], "region_id": s["region_id"], "family": s["scenario_family"], "errors": r["errors"], "validation": r} for s, r in validated if r["generated_status"] == "invalid_generated"]
    duplicate = [{"scenario_id": s["scenario_id"], "region_id": s["region_id"], "family": s["scenario_family"], "errors": r["errors"], "validation": r} for s, r in validated if r["generated_status"] == "duplicate"]
    unselected = [{"scenario_id": s["scenario_id"], "region_id": s["region_id"], "family": s["scenario_family"], "expected_behavior": s["expected_behavior"], "validation": r} for s, r in validated if r["scenario_id"] not in selected_ids and r["generated_status"] not in {"invalid_generated", "duplicate"}]
    rejected = [{"scenario_id": s["scenario_id"], "region_id": s["region_id"], "family": s["scenario_family"], "expected_behavior": s["expected_behavior"], "negative_type": s["negative_type"], "validation": r} for s, r in validated if s["expected_behavior"] == "REJECT"]
    manifest_blocked = [{"scenario_id": s["scenario_id"], "region_id": s["region_id"], "manifest_gate": r["manifest_gate"], "validation": r} for s, r in validated if r["manifest_gate"] != "PASS"]
    accepted_scenarios = [scenario for scenario, _ in selected]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scenario_seed": scenario_seed,
        "candidate_count": len(validated),
        "structurally_accepted_count": sum(result["structural_valid"] for _, result in validated),
        "pilot_accepted_count": len(accepted_scenarios),
        "strict_manifest_eligible_count": sum(result["structural_valid"] and result["manifest_gate"] == "PASS" for _, result in validated),
        "content_integrity_eligible_count": sum(result["structural_valid"] and result["manifest_gate"] != "BLOCKED_CONTENT" for _, result in validated),
        "executed_count": sum(scenario["execution_status"] == "EXECUTED" for scenario in accepted_scenarios),
        "oracle_proven_count": sum(scenario["oracle_status"] == "ORACLE_PROVEN" for scenario in accepted_scenarios),
        "rejected_count": len(rejected),
        "valid_negative_benchmark_count": sum(result["valid_negative_benchmark"] for _, result in validated),
        "deduplicated_count": len(duplicate),
        "accepted_region_counts": dict(Counter(s["region_id"] for s in accepted_scenarios)),
        "accepted_family_counts": dict(Counter(s["scenario_family"] for s in accepted_scenarios)),
        "selected_behavior_counts": dict(Counter(s["expected_behavior"] for s in accepted_scenarios)),
        "safe_stop_count": sum(s["expected_behavior"] == "SAFE_STOP" for s in accepted_scenarios),
        "difficulty_distribution": {
            "design_difficulty": dict(Counter(s["design_difficulty"]["decision_complexity"] for s in accepted_scenarios)),
            "measured_difficulty": {"NOT_MEASURED": len(accepted_scenarios)},
        },
        "status_counts": {"invalid_generated": len(invalid_generated), "valid_not_selected": len(unselected), "selected": len(accepted_scenarios), "duplicate": len(duplicate), "manifest_blocked": len(manifest_blocked)},
        "manifest_status": {
            region_id: {"a_v2_manifest_raw_match": snapshot.a_v2_manifest_match, "a_v2_manifest_content_match": snapshot.a_v2_content_match, "data_version": snapshot.data_version, "source_manifest_sha256": snapshot.source_manifest_sha256, "dataset_content_hash": snapshot.dataset_content_hash, "a_v2_manifest_sha256": snapshot.a_v2_manifest_sha256}
            for region_id, snapshot in snapshots.items()
        },
        "policy": {
            "history_merge_data_used": False, "xiaolan_normal_planning_allowed": False,
            "verified_scenarios_generated": False, "manifest_errors_bypass": False,
            "pilot_acceptance_with_manifest_block": allow_manifest_blocked_demo,
            "pilot_acceptance_label": "DEMO_ONLY; not VERIFIED", "temporary_site_supported": False,
            "improvement_option_limit": MAX_API_CANDIDATES, "identity_field": "dataset_content_hash",
            "execution_evidence_path": "reports/stage2_1_b_http_contract.json",
        },
        "stage3_gate": {"required_before_stage3": ["resolve real content mismatches if any; CRLF-only differences need a controlled checkout policy", "run B HTTP execution for selected EXECUTE cases", "capture real Trace and independent Oracle evidence", "do not mark DEMO_ONLY or NOT_EXECUTED as VERIFIED or EXECUTED"]},
    }
    output = root / output_dir
    output.mkdir(parents=True, exist_ok=True)
    candidates = [scenario for scenario, _ in validated]
    (output / "candidates.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "accepted_scenarios.json").write_text(json.dumps(accepted_scenarios, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "reject_log.json").write_text(json.dumps({"invalid_generated": invalid_generated, "duplicate": duplicate, "rejected": rejected, "manifest_blocked": manifest_blocked}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "rejected_log.json").write_text(json.dumps(rejected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "unselected_log.json").write_text(json.dumps(unselected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "reports/stage2_1_pilot_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"summary": summary, "candidates": candidates, "accepted": accepted_scenarios, "reject_log": {"invalid_generated": invalid_generated, "duplicate": duplicate, "rejected": rejected, "manifest_blocked": manifest_blocked}, "unselected": unselected}


def write_summary_report(result: dict[str, Any], root: Path = ROOT) -> Path:
    summary = result["summary"]
    lines = [
        "# ShelterPlan-Eval Stage 2.1 Pilot Summary", "",
        f"- generator: {summary['generator_version']}", f"- seed: {summary['scenario_seed']}",
        f"- candidates: {summary['candidate_count']}", f"- structurally valid: {summary['structurally_accepted_count']}",
        f"- selected: {summary['pilot_accepted_count']}", f"- strict raw Manifest eligible: {summary['strict_manifest_eligible_count']}",
        f"- content-integrity eligible: {summary['content_integrity_eligible_count']}", f"- executed: {summary['executed_count']}",
        f"- Oracle-proven: {summary['oracle_proven_count']}", "",
        "The previous Stage 2 Pilot remains archived under benchmark/shelterplan_eval/pilot. This Stage 2.1 run writes to benchmark/shelterplan_eval/pilot_stage2_1.", "",
        "All selected cases are DEMO_ONLY. measured_difficulty is NOT_MEASURED because no real B Solver Trace or independent Oracle was run. No case is EXECUTED or ORACLE_PROVEN.", "",
        "## Status accounting", "", "| status | count |", "| --- | ---: |",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in summary["status_counts"].items())
    lines.extend(["", "## Expected behaviors", "", "| behavior | selected |", "| --- | ---: |"])
    lines.extend(f"| {key} | {value} |" for key, value in sorted(summary["selected_behavior_counts"].items()))
    lines.extend(["", "## Regions", "", "| region_id | raw Manifest | content integrity | data_version |", "| --- | --- | --- | --- |"])
    lines.extend(f"| {region_id} | {item['a_v2_manifest_raw_match']} | {item['a_v2_manifest_content_match']} | {item['data_version']} |" for region_id, item in sorted(summary["manifest_status"].items()))
    lines.extend(["", "## Contract and release limits", "", "- shelter_ids/base_shelter_ids describe the complete active regional GIS environment.", "- The Module B maximum of 12 applies only to improvement_options.", "- All generated projects are existing-shelter capacity_expansion options. temporary_site is not supported without a complete new-site coordinate and OD contract.", "- CRLF-reconstructed Manifest matches are format evidence, not a VERIFIED release decision.", "- rejected_log.json and unselected_log.json are retained separately.", ""])
    path = root / "reports/stage2_1_pilot_summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Stage 2.1 quality pilot scenarios")
    parser.add_argument("--candidate-count", type=int, default=48)
    parser.add_argument("--target-accepted", type=int, default=24)
    parser.add_argument("--scenario-seed", type=int, default=20261125)
    parser.add_argument("--output-dir", default="benchmark/shelterplan_eval/pilot_stage2_1")
    args = parser.parse_args()
    result = generate_pilot(candidate_count=args.candidate_count, target_accepted=args.target_accepted, scenario_seed=args.scenario_seed, allow_manifest_blocked_demo=True, output_dir=args.output_dir)
    report = write_summary_report(result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"report={report.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
