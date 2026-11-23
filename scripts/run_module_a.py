from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from module_a_v2_lib import load_config, run_region


ROOT = Path(__file__).resolve().parents[1]


def compact_result(result: dict) -> dict:
    compact = {key: value for key, value in result.items() if key not in {"shelters"}}
    shelter_result = result.get("shelters") or {}
    if isinstance(shelter_result, dict):
        compact["shelters"] = {key: value for key, value in shelter_result.items() if key != "shelters"}
    return compact


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the config-driven ShelterPlan Module A v2 pipeline.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all-regions", action="store_true")
    group.add_argument("--region-id")
    parser.add_argument("--reuse-legacy-east", action="store_true", help="Reuse the preserved v0.1 East graph when public Overpass is unavailable.")
    parser.add_argument("--skip-network-supplementary", action="store_true", help="Skip external network calls for non-East regions.")
    args = parser.parse_args()
    cfg = load_config()
    region_ids = list(cfg["regions"]) if args.all_regions else [args.region_id]
    if any(region_id not in cfg["regions"] for region_id in region_ids):
        raise SystemExit(f"Unknown region_id; expected one of {list(cfg['regions'])}")
    results = []
    for region_id in region_ids:
        is_east = region_id == "east_district"
        result = run_region(
            region_id,
            force_legacy_network=bool(is_east and args.reuse_legacy_east),
            skip_network=bool((not is_east) and args.skip_network_supplementary),
        )
        results.append(compact_result(result))
    scenario_script = ROOT / "scripts" / "generate_scenarios.py"
    import subprocess
    scenario_run = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), str(scenario_script)], cwd=ROOT, text=True, capture_output=True)
    progress = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_version": cfg["project"]["data_version"],
        "regions": results,
        "scenario_generator_returncode": scenario_run.returncode,
        "scenario_generator_stdout": scenario_run.stdout.strip(),
        "scenario_generator_stderr": scenario_run.stderr.strip(),
        "status": "PASS_DEMO" if all(item.get("status") == "DEMO_ONLY" for item in results) and scenario_run.returncode == 0 else "PARTIAL",
    }
    progress_path = ROOT / "docs" / "development_progress.json"
    progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": progress["status"], "regions": [item.get("region_id") for item in results], "progress": str(progress_path)}, ensure_ascii=False))
    return 0 if progress["status"] in {"PASS_DEMO", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
