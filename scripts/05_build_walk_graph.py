import networkx as nx
import geopandas as gpd
import json
from datetime import datetime, timezone
from importlib.metadata import version
import osmnx as ox
from common import load_config, resolve, read_analysis_area, sha256_file

def main():
    cfg = load_config()
    _, area_wgs84 = read_analysis_area(cfg)
    buffer_m = float(cfg["network"]["network_buffer_m"])

    area_gdf = gpd.GeoDataFrame(geometry=[area_wgs84], crs="EPSG:4326")
    metric_crs = area_gdf.estimate_utm_crs()
    if metric_crs is None:
        raise RuntimeError("Could not estimate a local metric CRS for the pilot area.")
    area_metric = area_gdf.to_crs(metric_crs).geometry.iloc[0]
    buffered_metric = area_metric.buffer(buffer_m)
    buffered_wgs84 = gpd.GeoSeries([buffered_metric], crs=metric_crs).to_crs("EPSG:4326").iloc[0]

    cache_dir = resolve("data/interim/osmnx_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir)
    ox.settings.timeout = 180

    retrieved_at_utc = datetime.now(timezone.utc).isoformat()
    polygon_path = resolve(cfg["paths"]["analysis_polygon"])
    print("Requesting OSM walking network for the buffered pilot polygon...")
    G = ox.graph_from_polygon(
        buffered_wgs84,
        network_type=cfg["network"].get("network_type", "walk"),
        simplify=True,
        retain_all=True,
        truncate_by_edge=True
    )
    if len(G.nodes) == 0 or len(G.edges) == 0:
        raise RuntimeError("OSMnx returned an empty graph. Check polygon/network access.")

    Gp = ox.project_graph(G, to_crs=metric_crs)
    graph_path = resolve(cfg["paths"]["road_graph"])
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(Gp, filepath=graph_path)
    graph_metadata = {
        "retrieved_at_utc": retrieved_at_utc,
        "source": "OpenStreetMap contributors via OSMnx graph_from_polygon",
        "osm_copyright_url": "https://www.openstreetmap.org/copyright",
        "network_type": cfg["network"].get("network_type", "walk"),
        "network_buffer_m": buffer_m,
        "analysis_polygon_sha256": sha256_file(polygon_path),
        "osmnx_version": version("osmnx"),
        "graph_nodes": int(len(G.nodes)),
        "graph_edges_directed": int(len(G.edges)),
        "projected_crs": str(Gp.graph.get("crs")),
        "graphml_sha256": sha256_file(graph_path),
        "snapshot_note": "Live query result; graph hash pins this retrieved artifact, but the upstream OSM database is not a fixed snapshot.",
    }
    metadata_path = resolve("data/processed/osm_graph_metadata.json")
    metadata_path.write_text(json.dumps(graph_metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True, fill_edge_geometry=True)
    layers = resolve("data/processed/map_layers")
    layers.mkdir(parents=True, exist_ok=True)
    edges.to_crs("EPSG:4326").to_file(
        layers / "road_edges.geojson", driver="GeoJSON", index=False
    )
    gpd.GeoDataFrame(
        {"label": [cfg["project"]["area_label"]]},
        geometry=[buffered_wgs84], crs="EPSG:4326"
    ).to_file(layers / "network_buffer.geojson", driver="GeoJSON", index=False)

    undirected = G.to_undirected()
    components = list(nx.connected_components(undirected))
    largest = max((len(c) for c in components), default=0)
    report = resolve("docs/data_quality_roads.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "# Walking network quality report\n\n"
        f"- OSMnx network type: `{cfg['network'].get('network_type', 'walk')}`\n"
        f"- Nodes: `{len(G.nodes)}`\n"
        f"- Directed edges: `{len(G.edges)}`\n"
        f"- Undirected connected components: `{len(components)}`\n"
        f"- Largest component nodes: `{largest}`\n"
        f"- Largest component share: `{largest / max(len(G.nodes), 1):.4f}`\n"
        f"- Network buffer: `{buffer_m} m` (analysis setting)\n"
        f"- Projected CRS: `{metric_crs}`\n"
        f"- GraphML: `{graph_path}`\n"
        f"- OSM query UTC: `{retrieved_at_utc}`\n"
        f"- Graph SHA256: `{graph_metadata['graphml_sha256']}`\n"
        f"- Road layer: `{layers / 'road_edges.geojson'}`\n\n"
        "OSM road completeness, pedestrian access restrictions, temporary closures and disaster damage "
        "must be checked separately. This is not a live emergency routing graph.\n",
        encoding="utf-8"
    )
    print(f"Saved projected walking graph: {graph_path}")
    print(f"Saved OSM retrieval metadata: {metadata_path}")
    print(f"Saved road map layer: {layers / 'road_edges.geojson'}")

if __name__ == "__main__":
    main()
