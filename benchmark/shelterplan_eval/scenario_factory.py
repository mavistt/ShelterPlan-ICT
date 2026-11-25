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

from sources.preflight import ROOT, sha256_file


SCHEMA_VERSION = 1
GENERATOR_VERSION = "stage2-pilot-1"
ALLOWED_REGIONS = ("shiqi", "east_district")
ALL_REGION_IDS = ("shiqi", "east_district", "xiaolan")
MAX_API_CANDIDATES = 12
TASK_FAMILIES = (
    {
        "family": "coverage_baseline",
        "decision_complexity": "low",
        "workflow_complexity": "single_stage",
        "interaction_complexity": "complete_request",
        "expected_behavior": "EXECUTE",
    },
    {
        "family": "budget_tradeoff",
        "decision_complexity": "high",
        "workflow_complexity": "conditional_branch",
        "interaction_complexity": "parameter_edit",
        "expected_behavior": "EXECUTE",
    },
    {
        "family": "capacity_competition",
        "decision_complexity": "high",
        "workflow_complexity": "multi_stage",
        "interaction_complexity": "complete_request",
        "expected_behavior": "EXECUTE",
    },
    {
        "family": "facility_disruption",
        "decision_complexity": "medium",
        "workflow_complexity": "conditional_branch",
        "interaction_complexity": "complete_request",
        "expected_behavior": "EXECUTE",
    },
    {
        "family": "multi_stage_recovery",
        "decision_complexity": "high",
        "workflow_complexity": "multi_stage",
        "interaction_complexity": "parameter_edit",
        "expected_behavior": "EXECUTE",
    },
    {
        "family": "clarification_and_safe_stop",
        "decision_complexity": "medium",
        "workflow_complexity": "safe_stop",
        "interaction_complexity": "missing_parameter",
        "expected_behavior": "CLARIFY",
    },
)
REQUIRED_ASSUMPTIONS = {
    "region_id",
    "data_version",
    "source_manifest_sha256",
    "a_v2_manifest_sha256",
    "shelter_ids",
    "disabled_shelter_ids",
    "population_demand_ratio",
    "max_walk_time_min",
    "recovery_projects",
    "budget_units",
    "scenario_seed",
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
    return {
        "parameter": parameter,
        "value": value,
        "source_label": source_label,
        "source_detail": source_detail,
    }


@dataclass(frozen=True)
class RegionSnapshot:
    region_id: str
    data_version: str
    source_manifest_sha256: str
    a_v2_manifest_sha256: str
    a_v2_manifest_match: bool
    enabled: bool
    boundary_status: str
    data_status: str
    all_shelter_ids: tuple[str, ...]
    normal_candidate_ids: tuple[str, ...]
    capacity_by_shelter: dict[str, float]


def load_snapshots(root: Path = ROOT) -> dict[str, RegionSnapshot]:
    source_manifest_path = root / "sources/source_manifest.json"
    source_manifest = read_json(source_manifest_path)
    source_manifest_sha256 = sha256_file(source_manifest_path)
    eligibility = read_csv(root / "sources/eligibility.csv")
    eligibility_by_region: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in eligibility:
        eligibility_by_region[row.get("region_id", "")].append(row)
    manifest_checks = {
        item["region_id"]: item
        for item in source_manifest.get("a_v2_manifests", [])
    }
    region_checks = {
        item["region_id"]: item for item in source_manifest.get("regions", [])
    }
    snapshots: dict[str, RegionSnapshot] = {}
    for region_id in ALL_REGION_IDS:
        region_root = root / "data/processed/regions" / region_id
        region_manifest = read_json(region_root / "manifest.json")
        quality = read_json(region_root / "quality_summary.json")
        region_check = region_checks.get(region_id, {})
        manifest_check = manifest_checks.get(region_id, {})
        rows = read_csv(region_root / "shelters_all.csv")
        all_ids = tuple(sorted(row.get("shelter_id", "") for row in rows if row.get("shelter_id")))
        eligible_rows = {
            row["shelter_id"]: row
            for row in eligibility_by_region.get(region_id, [])
            if row.get("shelter_id")
        }
        normal_ids = tuple(
            sorted(
                shelter_id
                for shelter_id in all_ids
                if (
                    eligible_rows.get(shelter_id, {}).get("eligible_for_normal_planning", "")
                    .strip()
                    .lower()
                    in {"true", "1", "yes"}
                    or (
                        eligible_rows.get(shelter_id, {}).get("eligible_for_demo", "")
                        .strip()
                        .lower()
                        in {"true", "1", "yes"}
                        and bool(region_check.get("enabled", False))
                    )
                )
            )
        )
        capacities: dict[str, float] = {}
        for row in rows:
            try:
                capacities[row["shelter_id"]] = float(
                    row.get("capacity_persons_effective") or row.get("capacity_persons") or 0
                )
            except (TypeError, ValueError):
                capacities[row["shelter_id"]] = 0.0
        snapshots[region_id] = RegionSnapshot(
            region_id=region_id,
            data_version=region_manifest.get("data_version", ""),
            source_manifest_sha256=source_manifest_sha256,
            a_v2_manifest_sha256=sha256_file(region_root / "manifest.json"),
            a_v2_manifest_match=bool(manifest_check.get("all_declared_files_match", False)),
            enabled=bool(region_check.get("enabled", False)),
            boundary_status=str(region_check.get("boundary_status") or "unknown"),
            data_status=str(quality.get("data_status", "unknown")),
            all_shelter_ids=all_ids,
            normal_candidate_ids=normal_ids,
            capacity_by_shelter=capacities,
        )
    return snapshots


def _family(index: int) -> dict[str, Any]:
    return dict(TASK_FAMILIES[index % len(TASK_FAMILIES)])


def _interaction_payload(
    scenario: dict[str, Any], interaction_complexity: str, index: int
) -> dict[str, Any]:
    payload = {
        "region_id": scenario["region_id"],
        "shelter_ids": list(scenario["shelter_ids"]),
        "disabled_shelter_ids": list(scenario["disabled_shelter_ids"]),
        "population_demand_ratio": scenario["population_demand_ratio"],
        "max_walk_time_min": scenario["max_walk_time_min"],
        "recovery_projects": scenario["recovery_projects"],
        "budget_units": scenario["budget_units"],
    }
    if interaction_complexity == "missing_parameter":
        payload.pop("budget_units")
    elif interaction_complexity == "invalid_id":
        payload["shelter_ids"] = list(payload["shelter_ids"]) + [f"invalid-{index:03d}"]
    elif interaction_complexity == "illegal_constraint":
        payload["max_walk_time_min"] = -5
    elif interaction_complexity == "parameter_edit":
        payload["parent_scenario_id"] = f"ST2-parent-{index:03d}"
        payload["population_demand_ratio"] = min(
            1.0, float(payload["population_demand_ratio"]) + 0.05
        )
    return payload


def generate_candidate(
    snapshot: RegionSnapshot,
    *,
    index: int,
    seed: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    family = _family(index)
    candidate_ids = list(snapshot.normal_candidate_ids)
    if not candidate_ids:
        raise ValueError(f"No normal DEMO_ONLY candidate shelters for {snapshot.region_id}")
    shelter_count = 3 + (index % 4)
    rng.shuffle(candidate_ids)
    shelter_ids = sorted(candidate_ids[: min(shelter_count, MAX_API_CANDIDATES)])
    disabled_ids: list[str] = []
    if family["family"] == "facility_disruption":
        remaining = [sid for sid in snapshot.normal_candidate_ids if sid not in shelter_ids]
        if remaining:
            disabled_ids = [remaining[index % len(remaining)]]
    ratio = round(0.2 + 0.05 * (index % 3), 2)
    walk_time = (10, 15, 20, 30)[index % 4]
    project_count = 1 if family["family"] == "coverage_baseline" else 2
    projects = [
        {
            "project_id": f"recovery-{snapshot.region_id}-{index:03d}-{j}",
            "project_type": ("capacity_expansion", "temporary_site")[j % 2],
            "unit_cost": 2 + ((index + j) % 3),
            "capacity_delta_persons": 100 * (j + 1),
            "source_label": "HYPOTHETICAL_RECOVERY_PROJECT",
        }
        for j in range(project_count)
    ]
    budget = sum(project["unit_cost"] for project in projects) + 2 + (index % 3)
    interaction = (
        "invalid_id" if index % 10 == 7
        else "illegal_constraint" if index % 10 == 8
        else family["interaction_complexity"]
    )
    scenario: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "scenario_id": f"ST2-{snapshot.region_id}-{index:03d}",
        "scenario_family": family["family"],
        "region_id": snapshot.region_id,
        "data_version": snapshot.data_version,
        "source_manifest_sha256": snapshot.source_manifest_sha256,
        "a_v2_manifest_sha256": snapshot.a_v2_manifest_sha256,
        "shelter_ids": shelter_ids,
        "disabled_shelter_ids": disabled_ids,
        "population_demand_ratio": ratio,
        "max_walk_time_min": walk_time,
        "recovery_projects": projects,
        "budget_units": budget,
        "scenario_seed": seed,
        "difficulty": {
            "decision_complexity": family["decision_complexity"],
            "workflow_complexity": family["workflow_complexity"],
            "interaction_complexity": interaction,
        },
        "expected_behavior": (
            "REJECT" if interaction in {"invalid_id", "illegal_constraint"}
            else family["expected_behavior"]
        ),
        "provenance": {
            "data_status": snapshot.data_status,
            "boundary_status": snapshot.boundary_status,
            "real_gis_inputs": True,
            "hypothetical_inputs": [
                "population_demand_ratio",
                "recovery_projects",
                "budget_units",
            ],
            "verified_for_release": False,
        },
        "assumptions": [
            assumption("region_id", snapshot.region_id, "A_V2_MANIFEST", "A v2 region manifest"),
            assumption("data_version", snapshot.data_version, "A_V2_MANIFEST", "A v2 region manifest"),
            assumption(
                "source_manifest_sha256",
                snapshot.source_manifest_sha256,
                "EVAL_SOURCE_MANIFEST",
                "sources/source_manifest.json raw SHA-256",
            ),
            assumption(
                "a_v2_manifest_sha256",
                snapshot.a_v2_manifest_sha256,
                "A_V2_MANIFEST",
                "region manifest raw SHA-256",
            ),
            assumption("shelter_ids", shelter_ids, "A_V2_DERIVED", "active DEMO_ONLY candidates"),
            assumption("disabled_shelter_ids", disabled_ids, "EVAL_INPUT", "disruption input"),
            assumption("population_demand_ratio", ratio, "SCENARIO_PARAMETER", "pilot parameter"),
            assumption("max_walk_time_min", walk_time, "SCENARIO_PARAMETER", "pilot parameter"),
            assumption(
                "recovery_projects",
                projects,
                "HYPOTHETICAL_RECOVERY_PROJECT",
                "not an observed commitment",
            ),
            assumption("budget_units", budget, "SCENARIO_PARAMETER", "pilot budget units"),
            assumption("scenario_seed", seed, "GENERATOR_SEED", "deterministic pilot seed"),
        ],
    }
    scenario["request_payload"] = _interaction_payload(
        scenario, interaction, index
    )
    scenario["fingerprint"] = stable_hash(
        {
            key: scenario[key]
            for key in (
                "region_id",
                "data_version",
                "scenario_family",
                "shelter_ids",
                "disabled_shelter_ids",
                "population_demand_ratio",
                "max_walk_time_min",
                "recovery_projects",
                "budget_units",
                "difficulty",
                "request_payload",
            )
        }
    )
    return scenario


def validate_scenario(
    scenario: dict[str, Any],
    snapshot: RegionSnapshot,
    *,
    seen_fingerprints: set[str] | None = None,
    allow_manifest_blocked_demo: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if scenario.get("region_id") != snapshot.region_id:
        errors.append("region_id_mismatch")
    if snapshot.region_id not in ALLOWED_REGIONS or not snapshot.enabled:
        errors.append("disabled_region")
    if scenario.get("data_version") != snapshot.data_version:
        errors.append("data_version_mismatch")
    if scenario.get("source_manifest_sha256") != snapshot.source_manifest_sha256:
        errors.append("source_manifest_mismatch")
    if scenario.get("a_v2_manifest_sha256") != snapshot.a_v2_manifest_sha256:
        errors.append("a_v2_manifest_mismatch")
    if not str(scenario.get("data_version", "")).startswith("shelterplan_module_a_v2.0-"):
        errors.append("non_a_v2_or_historical_data_version")
    if not snapshot.a_v2_manifest_match:
        warnings.append("a_v2_manifest_mismatch")
        if not allow_manifest_blocked_demo:
            errors.append("a_v2_manifest_mismatch")
    shelter_ids = list(scenario.get("shelter_ids", []))
    disabled_ids = list(scenario.get("disabled_shelter_ids", []))
    if not shelter_ids:
        errors.append("empty_shelter_ids")
    if len(shelter_ids) > MAX_API_CANDIDATES:
        errors.append("api_candidate_limit_exceeded")
    if not set(shelter_ids).issubset(set(snapshot.normal_candidate_ids)):
        errors.append("invalid_or_non_admissible_shelter_id")
    if not set(disabled_ids).issubset(set(snapshot.all_shelter_ids)):
        errors.append("invalid_disabled_shelter_id")
    if set(shelter_ids) & set(disabled_ids):
        errors.append("disabled_shelter_selected")
    try:
        ratio = float(scenario["population_demand_ratio"])
        if not 0 < ratio <= 1:
            errors.append("population_demand_ratio_out_of_range")
    except (KeyError, TypeError, ValueError):
        errors.append("population_demand_ratio_invalid")
    try:
        walk_time = float(scenario["max_walk_time_min"])
        if not 0 < walk_time <= 120:
            errors.append("max_walk_time_invalid")
    except (KeyError, TypeError, ValueError):
        errors.append("max_walk_time_invalid")
    try:
        budget = float(scenario["budget_units"])
        project_cost = sum(float(project["unit_cost"]) for project in scenario["recovery_projects"])
        if budget <= 0 or project_cost > budget:
            errors.append("budget_constraint_invalid")
    except (KeyError, TypeError, ValueError):
        errors.append("budget_constraint_invalid")
    if not isinstance(scenario.get("scenario_seed"), int):
        errors.append("scenario_seed_invalid")
    if not isinstance(scenario.get("recovery_projects"), list):
        errors.append("recovery_projects_invalid")
    for project in scenario.get("recovery_projects", []):
        try:
            invalid_project = (
                not project.get("project_id")
                or not project.get("source_label")
                or float(project.get("unit_cost", 0)) <= 0
            )
        except (TypeError, ValueError):
            invalid_project = True
        if invalid_project:
            errors.append("recovery_project_missing_source_or_cost")
    assumption_names = {
        item.get("parameter")
        for item in scenario.get("assumptions", [])
        if item.get("source_label")
    }
    missing_assumptions = REQUIRED_ASSUMPTIONS - assumption_names
    if missing_assumptions:
        errors.append("missing_assumption_source:" + ",".join(sorted(missing_assumptions)))
    interaction = scenario.get("difficulty", {}).get("interaction_complexity")
    payload = scenario.get("request_payload", {})
    if interaction == "missing_parameter":
        if scenario.get("expected_behavior") != "CLARIFY":
            errors.append("missing_parameter_without_clarify")
        if "budget_units" in payload:
            errors.append("missing_parameter_case_not_missing")
    elif interaction == "invalid_id":
        if set(payload.get("shelter_ids", [])) <= set(snapshot.normal_candidate_ids):
            errors.append("invalid_id_case_not_invalid")
        else:
            errors.append("invalid_shelter_id_in_request")
        if scenario.get("expected_behavior") != "REJECT":
            errors.append("invalid_id_without_reject")
    elif interaction == "illegal_constraint":
        try:
            valid_constraint = float(payload.get("max_walk_time_min", 0)) > 0
        except (TypeError, ValueError):
            valid_constraint = False
        if valid_constraint:
            errors.append("illegal_constraint_case_not_invalid")
        else:
            errors.append("illegal_constraint_in_request")
        if scenario.get("expected_behavior") != "REJECT":
            errors.append("illegal_constraint_without_reject")
    elif interaction == "parameter_edit" and not payload.get("parent_scenario_id"):
        errors.append("parameter_edit_missing_parent")
    fingerprint = scenario.get("fingerprint") or stable_hash(scenario)
    if seen_fingerprints is not None and fingerprint in seen_fingerprints:
        errors.append("duplicate_or_high_similarity_scenario")
    if snapshot.boundary_status != "official":
        warnings.append("boundary_not_official")
    if snapshot.data_status != "VERIFIED":
        warnings.append("data_status_not_verified")
    pilot_accepted = not errors
    acceptance_class = (
        "accepted_demo_only_manifest_blocked"
        if pilot_accepted and not snapshot.a_v2_manifest_match
        else "accepted_demo_only"
        if pilot_accepted
        else "rejected"
    )
    return {
        "scenario_id": scenario.get("scenario_id"),
        "fingerprint": fingerprint,
        "validation_passed": not errors,
        "pilot_accepted": pilot_accepted,
        "acceptance_class": acceptance_class,
        "eligible_for_verified": False,
        "verified_for_release": False,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "manifest_gate": "PASS" if snapshot.a_v2_manifest_match else "BLOCKED",
    }


def select_diverse(
    validated: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    target: int,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    accepted = [item for item in validated if item[1]["pilot_accepted"]]
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    family_region_counts: Counter[tuple[str, str]] = Counter()
    for item in accepted:
        scenario = item[0]
        key = (scenario["region_id"], scenario["scenario_family"])
        if family_region_counts[key] < 2:
            selected.append(item)
            family_region_counts[key] += 1
    for item in accepted:
        if len(selected) >= target:
            break
        if item not in selected:
            selected.append(item)
    return selected[:target]


def generate_pilot(
    *,
    root: Path = ROOT,
    candidate_count: int = 40,
    target_accepted: int = 24,
    scenario_seed: int = 20261125,
    allow_manifest_blocked_demo: bool = True,
) -> dict[str, Any]:
    if not 30 <= candidate_count <= 50:
        raise ValueError("candidate_count must be between 30 and 50 for the Stage 2 pilot")
    snapshots = load_snapshots(root)
    validated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for index in range(candidate_count):
        region_id = ALLOWED_REGIONS[index % len(ALLOWED_REGIONS)]
        scenario = generate_candidate(
            snapshots[region_id], index=index, seed=scenario_seed + index
        )
        result = validate_scenario(
            scenario,
            snapshots[region_id],
            seen_fingerprints=seen,
            allow_manifest_blocked_demo=allow_manifest_blocked_demo,
        )
        if result["pilot_accepted"]:
            seen.add(result["fingerprint"])
        scenario["validation"] = result
        validated.append((scenario, result))
    accepted = select_diverse(validated, target=target_accepted)
    accepted_ids = {scenario["scenario_id"] for scenario, _ in accepted}
    reject_log = [
        {
            "scenario_id": scenario["scenario_id"],
            "region_id": scenario["region_id"],
            "scenario_family": scenario["scenario_family"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "validation": result,
        }
        for scenario, result in validated
        if scenario["scenario_id"] not in accepted_ids
    ]
    accepted_scenarios = [scenario for scenario, _ in accepted]
    dimension_counts = {
        dimension: dict(
            Counter(scenario["difficulty"][dimension] for scenario in accepted_scenarios)
        )
        for dimension in (
            "decision_complexity",
            "workflow_complexity",
            "interaction_complexity",
        )
    }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scenario_seed": scenario_seed,
        "candidate_count": len(validated),
        "structurally_accepted_count": sum(
            result["validation_passed"] for _, result in validated
        ),
        "pilot_accepted_count": len(accepted_scenarios),
        "strict_manifest_eligible_count": sum(
            result["validation_passed"] and result["manifest_gate"] == "PASS"
            for _, result in validated
        ),
        "rejected_count": len(reject_log),
        "deduplicated_count": len(validated)
        - len({result["fingerprint"] for _, result in validated}),
        "accepted_region_counts": dict(Counter(s["region_id"] for s in accepted_scenarios)),
        "accepted_family_counts": dict(Counter(s["scenario_family"] for s in accepted_scenarios)),
        "difficulty_distribution": dimension_counts,
        "manifest_status": {
            region_id: {
                "a_v2_manifest_match": snapshot.a_v2_manifest_match,
                "data_version": snapshot.data_version,
                "source_manifest_sha256": snapshot.source_manifest_sha256,
                "a_v2_manifest_sha256": snapshot.a_v2_manifest_sha256,
            }
            for region_id, snapshot in snapshots.items()
        },
        "policy": {
            "history_merge_data_used": False,
            "xiaolan_normal_planning_allowed": False,
            "verified_scenarios_generated": False,
            "manifest_errors_bypass": False,
            "pilot_acceptance_with_manifest_block": allow_manifest_blocked_demo,
            "pilot_acceptance_label": "DEMO_ONLY; not VERIFIED",
        },
        "stage3_contract": {
            "gt_required_fields": [
                "scenario_id",
                "expected_behavior",
                "feasibility_constraints",
                "oracle_source",
                "independent_oracle_version",
            ],
            "oracle_requirements": [
                "independent_from_B_solver",
                "region_id_and_data_version_bound",
                "source_manifest_sha256_bound",
                "reproducible_seed",
                "explicit_unscorable_reason_when_manifest_blocked",
            ],
        },
    }
    output_dir = root / "benchmark/shelterplan_eval/pilot"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "candidates.json").write_text(
        json.dumps([scenario for scenario, _ in validated], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "accepted_scenarios.json").write_text(
        json.dumps(accepted_scenarios, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "reject_log.json").write_text(
        json.dumps(reject_log, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "reports/stage2_pilot_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "summary": summary,
        "candidates": [scenario for scenario, _ in validated],
        "accepted": accepted_scenarios,
        "reject_log": reject_log,
    }


def write_summary_report(result: dict[str, Any], root: Path = ROOT) -> Path:
    summary = result["summary"]
    lines = [
        "# ShelterPlan-Eval Stage 2 Pilot Summary",
        "",
        f"- generator: {summary['generator_version']}",
        f"- seed: {summary['scenario_seed']}",
        f"- candidates: {summary['candidate_count']}",
        f"- structurally accepted: {summary['structurally_accepted_count']}",
        f"- pilot accepted: {summary['pilot_accepted_count']}",
        f"- strict Manifest eligible: {summary['strict_manifest_eligible_count']}",
        f"- rejected: {summary['rejected_count']}",
        f"- deduplicated: {summary['deduplicated_count']}",
        "",
        "Pilot accepted scenarios are DEMO_ONLY structural candidates. Because the current A v2 Manifest has blocking mismatches, none is VERIFIED or eligible for a verified release.",
        "",
        "## Region distribution",
        "",
        "| region_id | accepted |",
        "| --- | ---: |",
    ]
    lines.extend(
        f"| {region_id} | {count} |"
        for region_id, count in sorted(summary["accepted_region_counts"].items())
    )
    lines.extend(["", "## Scenario family distribution", "", "| family | accepted |", "| --- | ---: |"])
    lines.extend(
        f"| {family} | {count} |"
        for family, count in sorted(summary["accepted_family_counts"].items())
    )
    lines.extend(["", "## Difficulty distribution"])
    for dimension, counts in summary["difficulty_distribution"].items():
        lines.extend(["", f"### {dimension}", "", "| level | count |", "| --- | ---: |"])
        lines.extend(f"| {level} | {count} |" for level, count in sorted(counts.items()))
    lines.extend(
        [
            "",
            "## Manifest status",
            "",
            "| region_id | manifest match | data_version |",
            "| --- | --- | --- |",
        ]
    )
    lines.extend(
        f"| {region_id} | {item['a_v2_manifest_match']} | {item['data_version']} |"
        for region_id, item in sorted(summary["manifest_status"].items())
    )
    lines.extend(
        [
            "",
            "Reject details are retained in benchmark/shelterplan_eval/pilot/reject_log.json. The strict validator rejects Manifest errors; the pilot generator only labels structurally valid cases as DEMO_ONLY and carries the blocking warning forward.",
            "",
        ]
    )
    path = root / "reports/stage2_pilot_summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Stage 2 pilot scenarios")
    parser.add_argument("--candidate-count", type=int, default=40)
    parser.add_argument("--target-accepted", type=int, default=24)
    parser.add_argument("--scenario-seed", type=int, default=20261125)
    parser.add_argument("--output", default=None, help="reserved for compatibility")
    args = parser.parse_args()
    result = generate_pilot(
        candidate_count=args.candidate_count,
        target_accepted=args.target_accepted,
        scenario_seed=args.scenario_seed,
        allow_manifest_blocked_demo=True,
    )
    report = write_summary_report(result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"report={report.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
