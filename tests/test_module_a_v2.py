from pathlib import Path
import json

import geopandas as gpd
import pandas as pd
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"east_district": 31, "shiqi": 25, "xiaolan": 31}


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load((ROOT / "configs/regions.yaml").read_text(encoding="utf-8"))


def test_stage1a_target_records_are_complete():
    disposition = pd.read_csv(ROOT / "data/interim/regions/source_disposition.csv", dtype=str)
    target = disposition[disposition["region_id"].isin(EXPECTED)]
    assert len(target) == 87
    assert target["shelter_id"].is_unique
    assert target.groupby("region_id").size().to_dict() == EXPECTED


@pytest.mark.parametrize("region_id,expected", EXPECTED.items())
def test_region_source_and_spatial_counts(region_id, expected):
    base = ROOT / "data/processed/regions" / region_id
    source = pd.read_csv(base / "shelters_all.csv", dtype=str)
    spatial = gpd.read_file(base / "shelters.geojson")
    assert len(source) == expected
    assert len(spatial) == expected
    assert source["shelter_id"].is_unique
    assert spatial["shelter_id"].is_unique
    assert set(spatial["region_id"]) == {region_id}
    assert str(spatial.crs).upper().endswith("4326")


def test_canonical_capacity_keeps_provenance():
    shiqi = gpd.read_file(ROOT / "data/processed/regions/shiqi/shelters.geojson")
    assert shiqi["capacity_persons"].notna().all()
    assert "capacity_persons_original" in shiqi.columns
    assert "capacity_field_status" in shiqi.columns
    assert (shiqi["capacity_field_status"] == "demo_imputed").sum() == 1


def test_east_od_is_full_cartesian_and_isolated():
    base = ROOT / "data/processed/regions/east_district"
    demand = gpd.read_file(base / "demand_nodes.geojson")
    shelters = gpd.read_file(base / "shelters.geojson")
    matrix = pd.read_parquet(base / "travel_matrix.parquet")
    assert len(matrix) == len(demand) * len(shelters)
    assert set(matrix["region_id"]) == {"east_district"}
    assert set(matrix["demand_id"]) == set(demand["demand_id"])
    assert set(matrix["shelter_id"]) == set(shelters["shelter_id"])


def test_scenarios_reference_only_existing_shelters():
    catalog = yaml.safe_load((ROOT / "configs/planning_scenarios.yaml").read_text(encoding="utf-8"))
    by_region = {region: set(gpd.read_file(ROOT / "data/processed/regions" / region / "shelters.geojson")["shelter_id"]) for region in EXPECTED}
    for item in catalog["scenarios"]:
        region_ids = item.get("region_ids", [item.get("region_id")])
        for region_id in region_ids:
            if region_id not in by_region:
                continue
            assert set(item.get("closed_shelter_ids", [])) <= by_region[region_id]
            assert set(item.get("capacity_adjustments", {})) <= by_region[region_id]


def test_old_v01_east_graph_remains_present():
    assert (ROOT / "data/processed/road_walk.graphml").exists()
    assert (ROOT / "data/processed/travel_matrix.parquet").exists()


def test_required_release_artifacts_exist():
    assert (ROOT / "data/processed/module_a_v2_release_validation.json").exists()
    validation = json.loads((ROOT / "data/processed/module_a_v2_release_validation.json").read_text(encoding="utf-8"))
    assert validation["status"] == "PARTIAL"
    for path in [
        "docs/handoffs/A_to_B_v2.md",
        "docs/handoffs/A_to_C.md",
        "docs/handoffs/A_to_D.md",
        "docs/interfaces/module_a_schema.md",
        "docs/module_a_v2_final_report.md",
    ]:
        assert (ROOT / path).exists()


def test_region_versions_manifest_and_adapter_are_aligned():
    base_version = "shelterplan_module_a_v2.0-demo"
    for region_id in EXPECTED:
        region_version = f"{base_version}__{region_id}"
        base = ROOT / "data/processed/regions" / region_id
        quality = json.loads((base / "quality_summary.json").read_text(encoding="utf-8"))
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        adapter = yaml.safe_load((ROOT / "configs/module_a_v2_b" / f"{region_id}.yaml").read_text(encoding="utf-8"))
        catalog = json.loads((base / "scenario_catalog.json").read_text(encoding="utf-8"))
        assert quality["data_version"] == region_version
        assert manifest["data_version"] == region_version
        assert adapter["project"]["data_version"] == region_version
        assert catalog["data_version"] == region_version
        assert len(catalog["scenarios"]) == 6
        assert all(item["data_version"] == region_version for item in catalog["scenarios"])
        adapter_rel = manifest["b_adapter"]["path"]
        assert adapter_rel == f"configs/module_a_v2_b/{region_id}.yaml"
        assert manifest["files"][adapter_rel]["sha256"] == manifest["b_adapter"]["sha256"]


def test_manifest_excludes_itself_and_hashes_artifacts():
    for region_id in EXPECTED:
        manifest = json.loads((ROOT / "data/processed/regions" / region_id / "manifest.json").read_text(encoding="utf-8"))
        assert f"data/processed/regions/{region_id}/manifest.json" not in manifest["files"]
        assert manifest["files"]
        assert all(record.get("sha256") and record.get("bytes") is not None for record in manifest["files"].values())
