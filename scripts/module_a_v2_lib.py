"""Config-driven Module A v2.0 demo pipeline.

The legacy A1-A7 files remain untouched.  This module provides the new
region-scoped implementation and explicitly preserves source/demo provenance.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import rasterio
import requests
import yaml
from dotenv import load_dotenv
from rasterio.features import geometry_mask
from rasterio.mask import mask
from rasterio.transform import xy
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs/regions.yaml"
REGION_OUTPUT_ROOT = ROOT / "data/processed/regions"
REGION_INTERIM_ROOT = ROOT / "data/interim/regions"
AMAP_CACHE_PATH = ROOT / "data/interim/amap_geocode_cache.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def norm(value: Any) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).split()).strip()


def load_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def paths_for(region_id: str) -> dict[str, Path]:
    base = REGION_OUTPUT_ROOT / region_id
    interim = REGION_INTERIM_ROOT / region_id
    layers = base / "map_layers"
    return {
        "base": base,
        "interim": interim,
        "layers": layers,
        "shelters_all": base / "shelters_all.csv",
        "shelters": base / "shelters.geojson",
        "geocode_audit": interim / "shelter_geocode_audit.csv",
        "boundary": base / "boundary.geojson",
        "boundary_metadata": base / "boundary_metadata.json",
        "analysis_area": base / "analysis_area.geojson",
        "population": base / "population.tif",
        "population_heatmap": layers / "population_density.tif",
        "demand": base / "demand_nodes.geojson",
        "graph": base / "road_walk.graphml",
        "graph_metadata": base / "road_graph_metadata.json",
        "road_edges": layers / "road_edges.geojson",
        "network_buffer": layers / "network_buffer.geojson",
        "demand_snap": base / "demand_snap_audit.csv",
        "shelter_snap": base / "shelter_snap_audit.csv",
        "matrix": base / "travel_matrix.parquet",
        "population_stats": layers / "population_stats.json",
        "capacity_layer": layers / "shelter_capacity.geojson",
        "layers_manifest": base / "layers_manifest.json",
        "quality": base / "quality_summary.json",
        "manifest": base / "manifest.json",
        "scenario_catalog": base / "scenario_catalog.json",
        "b_config": ROOT / "configs/module_a_v2_b" / f"{region_id}.yaml",
        "run_status": base / "run_status.json",
    }


def ensure_dirs(region_id: str) -> dict[str, Path]:
    paths = paths_for(region_id)
    for path in paths.values():
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
    return paths


def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    """Approximate inverse used only for demo coordinates, never as verified GPS."""
    x, y = lon - 105.0, lat - 35.0
    a, ee = 6378245.0, 0.00669342162296594323
    pi = math.pi
    dlat = -100.0 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    dlat += (20 * math.sin(6 * x * pi) + 20 * math.sin(2 * x * pi)) * 2 / 3
    dlat += (20 * math.sin(y * pi) + 40 * math.sin(y * pi / 3)) * 2 / 3
    dlat += (160 * math.sin(y * pi / 12) + 320 * math.sin(y * pi / 30)) * 2 / 3
    dlon = 300.0 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    dlon += (20 * math.sin(6 * x * pi) + 20 * math.sin(2 * x * pi)) * 2 / 3
    dlon += (20 * math.sin(x * pi) + 40 * math.sin(x * pi / 3)) * 2 / 3
    dlon += (150 * math.sin(x * pi / 12) + 300 * math.sin(x * pi / 30)) * 2 / 3
    rad = lat * pi / 180.0
    magic = 1 - ee * math.sin(rad) ** 2
    root = math.sqrt(magic)
    dlat = dlat * 180 / ((a * (1 - ee)) / (magic * root) * pi)
    dlon = dlon * 180 / (a / root * math.cos(rad) * pi)
    return lon - dlon, lat - dlat


def _cache_load() -> dict[str, Any]:
    if AMAP_CACHE_PATH.exists():
        try:
            return json.loads(AMAP_CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _cache_save(cache: dict[str, Any]) -> None:
    AMAP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AMAP_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _amap_geocode(address: str, city: str, key: str, cache: dict[str, Any], rate: float) -> dict[str, Any]:
    cache_key = f"{city}|{address}"
    if cache_key not in cache:
        response = requests.get(
            "https://restapi.amap.com/v3/geocode/geo",
            params={"key": key, "address": address, "city": city, "output": "JSON"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "1":
            cache[cache_key] = payload
            _cache_save(cache)
        else:
            return {"provider": "amap", "status": "api_no_match", "info": payload.get("info")}
        time.sleep(rate)
    payload = cache[cache_key]
    geocodes = payload.get("geocodes") or []
    if not geocodes:
        return {"provider": "amap", "status": "api_no_match", "count": payload.get("count", 0)}
    best = geocodes[0]
    location = str(best.get("location", ""))
    if "," not in location:
        return {"provider": "amap", "status": "invalid_location", "count": payload.get("count", 0)}
    lon, lat = [float(part) for part in location.split(",", 1)]
    wgs_lon, wgs_lat = gcj02_to_wgs84(lon, lat)
    return {
        "provider": "amap",
        "status": "candidate",
        "lon_gcj02": lon,
        "lat_gcj02": lat,
        "lon_wgs84": wgs_lon,
        "lat_wgs84": wgs_lat,
        "geocode_level": best.get("level"),
        "formatted_address": best.get("formatted_address"),
        "adcode": best.get("adcode"),
        "geocode_count": int(payload.get("count", 0) or 0),
        "coordinate_status": "approximate",
        "coordinate_method": "approximate_gcj02_inverse_not_official",
    }


def _nominatim_geocode(address: str, city: str, rate: float) -> dict[str, Any]:
    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": f"{address}, {city}, 中国", "format": "jsonv2", "limit": 1},
        headers={"User-Agent": "ShelterPlan-ModuleA-Demo/2.0"},
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json()
    time.sleep(max(rate, 1.0))
    if not rows:
        return {"provider": "nominatim", "status": "no_match"}
    row = rows[0]
    return {
        "provider": "nominatim",
        "status": "candidate",
        "lon_wgs84": float(row["lon"]),
        "lat_wgs84": float(row["lat"]),
        "formatted_address": row.get("display_name"),
        "geocode_level": row.get("type"),
        "coordinate_status": "approximate",
        "coordinate_method": "public_geocoder_candidate_not_official",
    }


def _geocode_row(address: str, cfg: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    key = os.getenv("AMAP_KEY", "").strip()
    try:
        if key and key != "replace_with_your_web_service_key":
            return _amap_geocode(
                address, cfg["project"]["city"], key, cache,
                float(cfg["project"].get("geocoding_rate_limit_seconds", 0.35)),
            )
        return _nominatim_geocode(
            address, cfg["project"]["city"],
            float(cfg["project"].get("geocoding_rate_limit_seconds", 1.0)),
        )
    except (requests.RequestException, ValueError, KeyError) as exc:
        return {"provider": "amap_or_nominatim", "status": "unresolved", "error_type": type(exc).__name__}


def prepare_shelters(region_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_dirs(region_id)
    town = cfg["regions"][region_id]["town"]
    workbook = REGION_INTERIM_ROOT / region_id / "shelter_review.xlsx"
    if not workbook.is_file():
        raise FileNotFoundError(workbook)
    frame = pd.read_excel(workbook, sheet_name="shelter_review", dtype=object)
    frame["region_id"] = region_id
    frame["town"] = frame["所在镇区"].map(norm)
    if not frame["town"].eq(town).all():
        raise ValueError(f"Review workbook contains rows outside {town}")
    if frame["shelter_id"].isna().any() or frame["shelter_id"].duplicated().any():
        raise ValueError(f"Duplicate or missing shelter_id in {region_id}")

    clean = pd.read_csv(ROOT / cfg["project"]["source_clean"], dtype=str, keep_default_na=False)
    clean_capacity = pd.to_numeric(clean["capacity_persons"], errors="coerce")
    global_median = float(clean_capacity[clean_capacity.gt(0)].median())
    original_capacity = pd.to_numeric(frame["capacity_persons"], errors="coerce")
    region_valid = original_capacity[original_capacity.gt(0)]
    region_median = float(region_valid.median()) if not region_valid.empty else global_median
    effective = original_capacity.copy()
    missing = effective.isna() | effective.le(0)
    effective.loc[missing] = region_median if math.isfinite(region_median) else global_median

    frame["original_capacity_persons"] = original_capacity
    frame["capacity_persons_effective"] = effective.astype(float)
    frame["capacity_source_status"] = np.where(missing, "demo_imputed", "original")
    frame["capacity_imputation_method"] = np.where(missing, "regional_median", "none")
    frame["capacity_imputation_source"] = np.where(
        missing,
        f"{region_id}:valid_capacity_median={region_median:g}",
        "",
    )
    frame["verified_status"] = "demo_unverified"
    frame["active"] = True
    frame["current_open_status"] = "assumed_open"
    frame["open_status_source"] = "demo_assumption"
    frame["data_status"] = "DEMO_ONLY"
    frame["shelter_name_demo"] = frame["shelter_name_candidate"].map(norm)
    frame["address_demo"] = frame["address_candidate"].map(norm)
    frame["shelter_name_status"] = np.where(frame["shelter_name_demo"].ne(""), "demo_assumption", "unresolved")
    frame["address_status"] = np.where(frame["address_demo"].ne(""), "demo_assumption", "unresolved")
    frame["lon_gcj02"] = np.nan
    frame["lat_gcj02"] = np.nan
    frame["lon_wgs84"] = np.nan
    frame["lat_wgs84"] = np.nan
    frame["geocode_provider"] = ""
    frame["geocode_status"] = "unresolved"
    frame["coordinate_status"] = "unresolved"
    frame["coordinate_method"] = ""
    frame["formatted_address"] = ""
    frame["geocode_level"] = ""
    frame["geocode_count"] = 0

    cache = _cache_load()
    audits = []
    for index, row in frame.iterrows():
        address = norm(row["address_demo"])
        result = _geocode_row(address, cfg, cache) if address else {"status": "missing_address", "provider": "none"}
        for key, value in result.items():
            if key in frame.columns:
                frame.loc[index, key] = value
        audits.append({
            "region_id": region_id,
            "shelter_id": row["shelter_id"],
            "source_row_excel": int(row["source_row_excel"]),
            "address_used": address,
            **result,
        })

    frame.to_csv(paths["shelters_all"], index=False, encoding="utf-8-sig")
    pd.DataFrame(audits).to_csv(paths["geocode_audit"], index=False, encoding="utf-8-sig")
    spatial = frame[pd.to_numeric(frame["lon_wgs84"], errors="coerce").between(-180, 180) & pd.to_numeric(frame["lat_wgs84"], errors="coerce").between(-90, 90)].copy()
    if not spatial.empty:
        # Keep shelters_all.csv as the immutable source/demo separation.  The
        # GeoJSON is the B-facing canonical layer, so its capacity_persons is
        # explicitly the effective demo capacity while the original/null value
        # remains available in original_capacity_persons.
        spatial["capacity_persons_original"] = spatial["capacity_persons"]
        spatial["capacity_persons"] = spatial["capacity_persons_effective"]
        spatial["capacity_field_status"] = spatial["capacity_source_status"]
        geometry = [Point(float(lon), float(lat)) for lon, lat in zip(spatial["lon_wgs84"], spatial["lat_wgs84"])]
        gdf = gpd.GeoDataFrame(spatial, geometry=geometry, crs="EPSG:4326")
        gdf.to_file(paths["shelters"], driver="GeoJSON", index=False)
    else:
        gpd.GeoDataFrame(spatial, geometry=[], crs="EPSG:4326").to_file(paths["shelters"], driver="GeoJSON", index=False)
    return {
        "source_rows": int(len(frame)),
        "spatial_rows": int(len(spatial)),
        "unresolved_rows": int(len(frame) - len(spatial)),
        "capacity_imputed_rows": int(missing.sum()),
        "capacity_region_median": region_median,
        "shelters": frame,
    }


def _write_feature_collection(path: Path, geometry, properties: dict[str, Any]) -> None:
    gdf = gpd.GeoDataFrame([properties], geometry=[geometry], crs="EPSG:4326")
    gdf.to_file(path, driver="GeoJSON", index=False)


def build_boundary(region_id: str, cfg: dict[str, Any], shelters: pd.DataFrame) -> dict[str, Any]:
    paths = paths_for(region_id)
    geometry = None
    boundary_status = ""
    source_url = ""
    source_note = ""
    query = cfg["regions"][region_id].get("boundary_query", "")
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "geojson", "polygon_geojson": 1, "limit": 1},
            headers={"User-Agent": "ShelterPlan-ModuleA-Demo/2.0"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        features = payload.get("features", [])
        if features and features[0].get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}:
            geometry = shape(features[0]["geometry"])
            boundary_status = "public_osm_boundary"
            source_url = "https://nominatim.openstreetmap.org/"
            source_note = "Public OpenStreetMap/Nominatim geometry; not an official government boundary."
    except (requests.RequestException, ValueError, KeyError, TypeError):
        geometry = None

    if geometry is None or geometry.is_empty or not geometry.is_valid:
        point_rows = shelters[pd.to_numeric(shelters["lon_wgs84"], errors="coerce").notna() & pd.to_numeric(shelters["lat_wgs84"], errors="coerce").notna()]
        points = [Point(float(lon), float(lat)) for lon, lat in zip(point_rows["lon_wgs84"], point_rows["lat_wgs84"])]
        if not points:
            raise RuntimeError(f"No spatial shelter points available to construct a demo boundary for {region_id}")
        point_gdf = gpd.GeoDataFrame(geometry=points, crs="EPSG:4326")
        metric_crs = point_gdf.estimate_utm_crs()
        projected = point_gdf.to_crs(metric_crs)
        geometry = projected.unary_union.convex_hull.buffer(float(cfg["project"]["network_buffer_m"]))
        geometry = gpd.GeoSeries([geometry], crs=metric_crs).to_crs("EPSG:4326").iloc[0]
        boundary_status = "demo_analysis_boundary"
        source_note = "Convex hull of geocoded candidate shelters plus configured buffer; not an administrative boundary."
        source_url = ""

    boundary = gpd.GeoDataFrame(
        [{"region_id": region_id, "boundary_status": boundary_status, "source_url": source_url, "source_note": source_note}],
        geometry=[geometry], crs="EPSG:4326",
    )
    boundary.to_file(paths["boundary"], driver="GeoJSON", index=False)
    metric_crs = boundary.estimate_utm_crs()
    area_geometry = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(metric_crs).geometry.iloc[0].buffer(float(cfg["project"]["network_buffer_m"]))
    area_geometry = gpd.GeoSeries([area_geometry], crs=metric_crs).to_crs("EPSG:4326").iloc[0]
    analysis = gpd.GeoDataFrame(
        [{"region_id": region_id, "analysis_status": "demo_analysis_area", "buffer_m": float(cfg["project"]["network_buffer_m"])}],
        geometry=[area_geometry], crs="EPSG:4326",
    )
    analysis.to_file(paths["analysis_area"], driver="GeoJSON", index=False)
    metadata = {
        "region_id": region_id,
        "boundary_status": boundary_status,
        "source_url": source_url,
        "source_note": source_note,
        "query": query,
        "crs": "EPSG:4326",
        "geometry_valid": bool(geometry.is_valid),
        "boundary_sha256": sha256(paths["boundary"]),
        "analysis_area_sha256": sha256(paths["analysis_area"]),
        "retrieved_at_utc": utc_now(),
    }
    paths["boundary_metadata"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def clip_population(region_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    paths = paths_for(region_id)
    area = gpd.read_file(paths["analysis_area"]).geometry.iloc[0]
    raster_path = ROOT / cfg["project"]["population_raster"]
    with rasterio.open(raster_path) as source:
        area_projected = gpd.GeoDataFrame(geometry=[area], crs="EPSG:4326").to_crs(source.crs).geometry.iloc[0]
        nodata = source.nodata if source.nodata is not None else -99999
        clipped, transform = mask(source, [mapping(area_projected)], crop=True, filled=True, nodata=nodata, all_touched=False)
        metadata = source.meta.copy()
        metadata.update({"height": clipped.shape[1], "width": clipped.shape[2], "transform": transform, "nodata": nodata, "compress": "lzw"})
        with rasterio.open(paths["population"], "w", **metadata) as target:
            target.write(clipped)
        array = clipped[0].astype("float64")
        valid = np.isfinite(array) & (array != nodata) & (array >= 0)
        positive = valid & (array > 0)
        rows, cols = np.where(positive)
        xs, ys = xy(transform, rows, cols, offset="center")
        values = array[rows, cols]
        demand = gpd.GeoDataFrame(
            {
                "demand_id": [f"{region_id}__d_{int(row):05d}_{int(col):05d}" for row, col in zip(rows, cols)],
                "region_id": region_id,
                "row": rows.astype(int),
                "col": cols.astype(int),
                "pop_est": values.astype(float),
                "population_year": int(cfg["project"]["population_year"]),
                "population_source": "WorldPop Global2 R2025A v1",
                "population_status": "modelled_estimate",
                "data_status": "original",
            },
            geometry=[Point(x, y) for x, y in zip(xs, ys)], crs=source.crs,
        ).to_crs("EPSG:4326")
    demand.to_file(paths["demand"], driver="GeoJSON", index=False)
    stats = {
        "region_id": region_id,
        "raster_crs": str(metadata.get("crs")),
        "nodata": nodata,
        "valid_cells": int(valid.sum()),
        "positive_cells": int(len(demand)),
        "population_sum": float(values.sum()),
        "demand_node_sum": float(demand["pop_est"].sum()),
        "totals_reconcile": bool(np.isclose(float(values.sum()), float(demand["pop_est"].sum()), rtol=1e-6, atol=1e-3)),
    }
    paths["population_stats"].write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def build_network(region_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    import osmnx as ox

    paths = paths_for(region_id)
    area = gpd.read_file(paths["analysis_area"]).geometry.iloc[0]
    area_gdf = gpd.GeoDataFrame(geometry=[area], crs="EPSG:4326")
    metric_crs = area_gdf.estimate_utm_crs()
    area_metric = area_gdf.to_crs(metric_crs).geometry.iloc[0]
    buffered = area_metric.buffer(float(cfg["project"]["network_buffer_m"]))
    buffered_wgs84 = gpd.GeoSeries([buffered], crs=metric_crs).to_crs("EPSG:4326").iloc[0]
    cache_dir = paths["interim"] / "osmnx_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir)
    ox.settings.timeout = 180
    graph = None
    errors = []
    endpoints = cfg["project"].get("overpass_endpoints", ["https://overpass-api.de/api/interpreter"])
    for endpoint in endpoints:
        ox.settings.overpass_url = endpoint
        for attempt in range(2):
            try:
                graph = ox.graph_from_polygon(buffered_wgs84, network_type="walk", simplify=True, retain_all=True, truncate_by_edge=True)
                break
            except Exception as exc:
                errors.append(f"{endpoint} attempt {attempt + 1}: {type(exc).__name__}: {exc}")
                time.sleep(2 ** attempt)
        if graph is not None:
            break
    if graph is None:
        raise RuntimeError("All Overpass endpoints failed: " + " | ".join(errors[-3:]))
    if not graph.nodes or not graph.edges:
        raise RuntimeError(f"OSMnx returned an empty graph for {region_id}")
    projected = ox.project_graph(graph, to_crs=metric_crs)
    ox.save_graphml(projected, filepath=paths["graph"])
    nodes, edges = ox.graph_to_gdfs(graph, nodes=True, edges=True, fill_edge_geometry=True)
    edges.to_crs("EPSG:4326").to_file(paths["road_edges"], driver="GeoJSON", index=False)
    gpd.GeoDataFrame([{"region_id": region_id}], geometry=[buffered_wgs84], crs="EPSG:4326").to_file(paths["network_buffer"], driver="GeoJSON", index=False)
    components = list(nx.connected_components(graph.to_undirected()))
    metadata = {
        "region_id": region_id,
        "source": "OpenStreetMap contributors via OSMnx graph_from_polygon",
        "retrieved_at_utc": utc_now(),
        "network_type": "walk",
        "network_buffer_m": float(cfg["project"]["network_buffer_m"]),
        "graph_nodes": int(len(graph.nodes)),
        "graph_edges_directed": int(len(graph.edges)),
        "connected_components": int(len(components)),
        "largest_component_share": len(max(components, key=len)) / max(len(graph.nodes), 1),
        "projected_crs": str(projected.graph.get("crs")),
        "graphml_sha256": sha256(paths["graph"]),
        "source_note": "Live query result; no fixed PBF snapshot.",
        "overpass_endpoint": getattr(ox.settings, "overpass_url", None),
    }
    paths["graph_metadata"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def reuse_legacy_east_network(region_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Use the frozen v0.1 East graph only as a documented outage fallback."""
    if region_id != "east_district":
        raise RuntimeError("Legacy network fallback is only defined for east_district")
    paths = paths_for(region_id)
    legacy_graph = ROOT / "data/processed/road_walk.graphml"
    legacy_edges = ROOT / "data/processed/map_layers/road_edges.geojson"
    legacy_buffer = ROOT / "data/processed/map_layers/network_buffer.geojson"
    if not legacy_graph.is_file() or not legacy_edges.is_file():
        raise FileNotFoundError("Legacy East v0.1 network artifacts are incomplete")
    shutil.copy2(legacy_graph, paths["graph"])
    shutil.copy2(legacy_edges, paths["road_edges"])
    if legacy_buffer.is_file():
        shutil.copy2(legacy_buffer, paths["network_buffer"])
    import osmnx as ox
    graph = ox.load_graphml(filepath=paths["graph"])
    components = list(nx.connected_components(graph.to_undirected()))
    metadata = {
        "region_id": region_id,
        "source_status": "legacy_v0_1_reused_due_to_overpass_outage",
        "source_path": str(legacy_graph.relative_to(ROOT)),
        "graph_nodes": int(len(graph.nodes)),
        "graph_edges_directed": int(len(graph.edges)),
        "connected_components": int(len(components)),
        "largest_component_share": len(max(components, key=len)) / max(len(graph.nodes), 1),
        "graphml_sha256": sha256(paths["graph"]),
        "note": "The old East demo graph was reused locally; it was not regenerated for the v2 boundary and remains DEMO_ONLY.",
    }
    paths["graph_metadata"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def _graph_node_key(value: Any, graph) -> Any:
    if value in graph:
        return value
    try:
        integer = int(value)
        if integer in graph:
            return integer
    except (TypeError, ValueError):
        pass
    return value


def build_od(region_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    import osmnx as ox
    import pyarrow as pa
    import pyarrow.parquet as pq

    paths = paths_for(region_id)
    demand = gpd.read_file(paths["demand"])
    shelters = gpd.read_file(paths["shelters"])
    if demand.empty or shelters.empty:
        raise RuntimeError(f"Cannot build OD for {region_id}: demand or spatial shelters empty")
    graph = ox.load_graphml(filepath=paths["graph"]).to_undirected()
    max_snap = float(cfg["project"]["max_snap_distance_m"])
    speed = float(cfg["project"]["walking_speed_m_per_s"])
    graph_crs = graph.graph.get("crs")
    demand_projected = demand.to_crs(graph_crs)
    shelter_projected = shelters.to_crs(graph_crs)
    d_nearest, d_distances = ox.distance.nearest_nodes(graph, X=demand_projected.geometry.x, Y=demand_projected.geometry.y, return_dist=True)
    s_nearest, s_distances = ox.distance.nearest_nodes(graph, X=shelter_projected.geometry.x, Y=shelter_projected.geometry.y, return_dist=True)
    d_audit = pd.DataFrame({"demand_id": demand["demand_id"].astype(str), "network_node": [_graph_node_key(value, graph) for value in d_nearest], "snap_distance_m": [float(v) for v in d_distances]})
    d_audit["snap_ok"] = d_audit["snap_distance_m"].le(max_snap)
    s_audit = pd.DataFrame({"shelter_id": shelters["shelter_id"].astype(str), "network_node": [_graph_node_key(value, graph) for value in s_nearest], "snap_distance_m": [float(v) for v in s_distances]})
    s_audit["snap_ok"] = s_audit["snap_distance_m"].le(max_snap)
    d_audit.to_csv(paths["demand_snap"], index=False)
    s_audit.to_csv(paths["shelter_snap"], index=False)
    lengths = {}
    for _, row in s_audit.iterrows():
        lengths[row["shelter_id"]] = nx.single_source_dijkstra_path_length(graph, row["network_node"], weight="length") if row["snap_ok"] else {}
    data_version = f"{cfg['project']['data_version']}__{region_id}"
    schema = pa.schema([
        ("region_id", pa.string()), ("demand_id", pa.string()), ("shelter_id", pa.string()),
        ("distance_m", pa.float64()), ("travel_time_min", pa.float64()), ("time_min", pa.float64()),
        ("reachable", pa.bool_()), ("reason", pa.string()), ("demand_snap_m", pa.float64()),
        ("shelter_snap_m", pa.float64()), ("walking_speed_m_per_s", pa.float64()), ("data_version", pa.string()),
    ])
    batch_size = int(cfg["project"].get("od_demand_batch_size", 1024))
    temp_path = paths["matrix"].with_suffix(".parquet.tmp")
    temp_path.unlink(missing_ok=True)
    writer = None
    row_count = 0
    reachable_pairs = 0
    reason_counts: Counter[str] = Counter()
    try:
        for start in range(0, len(d_audit), batch_size):
            rows = {name: [] for name in schema.names}
            for _, drow in d_audit.iloc[start:start + batch_size].iterrows():
                for _, srow in s_audit.iterrows():
                    distance = None
                    time_min = None
                    if not drow["snap_ok"]:
                        reason = "demand_snap_too_far"
                    elif not srow["snap_ok"]:
                        reason = "shelter_snap_too_far"
                    else:
                        network_distance = lengths[srow["shelter_id"]].get(drow["network_node"])
                        if network_distance is None:
                            reason = "no_path"
                        else:
                            access_distance = float(drow["snap_distance_m"]) + float(srow["snap_distance_m"])
                            distance = float(network_distance) + access_distance
                            time_min = distance / speed / 60
                            reason = "ok"
                    rows["region_id"].append(region_id)
                    rows["demand_id"].append(str(drow["demand_id"]))
                    rows["shelter_id"].append(str(srow["shelter_id"]))
                    rows["distance_m"].append(distance)
                    rows["travel_time_min"].append(time_min)
                    rows["time_min"].append(time_min)
                    rows["reachable"].append(reason == "ok")
                    rows["reason"].append(reason)
                    rows["demand_snap_m"].append(float(drow["snap_distance_m"]))
                    rows["shelter_snap_m"].append(float(srow["snap_distance_m"]))
                    rows["walking_speed_m_per_s"].append(speed)
                    rows["data_version"].append(data_version)
                    row_count += 1
                    reachable_pairs += int(reason == "ok")
                    reason_counts[reason] += 1
            table = pa.Table.from_pydict(rows, schema=schema)
            if writer is None:
                writer = pq.ParquetWriter(temp_path, schema=schema, compression="zstd")
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()
    temp_path.replace(paths["matrix"])
    return {
        "rows": int(row_count),
        "expected_rows": int(len(demand) * len(shelters)),
        "reachable_pairs": int(reachable_pairs),
        "reason_counts": dict(reason_counts),
        "demand_nodes": int(len(demand)),
        "spatial_shelters": int(len(shelters)),
        "od_demand_batch_size": batch_size,
    }


def _quantile_level(values: pd.Series) -> list[str]:
    if len(values) < 3 or values.nunique() < 3:
        return ["medium"] * len(values)
    ranks = values.rank(method="first", pct=True)
    return ["low" if rank <= 1 / 3 else "medium" if rank <= 2 / 3 else "high" for rank in ranks]


def run_spatial_analysis(region_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    paths = paths_for(region_id)
    layers = []
    demand = gpd.read_file(paths["demand"])
    demand["pop_density_persons_per_km2"] = demand["pop_est"].astype(float) / 0.01
    demand["population_class"] = pd.qcut(demand["pop_est"].rank(method="first"), 3, labels=["low", "medium", "high"]).astype(str) if len(demand) >= 3 else "medium"
    demand.to_file(paths["layers"] / "population_heatmap.geojson", driver="GeoJSON", index=False)
    layers.append({"layer_id": "population_heatmap", "layer_name": "人口热力图", "path": str((paths["layers"] / "population_heatmap.geojson").relative_to(ROOT)), "format": "GeoJSON", "crs": "EPSG:4326", "geometry_type": "Point", "description": "WorldPop modeled population per 100m cell; not observed evacuees.", "fields": ["demand_id", "pop_est", "pop_density_persons_per_km2", "population_class"], "data_status": "original_modelled_estimate"})
    with rasterio.open(paths["population"]) as source:
        array = source.read(1).astype("float32")
        nodata = source.nodata if source.nodata is not None else -99999
        density = np.where(array == nodata, nodata, array / 0.01).astype("float32")
        meta = source.meta.copy()
        meta.update(dtype="float32", nodata=nodata, compress="lzw")
        with rasterio.open(paths["population_heatmap"], "w", **meta) as target:
            target.write(density, 1)
    paths["population_stats"].write_text(json.dumps({"region_id": region_id, "population_sum": float(demand["pop_est"].sum()), "demand_nodes": int(len(demand)), "cell_area_km2": 0.01, "population_status": "modelled_estimate"}, ensure_ascii=False, indent=2), encoding="utf-8")
    layers.append({"layer_id": "population_density_raster", "layer_name": "人口密度栅格", "path": str(paths["population_heatmap"].relative_to(ROOT)), "format": "GeoTIFF", "crs": "EPSG:4326", "geometry_type": "Raster", "description": "Population per km2 derived from 100m WorldPop cell totals.", "fields": [], "data_status": "original_modelled_estimate"})

    shelters = gpd.read_file(paths["shelters"])
    shelters["capacity_level"] = _quantile_level(shelters["capacity_persons_effective"].astype(float))
    shelters.to_file(paths["capacity_layer"], driver="GeoJSON", index=False)
    layers.append({"layer_id": "shelter_capacity", "layer_name": "避难场所容量分布", "path": str(paths["capacity_layer"].relative_to(ROOT)), "format": "GeoJSON", "crs": "EPSG:4326", "geometry_type": "Point", "description": "Effective demo capacity with original/imputed provenance.", "fields": ["shelter_id", "capacity_persons_effective", "capacity_level", "capacity_source_status", "verified_status", "active"], "data_status": "DEMO_ONLY"})

    coverage_stats = {}
    if paths["matrix"].is_file():
        matrix = pd.read_parquet(paths["matrix"])
        reachable = matrix[matrix["reachable"].astype(bool)].copy()
        idx = reachable.groupby("demand_id")["time_min"].idxmin()
        nearest = reachable.loc[idx, ["demand_id", "shelter_id", "time_min", "distance_m"]].rename(columns={"shelter_id": "nearest_shelter_id", "time_min": "nearest_time_min", "distance_m": "nearest_distance_m"})
        demand = demand.merge(nearest, on="demand_id", how="left")
        for threshold in (10, 15, 20, 30):
            covered = demand["nearest_time_min"].notna() & demand["nearest_time_min"].le(threshold)
            layer = demand.copy()
            layer["covered"] = covered
            layer["coverage_threshold_min"] = threshold
            out = paths["layers"] / f"coverage_{threshold}min.geojson"
            layer.to_file(out, driver="GeoJSON", index=False)
            population = float(layer.loc[covered, "pop_est"].sum())
            stats = {"region_id": region_id, "threshold_min": threshold, "demand_nodes": int(len(layer)), "covered_nodes": int(covered.sum()), "uncovered_nodes": int((~covered).sum()), "population_with_candidate": population, "population_total": float(layer["pop_est"].sum()), "population_share": population / float(layer["pop_est"].sum()) if layer["pop_est"].sum() else 0.0, "interpretation": "network candidate reachability only; no capacity allocation"}
            (paths["layers"] / f"coverage_{threshold}min_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
            coverage_stats[str(threshold)] = stats
            layers.append({"layer_id": f"coverage_{threshold}min", "layer_name": f"{threshold}分钟步行覆盖", "path": str(out.relative_to(ROOT)), "format": "GeoJSON", "crs": "EPSG:4326", "geometry_type": "Point", "description": "Demand-cell reachability before capacity allocation.", "fields": ["demand_id", "pop_est", "nearest_shelter_id", "nearest_time_min", "covered"], "data_status": "DEMO_ONLY"})
            gaps = layer[~covered].copy()
            gap_out = paths["layers"] / f"access_gaps_{threshold}min.geojson"
            gaps.to_file(gap_out, driver="GeoJSON", index=False)
            layers.append({"layer_id": f"access_gaps_{threshold}min", "layer_name": f"{threshold}分钟潜在服务缺口", "path": str(gap_out.relative_to(ROOT)), "format": "GeoJSON", "crs": "EPSG:4326", "geometry_type": "Point", "description": "No candidate shelter within the threshold; not capacity-unserved demand.", "fields": ["demand_id", "pop_est", "nearest_shelter_id", "nearest_time_min"], "data_status": "DEMO_ONLY"})
    paths["layers_manifest"].write_text(json.dumps({"schema_version": 1, "region_id": region_id, "layers": layers}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"layers": len(layers), "coverage": coverage_stats}


def write_quality_and_manifest(region_id: str, cfg: dict[str, Any], results: dict[str, Any]) -> None:
    paths = paths_for(region_id)
    data_version = f"{cfg['project']['data_version']}__{region_id}"
    status = "DEMO_ONLY"
    release_status = "PASS_DEMO" if not results.get("errors") else "PARTIAL"
    b_cfg = {
        "project": {"name": "ShelterPlan Module A v2", "data_version": data_version, "region_id": region_id, "data_status": status},
        "paths": {"shelters_geojson": str(paths["shelters"].relative_to(ROOT)), "demand_nodes": str(paths["demand"].relative_to(ROOT)), "travel_matrix": str(paths["matrix"].relative_to(ROOT)), "manifest": str(paths["manifest"].relative_to(ROOT)), "a7_quality_summary": str(paths["quality"].relative_to(ROOT))},
        "network": {"max_snap_distance_m": cfg["project"]["max_snap_distance_m"]},
    }
    paths["b_config"].parent.mkdir(parents=True, exist_ok=True)
    paths["b_config"].write_text(yaml.safe_dump(b_cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    quality = {
        "region_id": region_id,
        "data_version": data_version,
        "release_status": release_status,
        "verdict": status,
        "data_status": status,
        "real_pilot_accepted": False,
        "source_rows": results.get("shelters", {}).get("source_rows"),
        "spatial_shelters": results.get("shelters", {}).get("spatial_rows"),
        "unresolved_shelters": results.get("shelters", {}).get("unresolved_rows"),
        "population": results.get("population", {}),
        "network": results.get("network", {}),
        "travel_matrix": results.get("od", {}),
        "spatial_analysis": results.get("spatial", {}),
        "blockers": ["Demo coordinates are approximate or unresolved.", "2024 capacities are original or regional-median demo imputations.", "Boundary is public OSM or demo-derived, not an official government boundary.", "Current open status is assumed_open for demo only."],
    }
    paths["quality"].write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    files = {}
    for path in sorted(paths["base"].rglob("*")):
        if path.is_file() and path.name not in {"manifest.json", "run_status.json"}:
            rel = str(path.relative_to(ROOT)).replace("\\", "/")
            files[rel] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    adapter_rel = str(paths["b_config"].relative_to(ROOT)).replace("\\", "/")
    files[adapter_rel] = {"bytes": paths["b_config"].stat().st_size, "sha256": sha256(paths["b_config"])}
    manifest = {
        "schema_version": 1,
        "region_id": region_id,
        "data_version": data_version,
        "release_status": release_status,
        "quality_release_status": release_status,
        "created_at_utc": utc_now(),
        "source": {"shelter_clean": cfg["project"]["source_clean"], "population": cfg["project"]["population_raster"], "walking_network": "OpenStreetMap contributors via OSMnx"},
        "assumptions": {"population_demand_ratio": "Scenario parameter; not baked into demand_nodes", "walking_speed_m_per_s": cfg["project"]["walking_speed_m_per_s"], "max_snap_distance_m": cfg["project"]["max_snap_distance_m"], "current_open_status": "assumed_open", "coordinate_status": "approximate_or_unresolved"},
        "quality_summary": str(paths["quality"].relative_to(ROOT)),
        "b_adapter": {"path": adapter_rel, "sha256": files[adapter_rel]["sha256"], "bytes": files[adapter_rel]["bytes"]},
        "hash_policy": "files contains every region artifact and the B adapter; manifest.json and run_status.json are excluded to avoid self or post-run circular hashes.",
        "files": files,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def run_region(region_id: str, force_legacy_network: bool = False, skip_network: bool = False) -> dict[str, Any]:
    cfg = load_config()
    if region_id not in cfg["regions"]:
        raise KeyError(f"Unknown region_id: {region_id}")
    ensure_dirs(region_id)
    result: dict[str, Any] = {"region_id": region_id, "status": "PARTIAL", "steps": {}, "errors": []}
    try:
        result["shelters"] = prepare_shelters(region_id, cfg)
        result["steps"]["shelters"] = "PASS"
    except Exception as exc:
        result["errors"].append(f"shelters: {type(exc).__name__}: {exc}")
        paths_for(region_id)["run_status"].write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    try:
        result["boundary"] = build_boundary(region_id, cfg, result["shelters"]["shelters"])
        result["steps"]["boundary"] = "PASS"
        result["population"] = clip_population(region_id, cfg)
        result["steps"]["population"] = "PASS"
    except Exception as exc:
        result["errors"].append(f"boundary_or_population: {type(exc).__name__}: {exc}")
    if skip_network:
        result["steps"]["network"] = "BLOCKED_EXTERNAL_OVERPASS"
        result["steps"]["od"] = "BLOCKED_EXTERNAL_OVERPASS"
        result["errors"].append("network_or_od: skipped after repeated public Overpass endpoint timeouts")
    else:
      try:
        if force_legacy_network:
            if region_id != "east_district":
                raise RuntimeError("--reuse-legacy-network is only valid for east_district")
            result["network"] = reuse_legacy_east_network(region_id, cfg)
            result["steps"]["network"] = "FALLBACK_LEGACY_V0_1"
        else:
            result["network"] = build_network(region_id, cfg)
            result["steps"]["network"] = "PASS"
        result["od"] = build_od(region_id, cfg)
        result["steps"]["od"] = "PASS"
      except Exception as exc:
        if region_id == "east_district":
            try:
                result["network"] = reuse_legacy_east_network(region_id, cfg)
                result["steps"]["network"] = "FALLBACK_LEGACY_V0_1"
                result["od"] = build_od(region_id, cfg)
                result["steps"]["od"] = "PASS_WITH_LEGACY_NETWORK"
            except Exception as fallback_exc:
                result["errors"].append(f"network_or_od: {type(exc).__name__}: {exc}; fallback: {type(fallback_exc).__name__}: {fallback_exc}")
        else:
            result["errors"].append(f"network_or_od: {type(exc).__name__}: {exc}")
    try:
        result["spatial"] = run_spatial_analysis(region_id, cfg)
        result["steps"]["spatial"] = "PASS"
    except Exception as exc:
        result["errors"].append(f"spatial: {type(exc).__name__}: {exc}")
    result["status"] = "DEMO_ONLY" if not result["errors"] else "PARTIAL"
    write_quality_and_manifest(region_id, cfg, result)
    paths_for(region_id)["run_status"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result
