from pathlib import Path
import hashlib
import json
import yaml

ROOT = Path(__file__).absolute().parents[1]

def load_config():
    path = ROOT / "configs" / "pilot.yaml"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def resolve(path_value):
    return ROOT / path_value

def ensure_parent(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

def sha256_file(path, chunk_size=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def read_analysis_area(cfg):
    import geopandas as gpd
    path = resolve(cfg["paths"]["analysis_polygon"])
    if not path.exists():
        raise FileNotFoundError(f"Analysis polygon not found: {path}")
    gdf = gpd.read_file(path)
    if gdf.empty or gdf.geometry.isna().all():
        raise ValueError(
            "Analysis polygon is empty. Draw a WGS84 Polygon/MultiPolygon and save it to "
            f"{path}"
        )
    if gdf.crs is None:
        raise ValueError("Analysis polygon CRS is missing; explicitly set it to EPSG:4326.")
    gdf = gdf.to_crs("EPSG:4326")
    geom = gdf.geometry.union_all()
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"Expected Polygon/MultiPolygon, got {geom.geom_type}")
    return gdf, geom

def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
