"""Family-separated synthetic benchmark runner for Module B."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import platform
import time
import uuid

import scipy
from pydantic import ValidationError

from backend.evaluation.baselines import integer_expansion_oracle, integer_max_flow, run_baseline
from backend.evaluation.benchmark_cases import BenchmarkCase, build_cases
from backend.evaluation.fault_injection import run_fault_campaign
from backend.evaluation.refinement import run_offline_refinement
from backend.engine import solve_assignment
from backend.errors import ScenarioError
from backend.models import PlanningScenario, TaskRequest
from backend.graph.executor import _scenario_for_request
from backend.storage import canonical_hash


RUNNABLE = ["StaticWorkflow", "GreedyExpansion", "SkillGraphOnly", "SkillGraphVerifier"]
SPLITS = {"dev", "validation", "test", "all"}


def _safe_rejection(case: BenchmarkCase) -> dict:
    if case.template_family == "scenario_version":
        request = TaskRequest(task_type="assess", data_version="deliberately-wrong-version")
        try:
            _scenario_for_request(case.data, case.scenario, request)
        except ScenarioError as exc:
            rejected = exc.details.get("error_code") == "VERSION_MISMATCH"
            reason = "VERSION_MISMATCH"
        else:
            rejected, reason = False, "request was accepted"
    elif case.template_family == "invalid_input":
        try:
            PlanningScenario(
                scenario_id=case.case_id, data_version=case.data.data_version,
                population_demand_ratio=case.invalid_payload["population_demand_ratio"],
                max_walk_time_min=case.scenario.max_walk_time_min,
            )
        except ValidationError:
            rejected, reason = True, "SCENARIO_SCHEMA_REJECTED"
        else:
            rejected, reason = False, "invalid scenario was accepted"
    else:
        return {}
    return {
        "run_id": None, "case_id": case.case_id, "template_family": case.template_family,
        "task_type": "safe_input_rejection", "baseline_id": "SafeInputReject",
        "data_version": case.data.data_version, "data_status": case.data.data_status,
        "status": "PASS_REJECTED" if rejected else "FAILED", "published": False,
        "expected_rejection": True, "observed_rejection": rejected, "reason": reason,
        "score": {"safe_reject": rejected},
    }


def _case_manifest(case: BenchmarkCase) -> dict:
    return {
        "case_id": case.case_id,
        "template_family": case.template_family,
        "split": case.split,
        "difficulty": case.difficulty,
        "expected_behavior": case.expected_behavior,
        "input_sha256": canonical_hash({
            "data": case.data.model_dump(), "scenario": case.scenario.model_dump(),
            "options": [row.model_dump() for row in case.options],
        }),
        "data_version": case.data.data_version,
        "data_status": case.data.data_status,
        "demand_nodes": len(case.data.demands),
        "shelters": len(case.data.shelters),
        "od_edges": len(case.data.od_edges),
        "option_count": len(case.options),
    }


def _metrics(records: list[dict]) -> dict:
    ran = [row for row in records if row.get("status") == "RUN"]
    verifier_rows = [row for row in ran if row.get("baseline_id") == "SkillGraphVerifier"]
    fault_rows = [row for row in ran if row.get("baseline_id") == "VerifierFaultInjection"]
    numeric_gaps = [row["optimality_gap"] for row in ran if isinstance(row.get("optimality_gap"), (int, float))]
    greedy_gaps = [
        row["optimality_gap"] for row in ran
        if row.get("baseline_id") == "GreedyExpansion" and isinstance(row.get("optimality_gap"), (int, float))
    ]
    feasible = [row for row in ran if row.get("feasible") is not None]
    walk_times = [row["weighted_walk_time_min"] for row in ran if row.get("weighted_walk_time_min") is not None]
    baselines = {}
    for baseline in sorted({row.get("baseline_id") for row in ran}):
        grouped = [row for row in ran if row.get("baseline_id") == baseline]
        served = [row["served_demand"] for row in grouped if row.get("served_demand") is not None]
        baselines[baseline] = {
            "case_count": len({row.get("case_id") for row in grouped}),
            "mean_served_demand": sum(served) / len(served) if served else None,
            "verification_pass_count": sum(row.get("verification_status") in {"PASS_DEMO", "PASS_VERIFIED"} for row in grouped),
        }
    fault_count = sum(int(row.get("fault_count", 0)) for row in fault_rows)
    detected = sum(int(row.get("detected_faults", 0)) for row in fault_rows)
    rejection_records = [row for row in records if row.get("baseline_id") == "SafeInputReject"]
    return {
        "verified_task_success_rate": sum(row.get("verification_status") == "PASS_DEMO" for row in verifier_rows) / max(1, len(verifier_rows)),
        "safe_input_rejection_pass_rate": (sum(row.get("status") == "PASS_REJECTED" for row in rejection_records) / len(rejection_records)) if rejection_records else None,
        "invalid_published_output_count": sum(row.get("published") is True and row.get("status") not in {"RUN"} for row in records),
        "fault_detection_recall": detected / max(1, fault_count),
        "false_positive_clean_cases": sum(bool(row.get("false_positive_clean_case")) for row in fault_rows),
        "mean_optimality_gap_vs_independent_oracle": sum(numeric_gaps) / len(numeric_gaps) if numeric_gaps else None,
        "mean_greedy_expansion_gap_vs_budget_oracle": sum(greedy_gaps) / len(greedy_gaps) if greedy_gaps else None,
        "feasibility_rate": sum(row.get("feasible") is True for row in feasible) / len(feasible) if feasible else None,
        "mean_weighted_walk_time_min": sum(walk_times) / len(walk_times) if walk_times else None,
        "execution_cost_tokens": "N/A; no AgentArts/LLM calls",
        "metrics_by_baseline": baselines,
        "gt_a": "Independent Dinic maximum-flow oracle on integer synthetic cases; budgeted oracle enumerates all option subsets.",
        "gt_b": "Registered SkillGraph execution and independent verifier certificate; internal synthetic evaluation only.",
        "gt_c": "NOT_VALIDATED; no external field authority or ground-truth dataset supplied.",
        "limitations": [
            "All benchmark inputs are deterministic synthetic fixtures generated from frozen code templates.",
            "Template families are assigned to exactly one split; results are internal, not external validation.",
            "FlatToolAgent remains NOT_RUN because no AgentArts traces or model configuration were provided.",
            "Publication escape rate cannot be measured without a public publishing channel; only quarantine flags are recorded.",
        ],
    }


def run(split: str, output: str | Path) -> dict:
    if split not in SPLITS:
        raise ValueError(f"split must be one of {sorted(SPLITS)}")
    all_cases = build_cases()
    family_splits: dict[str, set[str]] = {}
    for case in all_cases:
        family_splits.setdefault(case.template_family, set()).add(case.split)
    if any(len(assigned) != 1 for assigned in family_splits.values()):
        raise RuntimeError("A template family appears in more than one split")
    cases = all_cases if split == "all" else [case for case in all_cases if case.split == split]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    records: list[dict] = []
    for case in cases:
        for baseline in RUNNABLE:
            started = time.perf_counter()
            try:
                result = run_baseline(
                    case.case_id, baseline, data=case.data, scenario=case.scenario,
                    options=list(case.options),
                )
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                served = result.get("served_demand")
                oracle = integer_expansion_oracle(case.data, case.scenario, case.options) if baseline == "GreedyExpansion" else integer_max_flow(case.data, case.scenario)
                gap = None if oracle is None or served is None else max(0.0, (oracle - served) / max(oracle, 1))
                records.append({
                    "run_id": run_id, "case_id": case.case_id, "template_family": case.template_family,
                    "split": case.split, "difficulty": case.difficulty,
                    "task_type": {"StaticWorkflow": "assess", "GreedyExpansion": "optimize", "SkillGraphOnly": "assess", "SkillGraphVerifier": "assess"}[baseline],
                    "baseline_id": baseline, "data_version": case.data.data_version,
                    "data_status": case.data.data_status, "solver_version": f"scipy-{scipy.__version__}",
                    "seed": 0, "status": "RUN", "served_demand": served,
                    "oracle_served_demand": oracle, "optimality_gap": gap,
                    "published": result.get("published"),
                    "score": {"served_demand": served, "optimality_gap": gap},
                    "feasible": result.get("feasible"), "weighted_walk_time_min": result.get("weighted_walk_time_min"),
                    "verification_status": result.get("verification_status", "NOT_RUN"),
                    "elapsed_ms": elapsed, "execution_cost": "token_cost=N/A; no LLM calls",
                    "expected_behavior": case.expected_behavior, "trace": result,
                })
            except Exception as exc:
                records.append({
                    "run_id": run_id, "case_id": case.case_id, "template_family": case.template_family,
                    "split": case.split, "baseline_id": baseline, "data_version": case.data.data_version,
                    "data_status": case.data.data_status, "status": "FAILED", "error": str(exc),
                })

        # Run each case's independent fault campaign on its clean solver result.
        clean = solve_assignment(case.data, case.scenario)
        fault_report = run_fault_campaign(case.data, case.scenario, clean, list(case.options))
        records.append({
            "run_id": run_id, "case_id": case.case_id, "template_family": case.template_family,
            "split": case.split, "difficulty": case.difficulty,
            "task_type": "verifier_fault_injection", "baseline_id": "VerifierFaultInjection",
            "data_version": case.data.data_version, "data_status": case.data.data_status,
            "status": "RUN", "published": False,
            "fault_count": fault_report["fault_count"], "detected_faults": fault_report["detected_faults"],
            "fault_detection_recall": fault_report["fault_detection_recall"],
            "false_positive_clean_case": fault_report["false_positive_clean_case"],
            "score": {"fault_detection_recall": fault_report["fault_detection_recall"], "false_positive_clean_case": fault_report["false_positive_clean_case"]},
            "trace": fault_report,
        })
        rejection = _safe_rejection(case)
        if rejection:
            rejection["run_id"] = run_id
            records.append(rejection)

    records.extend([
        {"run_id": run_id, "case_id": "agentarts_external", "baseline_id": "FlatToolAgent", "status": "NOT_RUN", "reason": "No AgentArts trace or model configuration was supplied.", "data_status": "N/A"},
        {"run_id": run_id, "case_id": "external_field", "baseline_id": "ExternalGT", "status": "NOT_RUN", "reason": "No external field validation authority or dataset was supplied.", "data_status": "N/A"},
    ])
    report = {
        "run_id": run_id, "split": split,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": platform.python_version(), "scipy_version": scipy.__version__,
        "case_count": len(cases), "family_count": len({case.template_family for case in cases}),
        "case_manifest": [_case_manifest(case) for case in cases],
        "records": records, "metrics": _metrics(records),
    }
    refinement = run_offline_refinement(output.parent.parent / "refinement")
    report["refinement_record"] = refinement["record_path"]
    json_path = output / f"{run_id}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    csv_path = output / f"{run_id}.csv"
    fields = sorted({key for row in records for key in row if key != "trace"})
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in records:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items() if key != "trace"})
    md_path = output / f"{run_id}.md"
    lines = [
        f"# Module B {split} benchmark `{run_id}`", "",
        f"Cases: {len(cases)} deterministic synthetic cases across {report['family_count']} family-separated templates.",
        "GT-C: NOT_VALIDATED.", "",
        "| Case | Family | Baseline | Status | Served | Oracle | Gap |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in records:
        if row.get("baseline_id") in {*RUNNABLE, "SafeInputReject"}:
            lines.append(f"| {row.get('case_id')} | {row.get('template_family')} | {row.get('baseline_id')} | {row.get('status')} | {row.get('served_demand', 'N/A')} | {row.get('oracle_served_demand', 'N/A')} | {row.get('optimality_gap', 'N/A')} |")
    lines.extend(["", "## Metrics", "", "```json", json.dumps(report["metrics"], ensure_ascii=False, indent=2), "```", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "json": str(json_path), "csv": str(csv_path), "markdown": str(md_path),
        "refinement": refinement["record_path"], "metrics": report["metrics"],
        "run_id": run_id, "case_count": len(cases), "family_count": report["family_count"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/module_b.yaml", help="Retained for command compatibility; synthetic cases are frozen in code.")
    parser.add_argument("--split", default="all", choices=sorted(SPLITS))
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.split, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
