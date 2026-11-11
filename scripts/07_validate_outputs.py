import pandas as pd
import geopandas as gpd
from common import load_config, resolve

def main():
    cfg = load_config()
    required = {
        "shelters": resolve(cfg["paths"]["shelters_geojson"]),
        "demand": resolve(cfg["paths"]["demand_nodes"]),
        "matrix": resolve(cfg["paths"]["travel_matrix"])
    }
    missing = [str(p) for p in required.values() if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing outputs:\n" + "\n".join(missing))

    shelters = gpd.read_file(required["shelters"])
    demand = gpd.read_file(required["demand"])
    matrix = pd.read_parquet(required["matrix"])

    errors, warnings = [], []
    demo_count = int(shelters.get("verified_status", pd.Series("", index=shelters.index)).eq("demo_unverified").sum())
    if demo_count:
        warnings.append(f"{demo_count} shelters use approximate unverified demo coordinates and capacities; not for real emergency operations.")
    for label, gdf in [("shelters", shelters), ("demand", demand)]:
        if gdf.crs is None:
            errors.append(f"{label}: CRS missing")
        elif gdf.crs.to_epsg() != 4326:
            errors.append(f"{label}: expected EPSG:4326, got {gdf.crs}")
        if gdf.geometry.isna().any() or gdf.geometry.is_empty.any():
            errors.append(f"{label}: null/empty geometry exists")

    if shelters["shelter_id"].duplicated().any():
        errors.append("shelters: duplicate shelter_id")
    if demand["demand_id"].duplicated().any():
        errors.append("demand: duplicate demand_id")
    capacity = pd.to_numeric(shelters["capacity_persons"], errors="coerce")
    if (capacity.isna() | (capacity <= 0)).any():
        errors.append("shelters: non-positive capacity")
    if (pd.to_numeric(demand["pop_est"], errors="coerce") < 0).any():
        errors.append("demand: negative population estimate")

    key_cols = ["demand_id", "shelter_id"]
    if matrix.duplicated(key_cols).any():
        errors.append("travel_matrix: duplicate demand_id/shelter_id pair")
    if set(matrix["demand_id"]) - set(demand["demand_id"].astype(str)):
        errors.append("travel_matrix: unknown demand_id")
    if set(matrix["shelter_id"]) - set(shelters["shelter_id"].astype(str)):
        errors.append("travel_matrix: unknown shelter_id")

    reachable = matrix["reachable"].astype(bool)
    if matrix.loc[reachable, ["distance_m", "time_min"]].isna().any().any():
        errors.append("travel_matrix: reachable pair has null distance/time")
    if (pd.to_numeric(matrix.loc[reachable, "distance_m"], errors="coerce") < 0).any():
        errors.append("travel_matrix: negative distance")
    if matrix.loc[~reachable, ["distance_m", "time_min"]].notna().any().any():
        warnings.append("Unreachable pairs should have null distance/time; inspect if intentional.")

    expected_pairs = len(demand) * len(shelters)
    if len(matrix) != expected_pairs:
        errors.append(f"travel_matrix rows={len(matrix)}; expected demand*shelter={expected_pairs}")

    report = resolve("docs/data_quality_pilot.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    text = [
        "# Pilot GIS data validation",
        "",
        f"- Data version: `{cfg['project']['data_version']}`",
        f"- Shelters included: `{len(shelters)}`; unverified demo shelters: `{demo_count}`",
        f"- Positive-population demand nodes: `{len(demand)}`",
        f"- Travel matrix rows: `{len(matrix)}`",
        f"- Reachable pairs: `{int(reachable.sum())}`",
        f"- Expected matrix rows: `{expected_pairs}`",
        "",
        "## Errors",
        *([f"- {x}" for x in errors] if errors else ["- None"]),
        "",
        "## Warnings",
        *([f"- {x}" for x in warnings] if warnings else ["- None"]),
        ""
    ]
    report.write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text))
    if errors:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
