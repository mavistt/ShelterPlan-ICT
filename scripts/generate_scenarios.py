from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import yaml


ROOT = Path(__file__).resolve().parents[1]


def make_scenarios(region_id: str, shelters: gpd.GeoDataFrame, data_version: str) -> list[dict]:
    spatial_ids = shelters.loc[shelters["geometry"].notna(), "shelter_id"].astype(str).tolist()
    first_id = spatial_ids[0] if spatial_ids else None
    capacity = 0.0
    if first_id:
        row = shelters.loc[shelters["shelter_id"].astype(str) == first_id].iloc[0]
        capacity = float(row.get("capacity_persons", 0) or 0)
    scenarios = []
    for scenario_id, label, ratio, minutes in [
        ("base", "baseline demonstration", 0.20, 15),
        ("high_demand", "higher demand assumption", 0.30, 15),
        ("strict_access", "strict walking threshold", 0.20, 10),
        ("relaxed_access", "relaxed walking threshold", 0.20, 30),
    ]:
        scenarios.append({
            "scenario_id": f"{region_id}__{scenario_id}",
            "region_id": region_id,
            "scenario_type": scenario_id,
            "population_demand_ratio": ratio,
            "max_walk_time_min": minutes,
            "closed_shelter_ids": [],
            "capacity_adjustments": {},
            "data_version": data_version,
            "analysis_mode": "demo",
            "assumption_type": "illustrative_scenario",
            "notes": label,
        })
    scenarios.append({
        "scenario_id": f"{region_id}__facility_disruption",
        "region_id": region_id,
        "scenario_type": "facility_disruption",
        "population_demand_ratio": 0.20,
        "max_walk_time_min": 15,
        "closed_shelter_ids": [first_id] if first_id else [],
        "capacity_adjustments": {},
        "data_version": data_version,
        "analysis_mode": "demo",
        "assumption_type": "illustrative_scenario",
        "notes": "Closes one spatial shelter candidate; replace the ID after human facility review.",
    })
    scenarios.append({
        "scenario_id": f"{region_id}__capacity_expansion",
        "region_id": region_id,
        "scenario_type": "capacity_expansion",
        "population_demand_ratio": 0.20,
        "max_walk_time_min": 15,
        "closed_shelter_ids": [],
        "capacity_adjustments": {first_id: {"delta_persons": round(capacity * 0.25, 2), "basis": "illustrative_25_percent_increase"}} if first_id else {},
        "data_version": data_version,
        "analysis_mode": "demo",
        "assumption_type": "illustrative_scenario",
        "capacity_adjustment_status": "parameter_only_not_optimization",
        "notes": "Illustrative capacity increase; does not assert current operational capacity.",
    })
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/regions.yaml")
    args = parser.parse_args()
    cfg = yaml.safe_load((ROOT / args.config).read_text(encoding="utf-8"))
    out = ROOT / "configs" / "planning_scenarios.yaml"
    all_scenarios = []
    catalog_dir = ROOT / "data" / "processed" / "regions"
    for region_id in cfg["regions"]:
        shelter_path = catalog_dir / region_id / "shelters.geojson"
        shelters = gpd.read_file(shelter_path)
        region_version = f"{cfg['project']['data_version']}__{region_id}"
        scenarios = make_scenarios(region_id, shelters, region_version)
        ids = set(shelters["shelter_id"].astype(str))
        for item in scenarios:
            unknown = (set(item["closed_shelter_ids"]) | set(item["capacity_adjustments"])) - ids
            if unknown:
                raise ValueError(f"{region_id}: scenario references unknown shelter IDs: {sorted(unknown)}")
        (catalog_dir / region_id / "scenario_catalog.json").write_text(
            json.dumps({"region_id": region_id, "data_version": region_version, "scenarios": scenarios}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        all_scenarios.extend(scenarios)
    all_scenarios.append({
        "scenario_id": "cross_region_comparison__base",
        "region_ids": list(cfg["regions"]),
        "scenario_type": "cross_region_comparison",
        "population_demand_ratio": 0.20,
        "max_walk_time_min": 15,
        "closed_shelter_ids": [],
        "capacity_adjustments": {},
        "data_version": None,
        "region_data_versions": {region_id: f"{cfg['project']['data_version']}__{region_id}" for region_id in cfg["regions"]},
        "analysis_mode": "demo",
        "assumption_type": "illustrative_scenario",
        "notes": "Compare region-level outputs; do not combine OD matrices across regions.",
    })
    out.write_text(yaml.safe_dump({"data_version": cfg["project"]["data_version"], "scenarios": all_scenarios}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(json.dumps({"output": str(out), "scenario_count": len(all_scenarios)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
