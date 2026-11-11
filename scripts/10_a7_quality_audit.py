"""Automated A7 quality audit for ShelterPlan demo/real pilot data."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import rasterio
import osmnx as ox

from common import load_config, read_analysis_area, resolve, sha256_file


def stats(series):
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {"count": 0}
    return {
        "count": int(values.size),
        "min": float(values.min()),
        "median": float(values.median()),
        "p95": float(values.quantile(0.95)),
        "max": float(values.max()),
        "mean": float(values.mean()),
    }


def main():
    cfg = load_config()
    demand_path = resolve(cfg["paths"]["demand_nodes"])
    shelters_path = resolve(cfg["paths"]["shelters_geojson"])
    matrix_path = resolve(cfg["paths"]["travel_matrix"])
    graph_path = resolve(cfg["paths"]["road_graph"])
    raster_path = resolve(cfg["paths"]["population_clip"])
    for path in (demand_path, shelters_path, matrix_path, graph_path, raster_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    demand = gpd.read_file(demand_path)
    shelters = gpd.read_file(shelters_path)
    matrix = pd.read_parquet(matrix_path)
    _, area = read_analysis_area(cfg)
    errors, warnings = [], []
    for label, gdf in (("demand", demand), ("shelters", shelters)):
        if gdf.crs is None or gdf.crs.to_epsg() != 4326:
            errors.append(f"{label} layer must declare EPSG:4326.")
        if gdf.geometry.isna().any() or gdf.geometry.is_empty.any() or (~gdf.geometry.is_valid).any():
            errors.append(f"{label} layer contains null, empty, or invalid geometry.")
    if "demand_id" not in demand or demand["demand_id"].duplicated().any():
        errors.append("Demand IDs are missing or duplicated.")

    # Population raster and vector reconciliation.
    with rasterio.open(raster_path) as src:
        arr = src.read(1, masked=True)
        raster_crs = str(src.crs)
        raster_nodata = src.nodata
        valid = np.asarray(arr.compressed(), dtype="float64")
        valid = valid[np.isfinite(valid) & (valid >= 0)]
        raster_sum = float(valid.sum())
        raster_valid_cells = int(valid.size)
    node_sum = float(pd.to_numeric(demand["pop_est"], errors="coerce").sum())
    if raster_crs != "EPSG:4326":
        warnings.append(f"Population clip CRS is {raster_crs}, expected EPSG:4326.")
    if len(demand) == 0 or not np.isclose(raster_sum, node_sum, rtol=1e-6, atol=1e-3):
        errors.append("Population raster and demand-node totals do not reconcile.")

    demand_inside = demand.geometry.within(area) | demand.geometry.intersects(area.boundary)
    demand_edge_dist = demand.to_crs(demand.estimate_utm_crs()).geometry.distance(
        gpd.GeoSeries([area], crs="EPSG:4326").to_crs(demand.estimate_utm_crs()).iloc[0].boundary
    )
    edge_count_100m = int((demand_edge_dist <= 100).sum())

    # Shelter provenance and coordinates.
    if "shelter_id" not in shelters or shelters["shelter_id"].duplicated().any():
        errors.append("Shelter IDs are missing or duplicated.")
    cap = pd.to_numeric(shelters.get("capacity_persons"), errors="coerce")
    if cap.isna().any() or (cap <= 0).any():
        errors.append("Shelter capacities contain missing/non-positive values.")
    unverified = int(shelters.get("verified_status", pd.Series("unknown", index=shelters.index)).astype(str).ne("verified").sum())
    if unverified:
        warnings.append(f"{unverified}/{len(shelters)} shelters are not verified; results are DEMO_ONLY.")
    coord_status = shelters.get("coordinate_status", pd.Series("unknown", index=shelters.index)).astype(str)
    approximate_coords = int(coord_status.str.contains("approximate|demo|unknown", case=False, regex=True).sum())
    coordinate_methods = shelters.get("coordinate_method", pd.Series("unknown", index=shelters.index)).fillna("unknown").astype(str)
    coordinate_sources = shelters.get("coordinate_source_url", pd.Series("", index=shelters.index)).fillna("").astype(str).str.strip()
    allowed_coordinate_methods = {"surveyed_gnss", "authoritative_wgs84"}
    coordinate_evidence_missing = int((~coordinate_methods.isin(allowed_coordinate_methods) | coordinate_sources.eq("")).sum())
    capacity_evidence = shelters.get("capacity_verification_status", pd.Series("unverified", index=shelters.index)).fillna("unverified").astype(str)
    capacity_unverified = int(capacity_evidence.ne("verified_current").sum())
    clean_path = resolve(cfg["paths"]["shelters_clean"])
    source_df = pd.read_csv(clean_path) if clean_path.is_file() else pd.DataFrame()
    source_rows = int(len(source_df))
    portal_rows = int(cfg["project"].get("portal_expected_shelter_records", 906))
    portal_count_basis = cfg["project"].get("portal_record_count_basis", "Not recorded")
    batch_reconciliation_path = resolve("data/processed/shelter_batch_reconciliation.json")
    batch_reconciliation = json.loads(batch_reconciliation_path.read_text(encoding="utf-8")) if batch_reconciliation_path.is_file() else None
    reconciliation_inputs = [resolve("data/raw/portal_shelters_batch_1.xls"), resolve("data/raw/portal_shelters_batch_2.xls"), resolve(cfg["paths"]["shelter_excel"])]
    current_input_hashes = {path.name: sha256_file(path) for path in reconciliation_inputs if path.is_file()}
    recorded_input_hashes = batch_reconciliation.get("input_sha256", {}) if batch_reconciliation else {}
    batch_reconciliation_current = bool(len(current_input_hashes) == len(reconciliation_inputs) and recorded_input_hashes == current_input_hashes)
    portal_scope_reconciled = bool(batch_reconciliation and batch_reconciliation_current and batch_reconciliation.get("exact_batch_payloads_equal") and batch_reconciliation.get("local_matches_any_batch"))
    updated_values = sorted(source_df["updated_at"].dropna().astype(str).unique().tolist()) if "updated_at" in source_df else []
    east_source = source_df[source_df.get("town", pd.Series(dtype=str)).fillna("").astype(str).str.strip().eq(str(cfg["project"].get("pilot_town", "")))] if not source_df.empty else pd.DataFrame()
    pilot_input_path = resolve(cfg["paths"]["shelters_pilot_raw"])
    pilot_input = pd.read_csv(pilot_input_path, dtype={"shelter_id": str}) if pilot_input_path.is_file() else pd.DataFrame()
    candidate_path = resolve(cfg["paths"]["geocode_candidates"])
    geocode_candidates = pd.read_csv(candidate_path, dtype={"shelter_id": str}) if candidate_path.is_file() else pd.DataFrame()
    exclusion_path = resolve("data/interim/shelters_geocoding_exclusions.csv")
    geocode_exclusions = pd.read_csv(exclusion_path, dtype={"shelter_id": str}) if exclusion_path.is_file() else pd.DataFrame()
    east_ids = set(east_source.get("shelter_id", pd.Series(dtype=str)).dropna().astype(str))
    pilot_ids = set(pilot_input.get("shelter_id", pd.Series(dtype=str)).dropna().astype(str))
    candidate_ids = set(geocode_candidates.get("shelter_id", pd.Series(dtype=str)).dropna().astype(str))
    exclusion_ids = set(geocode_exclusions.get("shelter_id", pd.Series(dtype=str)).dropna().astype(str))
    held_ids = east_ids - pilot_ids
    unresolved_geocode_ids = pilot_ids - candidate_ids - exclusion_ids
    overlapping_geocode_ids = candidate_ids & exclusion_ids
    if unresolved_geocode_ids or overlapping_geocode_ids or (candidate_ids | exclusion_ids) - pilot_ids:
        errors.append("Pilot source-to-geocoding dispositions do not reconcile; inspect IDs across pilot input, candidates, and exclusions.")
    if held_ids:
        held_rows = east_source[east_source["shelter_id"].astype(str).isin(held_ids)]
        held_sources = held_rows[[c for c in ["shelter_id", "source_row_excel"] if c in held_rows]].to_dict("records")
        warnings.append(f"{len(held_ids)} East District source records are held out before geocoding: {held_sources}.")
    pilot_reconciliation = {
        "east_source_rows": len(east_source), "pilot_input_rows": len(pilot_input),
        "held_before_geocoding": held_sources if held_ids else [],
        "geocode_candidate_rows": len(geocode_candidates), "documented_exclusion_rows": len(geocode_exclusions),
        "unresolved_geocode_disposition_ids": sorted(unresolved_geocode_ids),
        "overlap_candidate_and_exclusion_ids": sorted(overlapping_geocode_ids),
        "dispositions_reconcile": not (unresolved_geocode_ids or overlapping_geocode_ids or (candidate_ids | exclusion_ids) - pilot_ids),
    }

    # Network and snap diagnostics.
    graph = ox.load_graphml(filepath=graph_path)
    components = list(nx.connected_components(graph.to_undirected()))
    largest_component = max((len(c) for c in components), default=0)
    snap_paths = {
        "demand": resolve("data/processed/demand_snap_audit.csv"),
        "shelter": resolve("data/processed/shelter_snap_audit.csv"),
    }
    snap = {}
    for label, path in snap_paths.items():
        if not path.is_file():
            warnings.append(f"Missing {label} snap audit: {path}")
            snap[label] = {"count": 0}
            continue
        audit = pd.read_csv(path)
        col = "demand_id" if label == "demand" else "shelter_id"
        snap[label] = {
            **stats(audit["snap_distance_m"]),
            "within_configured_max": int(audit["snap_ok"].astype(bool).sum()),
            "over_configured_max": int((~audit["snap_ok"].astype(bool)).sum()),
            "max_snap_distance_m": float(cfg["network"]["max_snap_distance_m"]),
            "ids": int(audit[col].nunique()),
        }

    # Matrix key, numeric, reason, and equation checks.
    required = {"demand_id", "shelter_id", "reachable", "reason", "distance_m", "time_min", "network_distance_m", "access_distance_m"}
    missing_cols = sorted(required - set(matrix.columns))
    if missing_cols:
        errors.append(f"Travel matrix missing required columns: {missing_cols}")
    else:
        if matrix.duplicated(["demand_id", "shelter_id"]).any():
            errors.append("Travel matrix has duplicate demand/shelter pairs.")
        expected = len(demand) * len(shelters)
        if len(matrix) != expected:
            errors.append(f"Travel matrix has {len(matrix)} rows; expected {expected}.")
        known_d = set(demand["demand_id"].astype(str))
        known_s = set(shelters["shelter_id"].astype(str))
        if set(matrix["demand_id"].astype(str)) != known_d or set(matrix["shelter_id"].astype(str)) != known_s:
            errors.append("Travel matrix IDs do not exactly match the demand and shelter layers.")
        reachable = matrix["reachable"].astype(bool)
        r = matrix.loc[reachable].copy()
        dcalc = pd.to_numeric(r["network_distance_m"], errors="coerce") + pd.to_numeric(r["access_distance_m"], errors="coerce")
        if not np.allclose(pd.to_numeric(r["distance_m"], errors="coerce"), dcalc, rtol=1e-6, atol=0.02):
            errors.append("Travel distances do not equal network distance plus access distance.")
        expected_time = pd.to_numeric(r["distance_m"], errors="coerce") / float(cfg["network"]["walking_speed_m_per_s"]) / 60
        if not np.allclose(pd.to_numeric(r["time_min"], errors="coerce"), expected_time, rtol=1e-6, atol=1e-4):
            errors.append("Travel times do not reconcile to distance and configured walking speed.")
        reasons = matrix["reason"].fillna("<null>").value_counts().to_dict()
        threshold = float(cfg["scenario_defaults"]["max_walk_time_min"])
        within_time = r[pd.to_numeric(r["time_min"], errors="coerce") <= threshold]
        reachable_demand_ids = set(within_time["demand_id"].astype(str))
        no_service = int(len(known_d - reachable_demand_ids))
        if not within_time.empty and not np.isfinite(pd.to_numeric(within_time["time_min"], errors="coerce")).all():
            errors.append("Reachable matrix pairs contain non-finite travel times.")
    if missing_cols:
        reasons, within_time, reachable_demand_ids, no_service = {}, pd.DataFrame(), set(), len(demand)
        r = pd.DataFrame()
        reachable = pd.Series(dtype=bool)

    # Boundary provenance is explicit in the current configuration, not an official boundary.
    area_path = resolve(cfg["paths"]["analysis_polygon"])
    area_gdf = gpd.read_file(area_path)
    area_props = area_gdf.drop(columns="geometry").fillna("").astype(str)
    area_status = "; ".join(area_props.agg(" | ".join, axis=1).tolist())
    boundary_status = area_props.get("boundary_status", pd.Series("unknown", index=area_props.index)).str.lower()
    boundary_source = area_props.get("boundary_source_url", pd.Series("", index=area_props.index)).str.strip()
    boundary_year = pd.to_numeric(area_props.get("boundary_year", pd.Series(np.nan, index=area_props.index)), errors="coerce")
    is_official_area = bool(len(area_props) > 0 and boundary_status.eq("official").all() and boundary_source.ne("").all() and boundary_year.notna().all())
    if not is_official_area:
        warnings.append("Analysis area is not documented as an official administrative boundary.")

    real_pilot_blockers = []
    if source_rows != portal_rows and not portal_scope_reconciled:
        real_pilot_blockers.append(f"Downloaded shelter workbook has {source_rows} rows while portal catalog metadata reports {portal_rows}; two 2024 batches share the same first 20 preview rows, but full-batch reconciliation is missing or inconclusive.")
    if unverified:
        real_pilot_blockers.append(f"{unverified} included shelters lack verified status.")
    if approximate_coords:
        real_pilot_blockers.append(f"{approximate_coords} shelter coordinates use approximate/demo methods.")
    if coordinate_evidence_missing:
        real_pilot_blockers.append(f"{coordinate_evidence_missing} shelter points lack an approved WGS84 method and coordinate source URL.")
    if capacity_unverified:
        real_pilot_blockers.append(f"{capacity_unverified} shelter capacities lack current verification evidence.")
    if not is_official_area:
        real_pilot_blockers.append("Analysis polygon is not an authoritative administrative boundary.")
    if updated_values and any("2024" in value for value in updated_values):
        real_pilot_blockers.append("Shelter register records are from 2024; current capacity/open status is not established.")
    demo_ready = bool(not errors and len(demand) > 0 and len(shelters) > 0 and len(matrix) > 0 and int(reachable.sum()) > 0)
    route_path = resolve("data/processed/map_layers/route_example.geojson")
    route_exists = route_path.is_file()
    if not route_exists:
        warnings.append("No route example layer exists for review.")

    summary = {
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_version": cfg["project"]["data_version"],
        "verdict": "FAIL" if errors else ("DEMO_ONLY" if real_pilot_blockers else "PASS"),
        "demo_ready": demo_ready,
        "real_pilot_accepted": bool(not errors and not real_pilot_blockers),
        "real_pilot_blockers": real_pilot_blockers,
        "shelter_register": {"source_rows": source_rows, "portal_catalog_rows": portal_rows, "portal_count_basis": portal_count_basis, "portal_attachment_batches": cfg["project"].get("portal_attachment_batches", []), "batch_reconciliation": batch_reconciliation, "batch_reconciliation_current": batch_reconciliation_current, "scope_reconciled": portal_scope_reconciled, "updated_values": updated_values},
        "pilot_reconciliation": pilot_reconciliation,
        "coordinate_evidence_missing_count": coordinate_evidence_missing,
        "capacity_unverified_count": capacity_unverified,
        "population": {
            "raster_crs": raster_crs, "nodata": raster_nodata,
            "valid_nonnegative_cells": raster_valid_cells,
            "raster_population_sum": raster_sum,
            "demand_nodes": int(len(demand)), "demand_population_sum": node_sum,
            "within_analysis_area": int(demand_inside.sum()), "outside_analysis_area": int((~demand_inside).sum()),
            "within_100m_of_analysis_boundary": edge_count_100m,
        },
        "shelters": {
            "count": int(len(shelters)), "unverified_count": unverified,
            "approximate_or_unknown_coordinate_count": approximate_coords,
            "coordinate_evidence_missing_count": coordinate_evidence_missing,
            "capacity_unverified_count": capacity_unverified,
            "capacity_sum_persons": float(cap.sum()),
            "coordinate_status_counts": coord_status.value_counts().to_dict(),
        },
        "network": {
            "nodes": int(len(graph.nodes)), "directed_edges": int(len(graph.edges)),
            "components": int(len(components)), "largest_component_nodes": int(largest_component),
            "largest_component_share": largest_component / max(len(graph.nodes), 1),
            "snap": snap,
        },
        "travel_matrix": {
            "rows": int(len(matrix)), "expected_rows": int(len(demand) * len(shelters)),
            "reachable_pairs": int(reachable.sum()), "unreachable_pairs": int((~reachable).sum()),
            "reason_counts": {str(k): int(v) for k, v in reasons.items()},
            "time_threshold_min": float(cfg["scenario_defaults"]["max_walk_time_min"]),
            "demand_nodes_with_at_least_one_pair_within_threshold": int(len(reachable_demand_ids)),
            "demand_nodes_without_pair_within_threshold": int(no_service),
            "pairs_within_time_threshold": int(len(within_time)),
        },
        "analysis_area": {"path": str(area_path.relative_to(resolve("."))), "is_official_boundary": is_official_area, "properties": area_status},
        "route_example_exists": route_exists,
        "errors": errors, "warnings": warnings,
    }

    out_json = resolve("data/processed/a7_quality_summary.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    reason_path = resolve("data/processed/travel_matrix_reason_counts.csv")
    pd.Series(summary["travel_matrix"]["reason_counts"], name="pair_count").rename_axis("reason").to_csv(reason_path)

    lines = [
        "# A7 independent GIS quality audit", "",
        f"- Audited UTC: `{summary['audited_at_utc']}`",
        f"- Verdict: **{summary['verdict']}**",
        f"- Demo ready: **{summary['demo_ready']}**",
        f"- Real pilot accepted: **{summary['real_pilot_accepted']}**",
        f"- Shelter source rows / portal catalog count: `{source_rows}` / `{portal_rows}`; update values: `{updated_values}`",
        f"- Portal count interpretation: `{portal_count_basis}`",
        f"- Full batch payload reconciliation current and matches local inputs: `{portal_scope_reconciled}`", "",
        "## Population", "",
        f"- CRS / NoData: `{raster_crs}` / `{raster_nodata}`",
        f"- Non-negative valid raster cells: `{raster_valid_cells}`",
        f"- Raster sum / demand-node sum: `{raster_sum:.3f}` / `{node_sum:.3f}`",
        f"- Demand points within 100 m of analysis boundary: `{edge_count_100m}`", "",
        "## Pilot source-to-geocode reconciliation", "",
        f"- East District source / pilot input rows: `{pilot_reconciliation['east_source_rows']}` / `{pilot_reconciliation['pilot_input_rows']}`",
        f"- Held before geocoding: `{pilot_reconciliation['held_before_geocoding']}`",
        f"- Geocode candidates / documented exclusions: `{pilot_reconciliation['geocode_candidate_rows']}` / `{pilot_reconciliation['documented_exclusion_rows']}`; dispositions reconcile: `{pilot_reconciliation['dispositions_reconcile']}`", "",
        "## Shelters", "",
        f"- Included: `{len(shelters)}`; unverified: `{unverified}`; approximate/unknown coordinates: `{approximate_coords}`",
        f"- Registered capacity sum (unverified): `{float(cap.sum()):.0f}` persons",
        f"- Coordinate evidence missing: `{coordinate_evidence_missing}`; current capacity evidence missing: `{capacity_unverified}`", "",
        "## Network and matching", "",
        f"- Graph nodes/edges: `{len(graph.nodes)}` / `{len(graph.edges)}`",
        f"- Connected components: `{len(components)}`; largest component share: `{largest_component / max(len(graph.nodes), 1):.4f}`",
        f"- Demand snap within configured threshold: `{snap.get('demand', {}).get('within_configured_max', 0)}/{snap.get('demand', {}).get('count', 0)}`; over threshold: `{snap.get('demand', {}).get('over_configured_max', 0)}`",
        f"- Shelter snap within configured threshold: `{snap.get('shelter', {}).get('within_configured_max', 0)}/{snap.get('shelter', {}).get('count', 0)}`", "",
        "## Travel matrix", "",
        f"- Rows / expected: `{len(matrix)}` / `{len(demand) * len(shelters)}`",
        f"- Reachable pairs: `{int(reachable.sum())}`; unreachable pairs: `{int((~reachable).sum())}`",
        f"- Pairs within configured {cfg['scenario_defaults']['max_walk_time_min']} min scenario threshold: `{len(within_time)}`",
        f"- Demand nodes with no pair within threshold: `{no_service}`",
        "- Unreachable reason counts:",
        *[f"  - `{k}`: {v}" for k, v in summary["travel_matrix"]["reason_counts"].items()], "",
        "## Real-pilot blockers", *([f"- {x}" for x in real_pilot_blockers] if real_pilot_blockers else ["- None"]),
        "", "## Errors", *([f"- {x}" for x in errors] if errors else ["- None"]),
        "", "## Warnings", *([f"- {x}" for x in warnings] if warnings else ["- None"]),
        "", "This audit checks file structure, arithmetic consistency, matching distances and declared provenance. It cannot prove a shelter is currently open, validate its capacity, or turn an approximate coordinate into a surveyed location.",
    ]
    report = resolve("docs/data_quality_a7.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nJSON: {out_json}")
    print(f"Reason counts: {reason_path}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
