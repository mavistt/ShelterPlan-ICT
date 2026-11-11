from datetime import datetime, timezone
import json
from importlib.metadata import PackageNotFoundError, version
from common import load_config, resolve, sha256_file, write_json

DATA_FILES = [
    "data/raw/zhongshan_shelters.xls",
    "data/raw/portal_shelters_batch_1.xls",
    "data/raw/portal_shelters_batch_2.xls",
    "data/processed/shelter_batch_reconciliation.json",
    "data/raw/chn_pop_2025_CN_100m_R2025A_v1.tif",
    "data/interim/east_district_shelters_review.xlsx",
    "data/interim/east_district_shelters_review.csv",
    "data/interim/shelters_review_required.xlsx",
    "data/interim/shelters_review_required.csv",
    "data/interim/shelters_wgs84_review_template.csv",
    "configs/pilot.yaml",
    "configs/pilot_analysis_area.geojson",
    "data/processed/shelters_clean.csv",
    "data/processed/shelters_pilot_raw.csv",
    "data/interim/shelters_geocoding_candidates.csv",
    "data/interim/shelters_geocoding_exclusions.csv",
    "data/interim/amap_geocode_cache.json",
    "data/processed/shelters_geocoded.geojson",
    "data/processed/pop_pilot.tif",
    "data/processed/demand_nodes.geojson",
    "data/processed/road_walk.graphml",
    "data/processed/osm_graph_metadata.json",
    "data/processed/map_layers/road_edges.geojson",
    "data/processed/map_layers/network_buffer.geojson",
    "data/processed/map_layers/demand_nodes.geojson",
    "data/processed/map_layers/shelters.geojson",
    "data/processed/map_layers/route_example.geojson",
    "data/processed/demand_snap_audit.csv",
    "data/processed/shelter_snap_audit.csv",
    "data/processed/travel_matrix.parquet",
    "data/processed/travel_matrix_reason_counts.csv",
    "data/processed/snap_time_sensitivity.csv",
    "data/processed/a7_quality_summary.json",
    "README.md",
    "docs/shelter_inspection.txt",
    "docs/data_quality_shelters.md",
    "docs/data_quality_population.md",
    "docs/data_quality_roads.md",
    "docs/data_quality_pilot.md",
    "docs/data_quality_a7.md",
    "docs/shelter_batch_reconciliation.md",
    "docs/handoffs/A_to_B.md",
    "docs/data_dictionary.md",
    "docs/module_a.md",
    "docs/snap_time_sensitivity.md",
    "requirements-lock.txt",
]
CODE_FILES = [f"scripts/{name}" for name in [
    "00_inspect_shelters.py", "01_clean_shelters.py", "01_prepare_pilot_demo.py",
    "12_reconcile_portal_batches.py",
    "02_geocode_amap.py", "03_prepare_verified_shelters.py", "04_clip_population.py",
    "05_build_walk_graph.py", "06_build_travel_matrix.py", "07_validate_outputs.py",
    "08_build_manifest.py", "09_check_east_review.py", "10_a7_quality_audit.py",
    "common.py", "run_stage_a_demo.sh", "11_snap_time_sensitivity.py"
]]
PACKAGES = ["pandas", "numpy", "geopandas", "rasterio", "shapely", "pyproj", "osmnx", "networkx", "pyarrow", "scipy", "requests", "PyYAML"]


def describe(paths):
    result = {}
    for item in paths:
        path = resolve(item)
        if path.is_file():
            result[item] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    return result


def main():
    cfg = load_config()
    osm_metadata_path = resolve("data/processed/osm_graph_metadata.json")
    osm_metadata = json.loads(osm_metadata_path.read_text(encoding="utf-8")) if osm_metadata_path.is_file() else None
    software = {}
    for package in PACKAGES:
        try:
            software[package] = version(package)
        except PackageNotFoundError:
            software[package] = None

    manifest = {
        "project": cfg["project"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "reproducibility": {
            "command": "bash scripts/run_stage_a_demo.sh",
            "scope": "Rebuilds derived GIS outputs from frozen shelter, geocoding, population, and polygon inputs; online OSM retrieval may change over time.",
            "secrets_excluded": [".env", "AMAP_KEY"],
            "software_versions": software,
            "code": describe(CODE_FILES),
        },
        "coordinate_reference_system": "EPSG:4326 for vector interchange; local projected CRS for network distances",
        "data_quality": "Demonstration only unless all shelter positions/capacities and the analysis boundary have verified evidence. Current shelter points use an approximate non-official GCJ-02 inverse.",
        "sources": {
            "shelter_register": {
                "url": "https://zsdata.zs.gov.cn/web/dataServerView?id=ff80808171f8c2d2017211d225c2039a",
                "licence_status": "Portal marks dataset fully open; retain source attribution and confirm redistribution terms",
                "download_method": "Manual Xls download from portal",
                "source_period": "2024年度",
                "portal_catalog_record_count": 906,
                "attachment_batches": ["ff8080819daf4f90019e678d78683d50", "ff80808196a71e2901975c8dd57b21e4"],
                "count_interpretation": "The portal exposes two 2024 attachment batches whose first 20 preview rows match. The local workbook has 453 rows; the catalog count of 906 may include batch versions, but full row-level equivalence and unique current-register size are not verified.",
            },
            "population": {
                "url": "https://stac.worldpop.org/collections/CHN/items/chn_pop_2025_CN_100m_R2025A_v1",
                "doi": "10.5258/SOTON/WP00839",
                "licence": "CC BY 4.0",
                "meaning": "Modelled total people per grid cell, not evacuees",
            },
            "walking_network": {
                "url": "https://www.openstreetmap.org/copyright",
                "source": "OpenStreetMap contributors, retrieved through OSMnx/Overpass",
                "licence": "ODbL 1.0",
                "attribution": "© OpenStreetMap contributors",
                "version_note": "Online query result is captured by the road graph SHA256; no fixed regional PBF snapshot was used.",
                "retrieval_metadata": osm_metadata,
            },
            "geocoding": {
                "provider": "Amap Web Service",
                "coordinate_system_returned": "GCJ-02",
                "note": "The current demo uses a non-official approximate inverse to estimate WGS84 and is not suitable as verified/surveyed coordinates.",
            },
        },
        "assumptions": {
            "analysis_area_status": "Generated pilot analysis rectangle; not an official administrative boundary",
            "network_buffer_m": cfg["network"]["network_buffer_m"],
            "walking_speed_m_per_s": cfg["network"]["walking_speed_m_per_s"],
            "max_snap_distance_m": cfg["network"]["max_snap_distance_m"],
            "population_demand_ratio": "Scenario parameter; not baked into population raster output",
            "walk_time_threshold_min": cfg["scenario_defaults"]["max_walk_time_min"],
        },
        "files": describe(DATA_FILES),
    }
    write_json(resolve(cfg["paths"]["manifest"]), manifest)
    print(f"Manifest saved: {resolve(cfg['paths']['manifest'])}")


if __name__ == "__main__":
    main()
