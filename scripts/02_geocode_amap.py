import json
import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv
from common import load_config, resolve

API_URL = "https://restapi.amap.com/v3/geocode/geo"

def main():
    load_dotenv(resolve(".env"))
    key = os.getenv("AMAP_KEY", "").strip()
    if not key or key == "replace_with_your_web_service_key":
        raise RuntimeError("Set AMAP_KEY in the local .env file. Do not commit the key.")

    cfg = load_config()
    town = str(cfg["project"].get("pilot_town", "")).strip()
    if not town:
        raise ValueError("Set project.pilot_town in configs/pilot.yaml to the exact official town name.")

    cleaned_path = resolve(cfg["paths"]["shelters_pilot_raw"])
    if not cleaned_path.exists():
        raise FileNotFoundError("Run scripts/01_prepare_pilot_demo.py first.")
    df = pd.read_csv(cleaned_path, dtype={"shelter_id": str})
    pilot = df[df["town"].astype(str).str.strip() == town].copy()
    if pilot.empty:
        available = sorted(df["town"].dropna().astype(str).unique().tolist())
        raise ValueError(f"No rows match pilot_town='{town}'. Available town values: {available}")

    review_path = resolve(cfg["paths"]["wgs84_review"])
    if review_path.exists():
        print(f"Preserving existing coordinate review file: {review_path}")
    cache_path = resolve("data/interim/amap_geocode_cache.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    rows = []
    for _, row in pilot.iterrows():
        raw_address = row.get("address", "")
        address = "" if pd.isna(raw_address) else str(raw_address).strip()
        item = {
            "shelter_id": row["shelter_id"],
            "town": row["town"],
            "shelter_name": row["shelter_name"],
            "address": address,
            "capacity_persons": row.get("capacity_persons"),
            "source_file": row.get("source_file"),
            "source_row": row.get("source_row"),
            "data_status": row.get("data_status"),
            "data_note": row.get("data_note"),
            "geocode_source": "Amap Web Service",
            "coordinate_system": "GCJ-02",
            "geocode_status": "missing_address",
            "lon_gcj02": None,
            "lat_gcj02": None,
            "geocode_level": None,
            "formatted_address": None,
            "adcode": None,
            "geocode_count": 0
        }
        if not address:
            rows.append(item)
            continue

        cache_key = f"{cfg['geocoding']['city']}|{address}"
        if cache_key not in cache:
            params = {
                "key": key,
                "address": address,
                "city": cfg["geocoding"]["city"],
                "output": "JSON"
            }
            try:
                response = requests.get(API_URL, params=params, timeout=20)
                if not response.ok:
                    raise RuntimeError(f"Amap HTTP error {response.status_code}; check service access.")
            except requests.RequestException:
                raise RuntimeError("Could not contact Amap; check network access and retry.") from None
            payload = response.json()
            if payload.get("status") != "1":
                raise RuntimeError(f"Amap API failed: {payload.get('info')} ({payload.get('infocode')}); check key/quota. No failed response cached.")
            cache[cache_key] = payload
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(float(cfg["geocoding"].get("rate_limit_seconds", 0.35)))
        payload = cache[cache_key]

        item["geocode_count"] = int(payload.get("count", 0) or 0)
        if payload.get("status") == "1" and payload.get("geocodes"):
            best = payload["geocodes"][0]
            location = best.get("location", "")
            if "," in location:
                lon, lat = location.split(",", 1)
                item.update({
                    "lon_gcj02": float(lon),
                    "lat_gcj02": float(lat),
                    "geocode_level": best.get("level"),
                    "formatted_address": best.get("formatted_address"),
                    "adcode": best.get("adcode"),
                    "geocode_status": "candidate_needs_manual_review"
                })
        else:
            item["geocode_status"] = "api_no_match_or_error"
            item["api_infocode"] = payload.get("infocode")
            item["api_info"] = payload.get("info")

        rows.append(item)

    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    candidates = pd.DataFrame(rows)
    # Explicit demo exclusions based on review of this source and actual Amap results.
    exclusions = {
        64: "原结果为道路且明显偏离东区，待坐标复核",
        67: "输入万和街1号之二，返回1号之1，门牌不一致",
        82: "输入长景街13号，返回12号，门牌不一致",
        85: "仅定位至悦朗路道路，未到具体楼栋",
        88: "输入东区新安路18号，返回新安路5号且明显偏离东区",
        89: "仅定位至远洋城兴趣点，未到万象花园56卡",
    }
    source_rows = pd.to_numeric(candidates["source_row"], errors="raise").astype(int)
    if not set(exclusions).issubset(set(source_rows)):
        raise ValueError("Expected source rows for documented demo exclusions not found")
    excluded = candidates[source_rows.isin(exclusions)].copy()
    excluded["exclusion_reason"] = source_rows[source_rows.isin(exclusions)].map(exclusions)
    excluded.to_csv(resolve("data/interim/shelters_geocoding_exclusions.csv"), index=False, encoding="utf-8-sig")
    candidates = candidates[~source_rows.isin(exclusions)].copy()
    print(f"Excluded {len(excluded)} documented problematic candidates")
    out = resolve(cfg["paths"]["geocode_candidates"])
    out.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(out, index=False, encoding="utf-8-sig")

    if not review_path.exists():
        review = candidates[["shelter_id", "shelter_name", "address", "lon_gcj02", "lat_gcj02"]].copy()
        review["lon_wgs84"] = ""
        review["lat_wgs84"] = ""
        review["verified_status"] = "needs_review"
        review["verification_note"] = ""
        review.to_csv(review_path, index=False, encoding="utf-8-sig")

    print(f"Saved candidate geocodes: {out}")
    print(f"Matched: {(candidates['geocode_status'] == 'candidate_needs_manual_review').sum()}/{len(candidates)}")
    print(f"Review WGS84 coordinates here: {review_path}")
    print("Important: Amap coordinates are GCJ-02 candidates, not verified WGS84.")

if __name__ == "__main__":
    main()
