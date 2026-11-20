from __future__ import annotations

import json
import hashlib
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"east_district": 31, "shiqi": 25, "xiaolan": 31}
REQUIRED_DOCS = [
    "docs/handoffs/A_to_B_v2.md",
    "docs/handoffs/A_to_C.md",
    "docs/handoffs/A_to_D.md",
    "docs/interfaces/module_a_schema.md",
    "docs/development_progress.md",
    "docs/module_a_v2_final_report.md",
]


def check(cond: bool, message: str, failures: list[str], warnings: list[str] | None = None) -> None:
    if not cond:
        (warnings if warnings is not None else failures).append(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    cfg = yaml.safe_load((ROOT / "configs" / "regions.yaml").read_text(encoding="utf-8"))
    region_ids = list(cfg["regions"])
    check(set(region_ids) == set(EXPECTED), f"regions.yaml region set is {region_ids}", failures)
    disposition_path = ROOT / "data/interim/regions/source_disposition.csv"
    disposition = pd.read_csv(disposition_path, dtype=str)
    target = disposition[disposition["region_id"].isin(EXPECTED)]
    check(len(target) == sum(EXPECTED.values()), f"Stage1A target disposition rows={len(target)}", failures)
    check(target["shelter_id"].is_unique, "Stage1A target shelter_id is not unique", failures)
    check(target[["region_id", "shelter_id", "source_row_excel"]].drop_duplicates().shape[0] == len(target), "Stage1A target source identity is not unique", failures)
    region_reports = {}
    any_missing_matrix = False
    for region_id, expected_count in EXPECTED.items():
        base = ROOT / "data/processed/regions" / region_id
        all_csv = base / "shelters_all.csv"
        shelters_path = base / "shelters.geojson"
        demand_path = base / "demand_nodes.geojson"
        check(all_csv.exists(), f"{region_id}: missing shelters_all.csv", failures)
        check(shelters_path.exists(), f"{region_id}: missing shelters.geojson", failures)
        if not all_csv.exists() or not shelters_path.exists():
            continue
        all_rows = pd.read_csv(all_csv, dtype=str)
        shelters = gpd.read_file(shelters_path)
        check(len(all_rows) == expected_count, f"{region_id}: source rows {len(all_rows)} != {expected_count}", failures)
        check(all_rows["shelter_id"].is_unique, f"{region_id}: shelter_id duplicate in shelters_all.csv", failures)
        check(len(shelters) == expected_count, f"{region_id}: spatial shelter rows {len(shelters)} != {expected_count}", failures)
        check(shelters["shelter_id"].is_unique, f"{region_id}: shelter_id duplicate in shelters.geojson", failures)
        check(set(shelters["region_id"].astype(str)) == {region_id}, f"{region_id}: shelter region leakage", failures)
        check(str(shelters.crs).upper().endswith("4326"), f"{region_id}: shelter CRS is {shelters.crs}", failures)
        check(pd.to_numeric(shelters["capacity_persons"], errors="coerce").notna().all(), f"{region_id}: canonical capacity contains null/non-numeric values", failures)
        check(pd.to_numeric(shelters["capacity_persons"], errors="coerce").ge(0).all(), f"{region_id}: canonical capacity contains negative values", failures)
        check("capacity_persons_original" in shelters.columns and "capacity_field_status" in shelters.columns, f"{region_id}: source/demo capacity provenance missing", failures)
        check(demand_path.exists(), f"{region_id}: missing demand_nodes.geojson", failures)
        demand = gpd.read_file(demand_path) if demand_path.exists() else gpd.GeoDataFrame()
        if len(demand):
            check(set(demand["region_id"].astype(str)) == {region_id}, f"{region_id}: demand region leakage", failures)
            check(demand["demand_id"].is_unique, f"{region_id}: demand_id duplicate", failures)
        demand_parquet = base / "demand_nodes.parquet"
        check(demand_parquet.exists(), f"{region_id}: demand_nodes.parquet missing", failures)
        if demand_parquet.exists() and len(demand):
            demand_table = pd.read_parquet(demand_parquet)
            check(len(demand_table) == len(demand), f"{region_id}: demand_nodes.parquet row mismatch", failures)
            check(set(demand_table["demand_id"]) == set(demand["demand_id"]), f"{region_id}: demand_nodes.parquet ID mismatch", failures)
        check((base / "boundary.geojson").exists() and (base / "boundary_metadata.json").exists(), f"{region_id}: boundary products missing", failures)
        pop_stats = base / "map_layers" / "population_stats.json"
        check(pop_stats.exists(), f"{region_id}: population stats missing", failures)
        if pop_stats.exists():
            stats = json.loads(pop_stats.read_text(encoding="utf-8"))
            if len(demand):
                check(abs(float(stats.get("population_sum", -1)) - float(demand["pop_est"].astype(float).sum())) <= 1e-3, f"{region_id}: population totals do not reconcile", failures)
        quality_path = base / "quality_summary.json"
        manifest_path = base / "manifest.json"
        expected_version = f"{cfg['project']['data_version']}__{region_id}"
        check(quality_path.exists(), f"{region_id}: quality summary missing", failures)
        check(manifest_path.exists(), f"{region_id}: manifest missing", failures)
        if quality_path.exists():
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            check(quality.get("region_id") == region_id, f"{region_id}: quality region_id mismatch", failures)
            check(quality.get("data_version") == expected_version, f"{region_id}: quality data_version mismatch", failures)
            check(quality.get("verdict") in {"DEMO_ONLY", "VERIFIED"}, f"{region_id}: invalid quality verdict", failures)
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            check(manifest.get("region_id") == region_id, f"{region_id}: manifest region_id mismatch", failures)
            check(manifest.get("data_version") == expected_version, f"{region_id}: manifest data_version mismatch", failures)
            check(manifest.get("release_status") in {"PASS_DEMO", "PARTIAL", "FAIL"}, f"{region_id}: invalid manifest release_status", failures)
            for rel, record in manifest.get("files", {}).items():
                artifact = ROOT / rel
                check(artifact.is_file(), f"{region_id}: manifest artifact missing: {rel}", failures)
                if artifact.is_file():
                    check(record.get("bytes") == artifact.stat().st_size, f"{region_id}: manifest byte mismatch: {rel}", failures)
                    check(record.get("sha256") == sha256(artifact), f"{region_id}: manifest SHA mismatch: {rel}", failures)
            adapter_record = manifest.get("b_adapter") or {}
            adapter_path = ROOT / str(adapter_record.get("path", ""))
            check(adapter_path.is_file(), f"{region_id}: B adapter referenced by manifest is missing", failures)
            if adapter_path.is_file():
                check(adapter_record.get("sha256") == sha256(adapter_path), f"{region_id}: B adapter SHA mismatch", failures)
                adapter = yaml.safe_load(adapter_path.read_text(encoding="utf-8"))
                check(adapter.get("project", {}).get("data_version") == expected_version, f"{region_id}: B adapter data_version mismatch", failures)
        matrix_path = base / "travel_matrix.parquet"
        if not matrix_path.exists():
            any_missing_matrix = True
            warnings.append(f"{region_id}: travel_matrix.parquet missing; network/OD remains blocked or not run")
        else:
            matrix = pd.read_parquet(matrix_path, columns=["region_id", "demand_id", "shelter_id", "time_min", "distance_m", "reachable", "reason", "data_version"])
            expected_rows = len(demand) * len(shelters)
            check(len(matrix) == expected_rows, f"{region_id}: OD rows {len(matrix)} != Cartesian {expected_rows}", failures)
            check(set(matrix["region_id"].astype(str)) == {region_id}, f"{region_id}: OD region leakage", failures)
            check(set(matrix["demand_id"]) == set(demand["demand_id"]), f"{region_id}: OD demand foreign-key mismatch", failures)
            check(set(matrix["shelter_id"]) == set(shelters["shelter_id"]), f"{region_id}: OD shelter foreign-key mismatch", failures)
            accepted_versions = {cfg["project"]["data_version"], f"{cfg['project']['data_version']}__{region_id}"}
            check(set(matrix["data_version"].astype(str)) <= accepted_versions, f"{region_id}: OD data_version mismatch", failures)
        check((base / "layers_manifest.json").exists(), f"{region_id}: layer manifest missing", failures)
        scenario_path = base / "scenario_catalog.json"
        check(scenario_path.exists(), f"{region_id}: scenario catalog missing", failures)
        if scenario_path.exists():
            scenario_catalog = json.loads(scenario_path.read_text(encoding="utf-8"))
            scenarios = scenario_catalog.get("scenarios", [])
            check(scenario_catalog.get("data_version") == expected_version, f"{region_id}: scenario catalog data_version mismatch", failures)
            check(len(scenarios) == 6, f"{region_id}: expected six regional scenarios, found {len(scenarios)}", failures)
            check(all(item.get("data_version") == expected_version for item in scenarios), f"{region_id}: scenario item data_version mismatch", failures)
        check((ROOT / "configs/module_a_v2_b" / f"{region_id}.yaml").exists(), f"{region_id}: B adapter config missing", failures)
        region_reports[region_id] = {"source_rows": len(all_rows), "spatial_rows": len(shelters), "demand_nodes": len(demand), "has_od": matrix_path.exists()}
    for doc in REQUIRED_DOCS:
        check((ROOT / doc).exists(), f"missing required deliverable {doc}", failures)
    check((ROOT / "data/processed/road_walk.graphml").exists(), "legacy v0.1 East graph was overwritten or is missing", failures)
    if not all((ROOT / f"data/raw/portal_shelters_batch_{n}.xls").exists() for n in (1, 2)):
        warnings.append("portal shelter batches are absent; source completeness is not fully reconciled")
    warnings.append("official target administrative boundaries are absent; all regional results remain DEMO_ONLY")
    planning_scenarios_path = ROOT / "configs/planning_scenarios.yaml"
    if planning_scenarios_path.exists():
        planning_scenarios = yaml.safe_load(planning_scenarios_path.read_text(encoding="utf-8"))
        regional_items = [item for item in planning_scenarios.get("scenarios", []) if item.get("region_id") in EXPECTED]
        check(len(regional_items) == 18, f"planning_scenarios.yaml regional scenario count={len(regional_items)}", failures)
    status = "FAIL" if failures else ("PARTIAL" if any_missing_matrix else "PASS_DEMO")
    report = {
        "status": status,
        "data_version": cfg["project"]["data_version"],
        "generated_at_utc": pd.Timestamp.utcnow().isoformat(),
        "failures": failures,
        "warnings": warnings,
        "regions": region_reports,
        "external_dependencies": {
            "portal_batches_present": all((ROOT / f"data/raw/portal_shelters_batch_{n}.xls").exists() for n in (1, 2)),
            "official_target_boundaries_present": False,
            "static_pbf_present": (ROOT / str(cfg["project"].get("network_source", {}).get("pbf_path", ""))).is_file(),
            "public_overpass_available_for_this_run": False,
        },
    }
    out = ROOT / "data/processed/module_a_v2_release_validation.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if status == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
