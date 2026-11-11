import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from rasterio.features import geometry_mask
from rasterio.transform import xy
from shapely.geometry import Point, mapping
from common import load_config, resolve, read_analysis_area

def main():
    cfg = load_config()
    raster_path = resolve(cfg["paths"]["population_raster"])
    if not raster_path.exists():
        raise FileNotFoundError(f"Download WorldPop GeoTIFF first: {raster_path}")
    _, area = read_analysis_area(cfg)

    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise ValueError("Population raster CRS is missing.")
        source_crs = str(src.crs)
        area_gdf = gpd.GeoDataFrame(geometry=[area], crs="EPSG:4326").to_crs(src.crs)
        geom = area_gdf.geometry.iloc[0]
        nodata = src.nodata if src.nodata is not None else -99999
        clipped, transform = mask(
            src, [mapping(geom)], crop=True, filled=True, nodata=nodata,
            all_touched=False
        )
        meta = src.meta.copy()
        meta.update({
            "height": clipped.shape[1],
            "width": clipped.shape[2],
            "transform": transform,
            "nodata": nodata,
            "compress": "lzw"
        })
        clip_path = resolve(cfg["paths"]["population_clip"])
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(clip_path, "w", **meta) as dst:
            dst.write(clipped)

        arr = clipped[0].astype("float64")
        valid = np.isfinite(arr) & (arr != nodata) & (arr >= 0)
        positive = valid & (arr > 0)
        rows, cols = np.where(positive)
        xs, ys = xy(transform, rows, cols, offset="center")
        pop_values = arr[rows, cols]

        points = gpd.GeoDataFrame(
            {
                "demand_id": [f"d_{i:07d}" for i in range(len(rows))],
                "row": rows.astype(int),
                "col": cols.astype(int),
                "pop_est": pop_values.astype(float),
                "population_year": int(cfg["project"]["population_year"]),
                "population_source": "WorldPop Global2 R2025A v1",
                "population_status": "modelled_estimate"
            },
            geometry=[Point(x, y) for x, y in zip(xs, ys)],
            crs=src.crs
        ).to_crs("EPSG:4326")

    demand_path = resolve(cfg["paths"]["demand_nodes"])
    demand_path.parent.mkdir(parents=True, exist_ok=True)
    points.to_file(demand_path, driver="GeoJSON", index=False)

    report = resolve("docs/data_quality_population.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "# Population raster quality report\n\n"
        f"- Source: `{raster_path.name}`\n"
        f"- Source CRS: `{source_crs}`\n"
        f"- NoData: `{nodata}`\n"
        f"- Valid cells (including zero): `{int(valid.sum())}`\n"
        f"- Positive-population cells exported: `{len(points)}`\n"
        f"- Estimated population sum in positive cells: `{float(pop_values.sum()):.3f}`\n"
        f"- Output raster: `{clip_path}`\n"
        f"- Output demand nodes: `{demand_path}`\n\n"
        "Values are WorldPop modelled estimates per grid cell, not observed evacuees. "
        "The scenario demand ratio is applied later by the backend.\n",
        encoding="utf-8"
    )
    print(f"Clipped raster: {clip_path}")
    print(f"Demand nodes: {demand_path} ({len(points)} positive cells)")
    print(f"Estimated population sum: {float(pop_values.sum()):.3f}")

if __name__ == "__main__":
    main()
