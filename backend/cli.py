"""Command line interface for read-only data inspection and fixture runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.adapters.a_reader import inspect_data, load_a_bundle
from backend.errors import PlanningError
from backend.graph.executor import execute_task
from backend.models import TaskRequest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect-data")
    inspect.add_argument("--config", default="configs/pilot.yaml")
    inspect.add_argument("--repo-root", default=None, help="Explicit repository root for nested Module A adapters.")
    inspect.add_argument("--load", action="store_true", help="Deprecated alias; complete data validation is now the default.")
    inspect.add_argument("--availability-only", action="store_true", help="Check required file presence without parsing the data.")
    demo = sub.add_parser("demo")
    demo.add_argument("--fixture", required=True)
    demo.add_argument("--task-type", choices=["assess", "optimize", "diagnose_intervene", "stress_test", "compare", "sensitivity"], default="optimize")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect-data":
            availability = inspect_data(args.config, args.repo_root)
            result = {"availability": availability.model_dump()}
            if not args.availability_only and not availability.missing_paths:
                bundle = load_a_bundle(args.config, args.repo_root)
                result["availability"] = bundle.availability.model_dump()
                result["loaded"] = {
                    "data_version": bundle.manifest.data_version,
                    "demands": len(bundle.demands), "shelters": len(bundle.shelters),
                    "od_edges": len(bundle.od_edges), "data_status": bundle.availability.data_status,
                    "a7_status": bundle.manifest.release_status,
                    "source_manifest_sha256": bundle.manifest.source_manifest_hash,
                    "validated_artifacts": bundle.manifest.validated_artifacts,
                }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        fixture = Path(args.fixture)
        if not fixture.is_absolute():
            fixture = Path.cwd() / fixture
        fixture = fixture.resolve()
        from backend.graph.executor import FIXTURE_ROOT
        fixture.relative_to(FIXTURE_ROOT)
        request = TaskRequest(task_type=args.task_type, fixture_id=fixture.stem, analysis_mode="demo")
        result = execute_task(request)
        output_dir = Path("outputs/module_b/runs")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{result['result_id']}.json"
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"result_id": result["result_id"], "result_path": str(output_path), "data_status": result["data_status"], "solver_status": result["solver_status"], "verification_status": result["verification_status"], "result_summary": result["result_summary"], "optimization": result["result_detail"].get("optimization")}, ensure_ascii=False, indent=2))
        return 0
    except PlanningError as exc:
        print(json.dumps({"error_code": exc.error_code, "message": str(exc), "details": exc.details, "retryable": exc.retryable, "required_user_action": exc.required_user_action}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
