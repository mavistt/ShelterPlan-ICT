"""Build the compact, reproducible Module A release package."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark.shelterplan_eval.difficulty_discovery import ROOT, run as run_discovery


GENERATOR_VERSION = "module-a-release-1.0"
PILOT_REPLAY_IDS = {
    "ST21-shiqi-000",
    "ST21-east_district-001",
    "ST21-shiqi-002",
    "ST21-east_district-003",
    "ST21-shiqi-004",
    "ST21-shiqi-016",
    "ST21-east_district-029",
    "ST21-east_district-011",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_type(scenario: dict[str, Any]) -> str:
    interaction = str(scenario.get("interaction_type"))
    if interaction == "missing_parameter":
        return "clarify"
    if interaction in {"invalid_id", "illegal_constraint", "illegal_budget", "data_version_mismatch"}:
        return "reject"
    if interaction == "safe_stop":
        return "safe_stop"
    if scenario.get("scenario_family") in {"coverage_baseline", "facility_disruption"}:
        return "assess"
    return "optimize"


def _user_turns(scenario: dict[str, Any]) -> list[str]:
    region = scenario["region_id"]
    version = scenario["data_version"]
    family = scenario["scenario_family"]
    interaction = scenario["interaction_type"]
    if interaction == "missing_parameter":
        return [f"请基于 {region} 的 {version} 数据做 {family} 规划，但我还没有提供预算。请先追问缺少的预算参数。"]
    if interaction == "safe_stop":
        return [f"请检查 {region} 在 {scenario['max_walk_time_min']} 分钟步行阈值下的恢复服务；如果现有 A 数据无法支持我要求的目标，请给出有证据的 SAFE_STOP。"]
    if interaction == "invalid_id":
        return [f"请在 {region} 数据中使用一个不存在的避难所进行恢复规划，并明确拒绝非法场所。"]
    if interaction == "illegal_constraint":
        return [f"请在 {region} 数据上使用负的步行阈值执行规划；如果约束非法，应在 Solver 前拒绝。"]
    if interaction == "illegal_budget":
        return [f"请在 {region} 数据上使用负预算执行规划；如果预算非法，应在 Solver 前拒绝。"]
    if interaction == "data_version_mismatch":
        return [f"请用错误的数据版本请求 {region} 规划；必须拒绝版本不匹配，而不能静默回退。"]
    if interaction == "parameter_edit":
        return [
            f"第一轮：基于 {region} 的 {version} 数据制定 {family} 方案。",
            "第二轮：在上一轮场景状态上继续恢复，并保留父子场景、区域和数据版本关系。",
        ]
    return [f"请基于 {region} 的 {version} 数据完成 {family} 评估，并保留可验证的 A 侧证据。"]


def _gt_assertions(scenario: dict[str, Any]) -> dict[str, Any]:
    interaction = str(scenario.get("interaction_type"))
    expected = str(scenario.get("expected_behavior"))
    error_map = {
        "invalid_id": "CANDIDATE_INVALID",
        "illegal_constraint": "SCENARIO_INVALID",
        "illegal_budget": "SCENARIO_INVALID",
        "data_version_mismatch": "VERSION_MISMATCH",
    }
    expected_errors = [error_map[interaction]] if interaction in error_map else []
    phase_map = {
        "invalid_id": "candidate_validation",
        "illegal_constraint": "request_validation",
        "illegal_budget": "request_validation",
        "data_version_mismatch": "source_routing",
    }
    return {
        "expected_behavior": expected,
        "region_id": scenario["region_id"],
        "data_version": scenario["data_version"],
        "analysis_mode": "demo",
        "budget_units": scenario.get("budget_units"),
        "allowed_option_ids": [row["option_id"] for row in scenario.get("improvement_options", [])],
        "disabled_shelter_ids": list(scenario.get("disabled_shelter_ids", [])),
        "parent_state_required": interaction == "parameter_edit",
        "reject_contract": {
            "expected_error_codes": expected_errors,
            "expected_failure_phase": phase_map.get(interaction),
            "solver_must_not_run": expected in {"REJECT", "CLARIFY"},
        },
        "safe_stop_evidence_required": expected == "SAFE_STOP",
    }


def build_task(scenario: dict[str, Any], difficulty: dict[str, Any], *, source_manifest_sha256: str, region_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "task_id": f"A-{scenario['scenario_id']}",
        "scenario_id": scenario["scenario_id"],
        "region_id": scenario["region_id"],
        "data_version": scenario["data_version"],
        "dataset_content_hash": scenario["dataset_content_hash"],
        "scenario_source_manifest_sha256": scenario["source_manifest_sha256"],
        "release_source_manifest_sha256": source_manifest_sha256,
        "scenario_family": scenario["scenario_family"],
        "task_type": _task_type(scenario),
        "user_turns": _user_turns(scenario),
        "expected_behavior": scenario["expected_behavior"],
        "gt_assertions": _gt_assertions(scenario),
        "difficulty_evidence": difficulty,
        "oracle": difficulty["oracle"],
        "provenance": {
            "scenario_file": "benchmark/shelterplan_eval/pilot_stage2_1/accepted_scenarios.json",
            "source_manifest_file": "sources/source_manifest.json",
            "a_v2_region_manifest_sha256": scenario["a_v2_manifest_sha256"],
            "a_v2_content_match": bool(region_manifest.get("a_v2_content_match")),
            "a_v2_raw_manifest_match": bool(region_manifest.get("a_v2_manifest_match")),
            "data_status": region_manifest.get("data_status", "DEMO_ONLY"),
            "boundary_status": region_manifest.get("boundary_status", "unknown"),
            "verified_for_release": False,
            "runtime_execution": "NOT_RUN",
            "hypothetical_inputs": ["population_demand_ratio", "recovery_projects", "budget_units"],
        },
        "split": "frozen_eval",
        "family_group": scenario["scenario_family"],
        "pilot_replay": scenario["scenario_id"] in PILOT_REPLAY_IDS,
    }


def _release_report(tasks: list[dict[str, Any]], difficulty: dict[str, Any], source_manifest: dict[str, Any], artifacts: dict[str, str]) -> str:
    behavior_counts = Counter(str(row["expected_behavior"]) for row in tasks)
    region_counts = Counter(str(row["region_id"]) for row in tasks)
    lines = [
        "# Module A / ShelterPlan-Eval Release",
        "",
        "This is the A-only handoff package for the current iteration. It freezes the existing Stage 2.1 pool, adds measured A-side structural evidence, and supplies pure GT/evaluation contracts. It does not run or claim B, C, LLM, Agent, or frontend integration.",
        "",
        "## Release contents",
        "",
        f"- Task dataset: `{len(tasks)}` frozen tasks, grouped by scenario family.",
        f"- Evaluation set: `{len(tasks)}` tasks; the eight historical Stage 3 cases are marked `pilot_replay=true`.",
        f"- Regions: `{dict(sorted(region_counts.items()))}`.",
        f"- Expected behaviors: `{dict(sorted(behavior_counts.items()))}`.",
        f"- Difficulty evidence: `{difficulty['generator_version']}`; all evidence is A-side structural/reachability evidence.",
        "- Split policy: the available pool is too small for a training split; all 24 tasks are a compact frozen evaluation set. No task is silently reused as training data.",
        "",
        "## Evidence boundary",
        "",
        f"- A v2 content identity is `{source_manifest.get('policy', {}).get('dataset_identity_version')}` with stable dataset hash `{source_manifest.get('dataset_content_hash')}`.",
        "- All admitted regions remain `DEMO_ONLY`; the release does not upgrade them to `VERIFIED`.",
        "- Raw Manifest comparison remains blocked by 84 CRLF-only transport differences while canonical content matches. The raw files and manifests were not rewritten.",
        "- Real GIS cases have `oracle.status=NOT_PROVEN`: the A-side flow engine is independently tested on small fixtures, but no full GIS portfolio optimality proof is claimed here.",
        "- `ST21-shiqi-004` has a multi-stage structure but no damaged/disabled shelter input; its recovery difficulty is structural-only until a runtime lineage trace exists.",
        "",
        "## Reproduction",
        "",
        "```powershell",
        "python -B -m benchmark.shelterplan_eval.release",
        "```",
        "The command regenerates only A-owned release artifacts under `reports/final_release/` and never invokes B/C/LLM.",
        "",
        "## Artifact hashes",
        "",
    ]
    for path, digest in sorted(artifacts.items()):
        lines.append(f"- `{path}`: `{digest}`")
    return "\n".join(lines) + "\n"


def build_release(*, root: Path = ROOT, output_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    output_dir = (output_dir or root / "reports/final_release").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_manifest_path = root / "sources/source_manifest.json"
    source_manifest = _read_json(source_manifest_path)
    scenarios = _read_json(root / "benchmark/shelterplan_eval/pilot_stage2_1/accepted_scenarios.json")
    difficulty = run_discovery(root=root, output_dir=output_dir)
    difficulty_by_id = {row["scenario_id"]: row for row in difficulty["evidence"]}
    regions = {row["region_id"]: row for row in source_manifest.get("regions", [])}
    source_manifest_sha256 = _sha256(source_manifest_path)
    tasks = [
        build_task(
            scenario,
            difficulty_by_id[scenario["scenario_id"]],
            source_manifest_sha256=source_manifest_sha256,
            region_manifest=regions.get(scenario["region_id"], {}),
        )
        for scenario in scenarios
    ]
    dataset = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "dataset_kind": "compact_frozen_task_dataset",
        "split_policy": "frozen_eval_only_by_scenario_family",
        "source_manifest_sha256": source_manifest_sha256,
        "dataset_content_hash": source_manifest.get("dataset_content_hash"),
        "task_count": len(tasks),
        "tasks": tasks,
    }
    evaluation = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "evaluation_kind": "module_a_frozen_eval_set",
        "task_ids": [row["task_id"] for row in tasks],
        "pilot_replay_task_ids": [row["task_id"] for row in tasks if row["pilot_replay"]],
        "scoring_contract": {
            "evaluator": "benchmark.shelterplan_eval.evaluator.evaluate_task",
            "hard_gate": "behavioral contract plus A/B evidence supplied by the caller",
            "dimensions": {"U": "verified end-to-end task success", "W": "cross-stage constraint fidelity", "P": "oracle/recovery quality when applicable", "E": "evidence completeness"},
            "unscorable_policy": "NOT_RUN, UNSCORABLE and INFRA_ERROR are not counted as success",
        },
        "tasks": [
            {
                "task_id": row["task_id"],
                "scenario_id": row["scenario_id"],
                "scenario_family": row["scenario_family"],
                "split": row["split"],
                "pilot_replay": row["pilot_replay"],
                "expected_behavior": row["expected_behavior"],
                "oracle_status": row["oracle"]["status"],
            }
            for row in tasks
        ],
    }
    task_path = output_dir / "task_dataset.json"
    eval_path = output_dir / "evaluation_set.json"
    task_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    eval_path.write_text(json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifact_paths = {
        "reports/final_release/difficulty_discovery.json": output_dir / "difficulty_discovery.json",
        "reports/final_release/difficulty_discovery.md": output_dir / "difficulty_discovery.md",
        "reports/final_release/task_dataset.json": task_path,
        "reports/final_release/evaluation_set.json": eval_path,
    }
    artifact_hashes = {key: _sha256(path) for key, path in artifact_paths.items()}
    release_manifest = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "release_status": "A_ENGINEERING_COMPLETE_DEMO_ONLY",
        "source_manifest_sha256": source_manifest_sha256,
        "dataset_content_hash": source_manifest.get("dataset_content_hash"),
        "task_count": len(tasks),
        "oracle_status_counts": dict(Counter(row["oracle"]["status"] for row in tasks)),
        "artifacts": artifact_hashes,
        "limitations": [
            "A v2 remains DEMO_ONLY and raw Manifest has CRLF-only mismatch evidence.",
            "Real GIS portfolio optimality is NOT_PROVEN in this A-only package.",
            "No LLM/Agent execution or fairness comparison is included.",
        ],
    }
    manifest_path = output_dir / "release_manifest.json"
    manifest_path.write_text(json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = output_dir / "MODULE_A_RELEASE_REPORT.md"
    report_path.write_text(_release_report(tasks, difficulty, source_manifest, artifact_hashes), encoding="utf-8")
    return {"release_manifest": release_manifest, "report": str(report_path), "tasks": tasks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = build_release(root=args.root, output_dir=args.output_dir)
    print(json.dumps({"release_status": result["release_manifest"]["release_status"], "task_count": len(result["tasks"]), "report": result["report"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
