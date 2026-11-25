"""Read-only structural, version and checksum audit of configured Module A files."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import gc
from pathlib import Path
import uuid
from collections import Counter

import yaml

from backend.adapters.a_reader import _sha256, inspect_data

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _hash(path: Path) -> str:
    return _sha256(path)


def _profile_parquet(path: Path, demand_ids: set[str], shelter_ids: set[str], population: dict[str, float], ratio: float, max_snap: float, threshold: float) -> dict:
    import pyarrow.parquet as pq
    parquet = pq.ParquetFile(path)
    required = {"demand_id", "shelter_id", "distance_m", "time_min", "reachable", "reason", "demand_snap_m", "shelter_snap_m", "walking_speed_m_per_s", "data_version"}
    columns = set(parquet.schema_arrow.names)
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"Parquet schema is missing fields: {missing}")
    selected = ["demand_id", "shelter_id", "time_min", "reachable", "reason", "demand_snap_m", "shelter_snap_m", "data_version"]
    pairs, seen_demands, seen_shelters = set(), set(), set()
    versions, reasons = Counter(), Counter()
    threshold_pairs = Counter()
    threshold_demands = {10.0: set(), 15.0: set(), 20.0: set()}
    rows = unreachable_time_null = reachable_over_scenario = reachable_over_snap = 0
    for batch in parquet.iter_batches(batch_size=16384, columns=selected):
        data = {name: batch.column(name).to_pylist() for name in selected}
        for did, sid, time_min, reachable, reason, demand_snap, shelter_snap, version in zip(*(data[name] for name in selected)):
            did, sid, reason, version = str(did), str(sid), str(reason), str(version)
            pairs.add((did, sid)); seen_demands.add(did); seen_shelters.add(sid)
            versions[version] += 1; reasons[(reason, bool(reachable))] += 1
            if not reachable and time_min is None:
                unreachable_time_null += 1
            if reachable:
                if float(time_min) > threshold:
                    reachable_over_scenario += 1
                if float(demand_snap) > max_snap or float(shelter_snap) > max_snap:
                    reachable_over_snap += 1
                for limit in threshold_demands:
                    if float(time_min) <= limit:
                        threshold_pairs[limit] += 1
                        threshold_demands[limit].add(did)
            rows += 1
    thresholds = {}
    for limit, ids in threshold_demands.items():
        reachable_pop = sum(population[did] for did in ids if did in population)
        thresholds[str(int(limit))] = {
            "eligible_od_pairs": threshold_pairs[limit],
            "demand_nodes_with_any_eligible_pair": len(ids),
            "reachable_population_estimate": reachable_pop,
            "population_share": reachable_pop / sum(population.values()) if population else None,
            "scenario_demand_persons": reachable_pop * ratio,
        }
    return {
        "rows": rows,
        "columns": list(parquet.schema_arrow.names),
        "dtypes": {field.name: str(field.type) for field in parquet.schema_arrow},
        "versions": dict(versions),
        "reasons": {f"{key[0]}|{key[1]}": value for key, value in reasons.items()},
        "pairs_unique": len(pairs) == rows,
        "demand_ids_match": seen_demands == demand_ids,
        "shelter_ids_match": seen_shelters == shelter_ids,
        "complete_cartesian": rows == len(demand_ids) * len(shelter_ids) and len(pairs) == rows,
        "unreachable_time_null_count": unreachable_time_null,
        "reachable_rows_over_scenario_threshold": reachable_over_scenario,
        "reachable_rows_over_snap_limit": reachable_over_snap,
        "thresholds": thresholds,
    }


def run_audit(output_dir: str | Path = "outputs/module_b/real_data") -> dict:
    root = PROJECT_ROOT
    config_path = root / "configs/pilot.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    availability = inspect_data(config_path, root).model_dump()
    if availability["missing_paths"]:
        raise RuntimeError(f"Configured A artifacts are missing: {availability['missing_paths']}")

    paths = config["paths"]
    a7_path = root / paths.get("a7_quality_summary", "data/processed/a7_quality_summary.json")
    manifest_path = root / paths["manifest"]
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shelters_doc = json.loads((root / paths["shelters_geojson"]).read_text(encoding="utf-8"))
    demands_doc = json.loads((root / paths["demand_nodes"]).read_text(encoding="utf-8"))
    shelter_crs = shelters_doc.get("crs")
    demand_crs = demands_doc.get("crs")
    shelter_features = shelters_doc.get("features", [])
    demand_features = demands_doc.get("features", [])
    shelter_ids = [str(row.get("properties", {}).get("shelter_id")) for row in shelter_features]
    shelter_capacities = [float(row.get("properties", {}).get("capacity_persons", 0)) for row in shelter_features]
    demand_population = {
        str(row.get("properties", {}).get("demand_id")): float(row.get("properties", {}).get("pop_est", 0))
        for row in demand_features
    }
    shelter_count, demand_count = len(shelter_features), len(demand_features)
    del shelters_doc, demands_doc, shelter_features, demand_features
    gc.collect()
    total_population = sum(demand_population.values())
    scenario_ratio = float(config["scenario_defaults"]["population_demand_ratio"])
    threshold = float(config["scenario_defaults"]["max_walk_time_min"])
    max_snap = float(config.get("network", {}).get("max_snap_distance_m", 200.0))

    matrix_profile = _profile_parquet(root / paths["travel_matrix"], set(demand_population), set(shelter_ids), demand_population, scenario_ratio, max_snap, threshold)

    manifest_rows = []
    for relative, expected in manifest.get("files", {}).items():
        path = (root / relative).resolve()
        exists = path.is_file() and (path == root or root in path.parents)
        actual_bytes = path.stat().st_size if exists else None
        actual_hash = _hash(path) if exists else None
        pointer_text = None
        if exists and actual_bytes is not None and actual_bytes < 1024:
            try:
                pointer_text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pointer_text = None
        is_lfs_pointer = bool(pointer_text and pointer_text.startswith("version https://git-lfs.github.com/spec/v1"))
        manifest_rows.append({
            "path": relative, "exists": exists,
            "expected_bytes": expected.get("bytes"), "actual_bytes": actual_bytes,
            "expected_sha256": expected.get("sha256"), "actual_sha256": actual_hash,
            "matches": bool(exists and expected.get("bytes") == actual_bytes and expected.get("sha256") == actual_hash),
            "git_lfs_pointer": is_lfs_pointer,
        })
    mismatches = [row for row in manifest_rows if not row["matches"]]

    result = {
        "audit_run_id": str(uuid.uuid4()),
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "label": "Read-only Module A integration audit; A7 currently DEMO_ONLY",
        "repository_metadata": {
            "project_root_has_git_metadata": (root / ".git").exists(),
            "workspace_root_has_git_metadata": (root.parent / ".git").exists(),
            "origin_revision": "NOT_AVAILABLE: no .git metadata in either local root",
        },
        "configured_version": config["project"]["data_version"],
        "manifest_version": manifest.get("project", {}).get("data_version"),
        "a7_version": a7.get("data_version"),
        "data_status": availability["data_status"],
        "a7": {
            "path": str(a7_path.relative_to(root).as_posix()),
            "sha256": _hash(a7_path), "verdict": a7.get("verdict"),
            "real_pilot_accepted": a7.get("real_pilot_accepted"),
            "demo_ready": a7.get("demo_ready"),
            "blocker_count": len(a7.get("real_pilot_blockers", [])),
            "real_pilot_blockers": a7.get("real_pilot_blockers", []),
        },
        "input_files": [
            {"path": paths["shelters_geojson"], "bytes": (root / paths["shelters_geojson"]).stat().st_size, "sha256": _hash(root / paths["shelters_geojson"]), "records": shelter_count, "crs": shelter_crs},
            {"path": paths["demand_nodes"], "bytes": (root / paths["demand_nodes"]).stat().st_size, "sha256": _hash(root / paths["demand_nodes"]), "records": demand_count, "crs": demand_crs},
            {"path": paths["travel_matrix"], "bytes": (root / paths["travel_matrix"]).stat().st_size, "sha256": _hash(root / paths["travel_matrix"]), "records": matrix_profile["rows"], "columns": matrix_profile["columns"], "dtypes": matrix_profile["dtypes"]},
            {"path": paths["manifest"], "bytes": manifest_path.stat().st_size, "sha256": _hash(manifest_path), "declared_files": len(manifest_rows)},
        ],
        "structural_checks": {
            "shelters_unique_ids": len(set(shelter_ids)) == shelter_count,
            "demand_unique_ids": len(demand_population) == demand_count,
            "od_unique_pairs": matrix_profile["pairs_unique"],
            "expected_complete_pair_count": demand_count * shelter_count,
            "actual_od_rows": matrix_profile["rows"],
            "complete_cartesian_od_relation": matrix_profile["complete_cartesian"],
            "od_demand_keys_match_geojson": matrix_profile["demand_ids_match"],
            "od_shelter_keys_match_geojson": matrix_profile["shelter_ids_match"],
            "od_versions": matrix_profile["versions"],
            "reachable_reason_counts": matrix_profile["reasons"],
            "unreachable_time_null_count": matrix_profile["unreachable_time_null_count"],
            "reachable_rows_exceeding_a15_threshold": matrix_profile["reachable_rows_over_scenario_threshold"],
            "reachable_rows_over_a_snap_limit": matrix_profile["reachable_rows_over_snap_limit"],
        },
        "counts_from_a7": {
            "population_nodes": a7.get("population", {}).get("demand_nodes"),
            "population_sum": a7.get("population", {}).get("demand_population_sum"),
            "shelter_count": a7.get("shelters", {}).get("count"),
            "nominal_capacity_unverified_persons": a7.get("shelters", {}).get("capacity_sum_persons"),
            "travel_matrix_rows": a7.get("travel_matrix", {}).get("rows"),
            "reachable_pairs": a7.get("travel_matrix", {}).get("reachable_pairs"),
        },
        "recomputed": {
            "total_population_estimate": total_population,
            "scenario_ratio": scenario_ratio,
            "scenario_demand_persons": total_population * scenario_ratio,
            "nominal_capacity_persons_unverified": sum(shelter_capacities),
            "threshold_sensitivity": matrix_profile["thresholds"],
        },
        "manifest_integrity": {
            "matched_file_count": len(manifest_rows) - len(mismatches),
            "declared_file_count": len(manifest_rows),
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
            "note": "B validates hashes for the three consumed processed inputs and pilot.yaml. The full audit also checks A's other declared files; a small local LFS pointer can expose a non-materialized raw source without invalidating hashes of processed outputs.",
        },
    }
    output = (root / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"data_integration_audit_{result['audit_run_id']}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    result["record_path"] = target.relative_to(root).as_posix()
    return result


def main() -> int:
    result = run_audit()
    print(json.dumps({
        "audit_run_id": result["audit_run_id"], "record_path": result["record_path"],
        "data_version": result["configured_version"], "data_status": result["data_status"],
        "a7_verdict": result["a7"]["verdict"], "demand_nodes": result["input_files"][1]["records"],
        "shelters": result["input_files"][0]["records"], "od_rows": result["input_files"][2]["records"],
        "manifest_hashes_matched": result["manifest_integrity"]["matched_file_count"],
        "manifest_hashes_total": result["manifest_integrity"]["declared_file_count"],
        "manifest_mismatches": result["manifest_integrity"]["mismatches"],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
