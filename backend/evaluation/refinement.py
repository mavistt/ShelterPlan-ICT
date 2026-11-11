"""Offline-only failure classification and bounded routing-candidate review."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

from backend.graph.planner import validate_plan
from backend.models import PlanNode, ProposedSkillPlan
from backend.graph.registry import SKILL_VERSION
from backend.evaluation.benchmark_cases import build_cases
from backend.models import TaskRequest


def run_offline_refinement(output_dir: str | Path = "outputs/module_b/refinement") -> dict:
    """Exercise an omitted-prerequisite failure and record a conservative repair.

    The candidate is evaluated on family-separated validation templates. It remains
    unpromoted because those cases are synthetic and have no external field authority.
    """
    malformed = ProposedSkillPlan(nodes=[PlanNode(node_id="one", skill_id="S01", depends_on=[], skill_version=SKILL_VERSION), PlanNode(node_id="two", skill_id="S12", depends_on=["one"], skill_version=SKILL_VERSION)])
    proposed = validate_plan("assess", malformed)
    validation_cases = [case for case in build_cases() if case.split == "validation"]
    validation_rows = []
    from backend.graph.executor import execute_task
    for case in validation_cases:
        try:
            result = execute_task(
                TaskRequest(task_type="assess", analysis_mode="demo", proposed_skill_plan=malformed),
                source_override=(case.data, case.scenario, list(case.options)),
            )
            passed = result["verification_status"] == "PASS_DEMO" and result["plan_decision"]["status"] == "rewritten"
            validation_rows.append({
                "case_id": case.case_id, "template_family": case.template_family,
                "passed": passed, "verification_status": result["verification_status"],
                "plan_status": result["plan_decision"]["status"],
            })
        except Exception as exc:
            validation_rows.append({
                "case_id": case.case_id, "template_family": case.template_family,
                "passed": False, "error": str(exc),
            })
    passed_count = sum(row["passed"] for row in validation_rows)
    validation_status = "PASS_INTERNAL_SYNTHETIC" if passed_count == len(validation_rows) and validation_rows else "FAIL"
    document = {
        "run_id": str(uuid.uuid4()), "mode": "offline", "source_split": "dev_template_only",
        "trace_ref": "synthetic:missing-required-assignment", "error_taxonomy": "INVALID_PLAN",
        "root_cause_candidate": "Task plan omitted required S02/S03/S04 prerequisites.",
        "candidate": {"type": "registered_template_fallback", "changed_fields": ["routing_policy"], "candidate_status": proposed.status, "reason_codes": proposed.reason_codes},
        "validation": {
            "status": validation_status, "split": "validation", "case_count": len(validation_rows),
            "passed_count": passed_count,
            "success_rate": passed_count / len(validation_rows) if validation_rows else None,
            "families": sorted({row["template_family"] for row in validation_rows}),
            "cases": validation_rows,
            "scope": "family-separated synthetic cases; not external or field validation",
        },
        "decision": "KEEP_REVIEW_REQUIRED" if validation_status == "PASS_INTERNAL_SYNTHETIC" else "REJECT_CANDIDATE_PROMOTION",
        "promotion_requires_human_confirmation": True,
        "metrics": {"accuracy_delta": None, "invalid_published_output_rate": 0.0 if validation_status == "PASS_INTERNAL_SYNTHETIC" else None},
    }
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{document['run_id']}.json"
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    document["record_path"] = str(target)
    return document
