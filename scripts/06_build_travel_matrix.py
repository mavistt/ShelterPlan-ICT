import math
import networkx as nx
import numpy as np
import pandas as pd
import geopandas as gpd
import osmnx as ox
from shapely.geometry import LineString
from common import load_config, resolve

def snap_points(G, points_gdf, max_snap_m, id_col):
    graph_crs = G.graph.get("crs")
    if graph_crs is None:
        raise ValueError("GraphML does not contain a CRS.")
    projected = points_gdf.to_crs(graph_crs)
    xs = projected.geometry.x.to_numpy()
    ys = projected.geometry.y.to_numpy()
    nearest, distances = ox.distance.nearest_nodes(
        G, X=xs, Y=ys, return_dist=True
    )
    result = {}
    audit = []
    for i, (_, row) in enumerate(points_gdf.iterrows()):
        node = int(nearest[i]) if isinstance(nearest[i], (np.integer, int)) else nearest[i]
        dist = float(distances[i])
        result[str(row[id_col])] = {
            "node": node,
            "snap_distance_m": dist,
            "snap_ok": dist <= max_snap_m
        }
        audit.append({
            id_col: row[id_col],
            "network_node": node,
            "snap_distance_m": dist,
            "snap_ok": dist <= max_snap_m
        })
    return result, pd.DataFrame(audit)

def main():
    cfg = load_config()
    demand_path = resolve(cfg["paths"]["demand_nodes"])
    shelter_path = resolve(cfg["paths"]["shelters_geojson"])
    graph_path = resolve(cfg["paths"]["road_graph"])
    for path in [demand_path, shelter_path, graph_path]:
        if not path.exists():
            raise FileNotFoundError(f"Missing required input: {path}")

    demand = gpd.read_file(demand_path)
    shelters = gpd.read_file(shelter_path)
    if demand.empty or shelters.empty:
        raise ValueError("Demand nodes or verified shelters are empty.")
    if demand.crs is None or shelters.crs is None:
        raise ValueError("Demand/shelter CRS missing.")
    if "demand_id" not in demand or "shelter_id" not in shelters:
        raise KeyError("Missing demand_id or shelter_id.")

    G = ox.load_graphml(filepath=graph_path)
    # Pedestrian routes are treated as bidirectional for this planning estimate.
    Gu = G.to_undirected()
    max_snap = float(cfg["network"]["max_snap_distance_m"])
    speed = float(cfg["network"]["walking_speed_m_per_s"])
    if speed <= 0:
        raise ValueError("walking_speed_m_per_s must be positive.")

    demand_snap, demand_audit = snap_points(Gu, demand, max_snap, "demand_id")
    shelter_snap, shelter_audit = snap_points(Gu, shelters, max_snap, "shelter_id")
    demand_audit.to_csv(resolve("data/processed/demand_snap_audit.csv"), index=False)
    shelter_audit.to_csv(resolve("data/processed/shelter_snap_audit.csv"), index=False)

    # Run one Dijkstra search per shelter; all demand-shelter pairs are then looked up.
    lengths_by_shelter = {}
    for sid, snap in shelter_snap.items():
        if snap["snap_ok"]:
            lengths_by_shelter[sid] = nx.single_source_dijkstra_path_length(
                Gu, source=snap["node"], weight="length"
            )
        else:
            lengths_by_shelter[sid] = {}

    records = []
    for _, drow in demand.iterrows():
        did = str(drow["demand_id"])
        ds = demand_snap[did]
        for _, srow in shelters.iterrows():
            sid = str(srow["shelter_id"])
            ss = shelter_snap[sid]
            distance = None
            time_min = None
            if not ds["snap_ok"]:
                reason = "demand_snap_too_far"
            elif not ss["snap_ok"]:
                reason = "shelter_snap_too_far"
            else:
                distance = lengths_by_shelter[sid].get(ds["node"])
                if distance is None:
                    reason = "no_path"
                else:
                    network_distance = float(distance)
                    access_distance = ds["snap_distance_m"] + ss["snap_distance_m"]
                    distance = network_distance + access_distance
                    time_min = distance / speed / 60.0
                    reason = "ok"
            records.append({
                "demand_id": did,
                "shelter_id": sid,
                "distance_m": distance,
                "network_distance_m": network_distance if reason == "ok" else None,
                "access_distance_m": access_distance if reason == "ok" else None,
                "time_min": time_min,
                "reachable": reason == "ok",
                "reason": reason,
                "demand_snap_m": ds["snap_distance_m"],
                "shelter_snap_m": ss["snap_distance_m"],
                "walking_speed_m_per_s": speed,
                "data_version": cfg["project"]["data_version"]
            })

    matrix = pd.DataFrame(records)
    matrix_path = resolve(cfg["paths"]["travel_matrix"])
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    matrix.to_parquet(matrix_path, index=False)

    # Export one reachable route for human inspection.
    example = matrix[matrix["reachable"]].head(1)
    if not example.empty:
        row = example.iloc[0]
        did, sid = str(row["demand_id"]), str(row["shelter_id"])
        dnode = demand_snap[did]["node"]
        snode = shelter_snap[sid]["node"]
        path_nodes = nx.shortest_path(Gu, source=dnode, target=snode, weight="length")
        demand_projected = demand.to_crs(Gu.graph["crs"])
        shelters_projected = shelters.to_crs(Gu.graph["crs"])
        dpoint = demand_projected.loc[demand_projected["demand_id"].astype(str) == did, "geometry"].iloc[0]
        spoint = shelters_projected.loc[shelters_projected["shelter_id"].astype(str) == sid, "geometry"].iloc[0]
        coords = [(dpoint.x, dpoint.y)]
        coords.extend((Gu.nodes[n]["x"], Gu.nodes[n]["y"]) for n in path_nodes)
        coords.append((spoint.x, spoint.y))
        route = gpd.GeoDataFrame(
            [{
                "demand_id": did,
                "shelter_id": sid,
                "distance_m": float(row["distance_m"]),
                "time_min": float(row["time_min"]),
                "walking_speed_m_per_s": speed
            }],
            geometry=[LineString(coords)],
            crs=Gu.graph["crs"]
        ).to_crs("EPSG:4326")
        layers = resolve("data/processed/map_layers")
        layers.mkdir(parents=True, exist_ok=True)
        route.to_file(layers / "route_example.geojson", driver="GeoJSON", index=False)

    # Copy canonical point layers for the front-end.
    layers = resolve("data/processed/map_layers")
    layers.mkdir(parents=True, exist_ok=True)
    demand.to_crs("EPSG:4326").to_file(layers / "demand_nodes.geojson", driver="GeoJSON", index=False)
    shelters.to_crs("EPSG:4326").to_file(layers / "shelters.geojson", driver="GeoJSON", index=False)

    print(f"Travel matrix saved: {matrix_path}")
    print(f"Rows: {len(matrix)}; reachable pairs: {int(matrix['reachable'].sum())}")
    if example.empty:
        print("WARNING: no reachable pair found; inspect snap audits, graph connectivity and CRS.")

if __name__ == "__main__":
    main()
