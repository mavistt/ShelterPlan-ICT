"""Calculate snap-distance and walk-time sensitivity without changing the base matrix."""
from datetime import datetime, timezone

import geopandas as gpd
import networkx as nx
import pandas as pd
import osmnx as ox

from common import load_config, resolve

SNAP_THRESHOLDS_M = (200, 300, 400, 500, 700)
TIME_THRESHOLDS_MIN = (10, 15, 20, 30)


def node_key(value, graph):
    if value in graph:
        return value
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        return value
    return as_int if as_int in graph else value


def main():
    cfg = load_config()
    demand = gpd.read_file(resolve(cfg["paths"]["demand_nodes"]))
    shelters = gpd.read_file(resolve(cfg["paths"]["shelters_geojson"]))
    graph = ox.load_graphml(filepath=resolve(cfg["paths"]["road_graph"])).to_undirected()
    d_audit = pd.read_csv(resolve("data/processed/demand_snap_audit.csv"), dtype={"demand_id": str})
    s_audit = pd.read_csv(resolve("data/processed/shelter_snap_audit.csv"), dtype={"shelter_id": str})

    d_audit["network_node"] = d_audit["network_node"].map(lambda n: node_key(n, graph))
    s_audit["network_node"] = s_audit["network_node"].map(lambda n: node_key(n, graph))
    d_by_id = d_audit.set_index("demand_id")
    s_by_id = s_audit.set_index("shelter_id")
    pop = demand.set_index(demand["demand_id"].astype(str))["pop_est"].astype(float)

    # Recompute candidate distances for every snapped point, including points
    # currently beyond the configured 200 m gate. Their straight-line access
    # legs are sensitivity assumptions, never silently admitted to the base run.
    length_maps = {}
    for sid, snap in s_by_id.iterrows():
        source = snap["network_node"]
        if source in graph:
            length_maps[sid] = nx.single_source_dijkstra_path_length(graph, source=source, weight="length")
        else:
            length_maps[sid] = {}

    rows = []
    for did, ds in d_by_id.iterrows():
        dnode = ds["network_node"]
        d_snap = float(ds["snap_distance_m"])
        for sid, ss in s_by_id.iterrows():
            network_m = length_maps[sid].get(dnode)
            if network_m is None:
                continue
            access_m = d_snap + float(ss["snap_distance_m"])
            distance_m = float(network_m) + access_m
            time_min = distance_m / float(cfg["network"]["walking_speed_m_per_s"]) / 60.0
            rows.append({
                "demand_id": str(did), "shelter_id": str(sid),
                "demand_snap_m": d_snap, "shelter_snap_m": float(ss["snap_distance_m"]),
                "network_distance_m": float(network_m), "access_distance_m": access_m,
                "distance_m": distance_m, "time_min": time_min,
            })
    candidates = pd.DataFrame(rows)
    if candidates.empty:
        raise RuntimeError("No routable demand/shelter candidate pairs were found.")

    # Reconcile the current base-snap route set and distances against A6 before
    # publishing parameter sensitivity. This catches ID/CRS/graph-version drift.
    base_snap = float(cfg["network"]["max_snap_distance_m"])
    base_candidates = candidates[
        (candidates["demand_snap_m"] <= base_snap)
        & (candidates["shelter_snap_m"] <= base_snap)
    ].copy()
    base_matrix = pd.read_parquet(resolve(cfg["paths"]["travel_matrix"]))
    matrix_routes = base_matrix[base_matrix["reachable"].astype(bool)].copy()
    # Normalize keys before comparison and merge; readers can infer IDs as different dtypes.
    base_candidates["demand_id"] = base_candidates["demand_id"].astype(str)
    base_candidates["shelter_id"] = base_candidates["shelter_id"].astype(str)
    matrix_routes["demand_id"] = matrix_routes["demand_id"].astype(str)
    matrix_routes["shelter_id"] = matrix_routes["shelter_id"].astype(str)
    candidate_keys = set(zip(base_candidates["demand_id"], base_candidates["shelter_id"]))
    matrix_keys = set(zip(matrix_routes["demand_id"], matrix_routes["shelter_id"]))
    if candidate_keys != matrix_keys:
        raise RuntimeError(
            f"Sensitivity/base matrix route-key mismatch: candidate={len(candidate_keys)}, matrix={len(matrix_keys)}."
        )
    aligned = base_candidates.merge(
        matrix_routes[["demand_id", "shelter_id", "distance_m", "time_min"]],
        on=["demand_id", "shelter_id"], how="inner", suffixes=("_sensitivity", "_base")
    )
    max_distance_delta = float((aligned["distance_m_sensitivity"] - aligned["distance_m_base"]).abs().max())
    max_time_delta = float((aligned["time_min_sensitivity"] - aligned["time_min_base"]).abs().max())
    if max_distance_delta > 0.02 or max_time_delta > 1e-4:
        raise RuntimeError(
            f"Sensitivity/base matrix arithmetic mismatch: max distance delta={max_distance_delta:.6f} m, "
            f"max time delta={max_time_delta:.8f} min."
        )

    total_population = float(pop.sum())
    results = []
    for snap_limit in SNAP_THRESHOLDS_M:
        within_snap = candidates[
            (candidates["demand_snap_m"] <= snap_limit)
            & (candidates["shelter_snap_m"] <= snap_limit)
        ]
        for time_limit in TIME_THRESHOLDS_MIN:
            eligible = within_snap[within_snap["time_min"] <= time_limit]
            served_ids = set(eligible["demand_id"])
            pop_with_candidate = float(pop.reindex(sorted(served_ids)).fillna(0).sum())
            results.append({
                "snap_limit_m": snap_limit,
                "walk_time_limit_min": time_limit,
                "base_matrix_reconciled": True,
                "base_matrix_reachable_pairs": int(len(matrix_routes)),
                "base_distance_delta_max_m": max_distance_delta,
                "base_time_delta_max_min": max_time_delta,
                "routable_pairs_within_both_limits": int(len(eligible)),
                "demand_nodes_with_at_least_one_pair": int(len(served_ids)),
                "demand_nodes_total": int(len(demand)),
                "population_with_at_least_one_candidate": pop_with_candidate,
                "population_total_estimate": total_population,
                "population_candidate_share": pop_with_candidate / total_population if total_population else 0,
                "interpretation": "network candidate reachability only; no facility-capacity assignment",
            })
    table = pd.DataFrame(results)
    out = resolve("data/processed/snap_time_sensitivity.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out, index=False)

    lines = [
        "# Snap distance and walk-time sensitivity", "",
        f"- Run UTC: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Base snap limit: `{cfg['network']['max_snap_distance_m']} m`",
        f"- Base time scenario: `{cfg['scenario_defaults']['max_walk_time_min']} min`",
        f"- Candidate routable pairs evaluated: `{len(candidates)}`",
        f"- Base snap route keys reconciled with matrix: `{len(matrix_routes)}`; max distance/time deltas `{max_distance_delta:.6f} m` / `{max_time_delta:.8f} min`",
        f"- Demand population estimate in analysis area: `{total_population:.3f}`", "",
        "| Snap limit (m) | Time limit (min) | Routable pairs | Demand nodes with a candidate | Population with a candidate | Population share |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        lines.append(
            f"| {row['snap_limit_m']} | {row['walk_time_limit_min']} | "
            f"{row['routable_pairs_within_both_limits']} | {row['demand_nodes_with_at_least_one_pair']} | "
            f"{row['population_with_at_least_one_candidate']:.1f} | {row['population_candidate_share']:.1%} |"
        )
    base_15 = next(r for r in results if r["snap_limit_m"] == base_snap and r["walk_time_limit_min"] == cfg["scenario_defaults"]["max_walk_time_min"])
    expanded_15 = next(r for r in results if r["snap_limit_m"] == 300 and r["walk_time_limit_min"] == cfg["scenario_defaults"]["max_walk_time_min"])
    base_30 = next(r for r in results if r["snap_limit_m"] == base_snap and r["walk_time_limit_min"] == 30)
    delta_share_pp = (expanded_15["population_candidate_share"] - base_15["population_candidate_share"]) * 100
    lines.extend([
        "", "## Interpretation", "",
        f"- Keep the configured `{base_snap} m` snap limit: expanding to `300 m` at the default time threshold adds only `{expanded_15['demand_nodes_with_at_least_one_pair'] - base_15['demand_nodes_with_at_least_one_pair']}` demand cells and `{delta_share_pp:.2f}` percentage points of candidate population. A longer connector may cross a barrier, so this small modeled gain does not justify changing the base limit.",
        f"- Time threshold has a much larger effect: at `{base_snap} m`, changing from the default `{base_15['walk_time_limit_min']} min` to `30 min` raises population with at least one candidate from `{base_15['population_candidate_share']:.1%}` to `{base_30['population_candidate_share']:.1%}`.",
        "- These are candidate reachability figures. They do not model capacity competition or confirm that people can safely access a road or shelter.",
        "", "These are sensitivity scenarios, not evidence that a longer off-network connector is safe or walkable. "
        "A candidate means at least one network path exists and meets the selected time limit; it does not account for facility capacity, allocation competition, barriers between a population-cell centroid and the nearest road, or shelter verification. "
        "The 200 m configured base result remains unchanged.",
    ])
    report = resolve("docs/snap_time_sensitivity.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"CSV: {out}")
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
