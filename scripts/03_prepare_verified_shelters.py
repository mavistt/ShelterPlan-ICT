import pandas as pd
import argparse
import json
import math
from common import load_config, resolve

def approximate_wgs84(lon, lat):
    """Approximate inverse for demo use; not an authoritative GPS measurement."""
    x, y = lon - 105.0, lat - 35.0
    a, ee = 6378245.0, 0.00669342162296594323
    pi = math.pi
    dlat = -100.0 + 2*x + 3*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
    dlat += (20*math.sin(6*x*pi) + 20*math.sin(2*x*pi))*2/3
    dlat += (20*math.sin(y*pi) + 40*math.sin(y*pi/3))*2/3
    dlat += (160*math.sin(y*pi/12) + 320*math.sin(y*pi/30))*2/3
    dlon = 300.0 + x + 2*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    dlon += (20*math.sin(6*x*pi) + 20*math.sin(2*x*pi))*2/3
    dlon += (20*math.sin(x*pi) + 40*math.sin(x*pi/3))*2/3
    dlon += (150*math.sin(x*pi/12) + 300*math.sin(x*pi/30))*2/3
    rad = lat*pi/180.0
    magic = 1-ee*math.sin(rad)**2
    root = math.sqrt(magic)
    dlat = dlat*180 / ((a*(1-ee))/(magic*root)*pi)
    dlon = dlon*180 / (a/root*math.cos(rad)*pi)
    return lon-dlon, lat-dlat

def prepare_demo(cfg):
    path = resolve(cfg["paths"]["geocode_candidates"])
    df = pd.read_csv(path, dtype={"shelter_id": str})
    for c in ("lon_gcj02", "lat_gcj02", "capacity_persons"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    ok = (df["geocode_status"].eq("candidate_needs_manual_review")
          & df.lon_gcj02.between(113.30, 113.50) & df.lat_gcj02.between(22.43, 22.57)
          & df.capacity_persons.gt(0))
    selected = df.loc[ok].copy()
    if selected.empty:
        raise RuntimeError("No valid demonstration geocodes available.")
    coordinates = [approximate_wgs84(float(x),float(y)) for x,y in zip(selected.lon_gcj02, selected.lat_gcj02)]
    selected["lon_wgs84"] = [x for x,y in coordinates]
    selected["lat_wgs84"] = [y for x,y in coordinates]
    selected["coordinate_status"] = "approximate_demo_unverified"
    selected["coordinate_method"] = "approximate_gcj02_inverse_not_official"
    selected["verified_status"] = "demo_unverified"
    selected["admission_status"] = "included_for_demo_only"
    selected["admission_reason"] = "candidate_geocode_approximate_unverified"
    out = resolve(cfg["paths"]["shelters_geojson"])
    out.parent.mkdir(parents=True, exist_ok=True)
    selected = selected.where(pd.notna(selected), None)
    features=[]
    for _,row in selected.iterrows():
        props={k:(v.item() if hasattr(v,"item") else v) for k,v in row.items()}
        features.append({"type":"Feature","geometry":{"type":"Point","coordinates":[float(row.lon_wgs84),float(row.lat_wgs84)]},"properties":props})
    out.write_text(json.dumps({"type":"FeatureCollection","name":"demo_shelters_approximate_not_verified","features":features},ensure_ascii=False,allow_nan=False),encoding="utf-8")
    area_path=resolve(cfg["paths"]["analysis_polygon"])
    area=json.loads(area_path.read_text(encoding="utf-8")) if area_path.exists() else {"features": []}
    if not area.get("features"):
        lon=[p[0] for p in coordinates]; lat=[p[1] for p in coordinates]; margin=0.01
        xmin,xmax=min(lon)-margin,max(lon)+margin; ymin,ymax=min(lat)-margin,max(lat)+margin
        ring=[[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax],[xmin,ymin]]
        area={"type":"FeatureCollection","name":"demo_analysis_area_not_administrative",
              "features":[{"type":"Feature","properties":{"label":"试点分析范围：场所候选点外包矩形加约1公里缓冲，非行政边界","status":"demo_approximate"},"geometry":{"type":"Polygon","coordinates":[ring]}}]}
        area_path.write_text(json.dumps(area,ensure_ascii=False,indent=2),encoding="utf-8")
        print(f"Generated provisional analysis area: {area_path}")
    print(f"Saved {len(features)} approximate, unverified demonstration points: {out}")
    print("Not for emergency use. Original GCJ-02 and conversion method are retained in properties.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Use approximate unverified positions for a demo")
    args = parser.parse_args()
    cfg = load_config()
    if args.demo:
        prepare_demo(cfg)
        return
    import geopandas as gpd
    from shapely.geometry import Point
    candidates_path = resolve(cfg["paths"]["geocode_candidates"])
    review_path = resolve(cfg["paths"]["wgs84_review"])
    if not candidates_path.exists() or not review_path.exists():
        raise FileNotFoundError("Run scripts/02_geocode_amap.py first.")

    candidates = pd.read_csv(candidates_path, dtype={"shelter_id": str})
    review = pd.read_csv(review_path, dtype={"shelter_id": str})
    merged = candidates.merge(
        review[["shelter_id", "lon_wgs84", "lat_wgs84", "verified_status", "verification_note"]],
        on="shelter_id", how="left", validate="one_to_one"
    )
    merged["lon_wgs84"] = pd.to_numeric(merged["lon_wgs84"], errors="coerce")
    merged["lat_wgs84"] = pd.to_numeric(merged["lat_wgs84"], errors="coerce")
    merged["capacity_persons"] = pd.to_numeric(merged["capacity_persons"], errors="coerce")

    coord_ok = (
        merged["lon_wgs84"].between(-180, 180) &
        merged["lat_wgs84"].between(-90, 90)
    )
    capacity_ok = merged["capacity_persons"].notna() & (merged["capacity_persons"] > 0)
    verified = merged["verified_status"].fillna("needs_review").eq("verified")
    merged["admission_status"] = "excluded"
    merged.loc[~verified, "admission_reason"] = "not_verified"
    merged.loc[verified & ~coord_ok, "admission_reason"] = "invalid_or_missing_wgs84"
    merged.loc[verified & coord_ok & ~capacity_ok, "admission_reason"] = "invalid_or_missing_capacity"
    keep = verified & coord_ok & capacity_ok
    merged.loc[keep, "admission_status"] = "included"
    merged.loc[keep, "admission_reason"] = "verified_wgs84_and_positive_capacity"

    audit_path = resolve("data/processed/geocoding_audit.csv")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(audit_path, index=False, encoding="utf-8-sig")

    selected = merged.loc[keep].copy()
    if selected.empty:
        raise RuntimeError(
            "No shelters passed verification. Fill WGS84 coordinates and set verified_status=verified "
            "in the review CSV. Do not copy GCJ-02 values into WGS84 fields."
        )

    geometry = [Point(xy) for xy in zip(selected["lon_wgs84"], selected["lat_wgs84"])]
    gdf = gpd.GeoDataFrame(selected, geometry=geometry, crs="EPSG:4326")
    output = resolve(cfg["paths"]["shelters_geojson"])
    output.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(output, driver="GeoJSON", index=False)

    print(f"Included {len(gdf)} verified shelters: {output}")
    print(f"Full audit (including rejected/unverified): {audit_path}")

if __name__ == "__main__":
    main()
