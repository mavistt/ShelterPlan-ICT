from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required to run the ShelterPlan-Eval preflight") from exc


ROOT = Path(__file__).resolve().parents[1]
REGION_IDS = ("shiqi", "east_district", "xiaolan")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_lfs_pointer(path: Path) -> bool:
    try:
        prefix = path.read_bytes()[:256]
    except OSError:
        return False
    return prefix.startswith(b"version https://git-lfs.github.com/spec/v1")


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_regions() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "configs/regions.yaml").read_text(encoding="utf-8"))


def unique_paths(paths: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        normalized = raw.replace("\\", "/")
        if normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def source_paths() -> list[str]:
    paths = [
        "configs/regions.yaml",
        "data/processed/shelters_clean.csv",
        "data/raw/zhongshan_shelters.xls",
        "data/raw/chn_pop_2025_CN_100m_R2025A_v1.tif",
        "data/raw/guangdong-latest.osm.pbf",
        "data/interim/regions/source_disposition.csv",
        "data/interim/regions/duplicate_candidates_all.csv",
    ]
    for region_id in REGION_IDS:
        region_root = f"data/processed/regions/{region_id}"
        paths.extend(
            [
                f"configs/module_a_v2_b/{region_id}.yaml",
                f"{region_root}/manifest.json",
                f"{region_root}/quality_summary.json",
                f"{region_root}/boundary_metadata.json",
                f"{region_root}/layers_manifest.json",
                f"{region_root}/shelters_all.csv",
                f"{region_root}/shelter_snap_audit.csv",
                f"{region_root}/demand_snap_audit.csv",
                f"{region_root}/demand_nodes.parquet",
                f"{region_root}/travel_matrix.parquet",
                f"data/interim/regions/{region_id}/shelter_geocode_audit.csv",
                f"data/interim/regions/{region_id}/shelter_review.xlsx",
            ]
        )
    return unique_paths(paths)


def describe_file(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    result: dict[str, Any] = {
        "path": relative_path,
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "sha256": sha256_file(path) if path.is_file() else None,
        "lfs_pointer": is_lfs_pointer(path) if path.is_file() else False,
    }
    return result


def region_check(region_id: str, regions: dict[str, Any]) -> dict[str, Any]:
    region_cfg = regions.get("regions", {}).get(region_id, {})
    region_root = ROOT / "data/processed/regions" / region_id
    quality_path = region_root / "quality_summary.json"
    manifest_path = region_root / "manifest.json"
    adapter_path = ROOT / "configs/module_a_v2_b" / f"{region_id}.yaml"
    quality = read_json(quality_path) if quality_path.is_file() else {}
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    adapter = yaml.safe_load(adapter_path.read_text(encoding="utf-8")) if adapter_path.is_file() else {}
    shelter_csv = region_root / "shelters_all.csv"
    shelter_rows = list(csv.DictReader(shelter_csv.open(encoding="utf-8-sig", newline=""))) if shelter_csv.is_file() else []
    od = quality.get("travel_matrix", {})
    return {
        "region_id": region_id,
        "enabled": bool(region_cfg.get("enabled", True)),
        "role": region_cfg.get("role"),
        "data_version": adapter.get("project", {}).get("data_version"),
        "quality_data_version": quality.get("data_version"),
        "manifest_data_version": manifest.get("data_version"),
        "release_status": quality.get("release_status"),
        "data_status": quality.get("data_status"),
        "source_rows": len(shelter_rows),
        "quality_source_rows": quality.get("source_rows"),
        "spatial_shelters": quality.get("spatial_shelters"),
        "od_rows": od.get("rows"),
        "od_expected_rows": od.get("expected_rows"),
        "data_version_matches": len(
            {
                adapter.get("project", {}).get("data_version"),
                quality.get("data_version"),
                manifest.get("data_version"),
            }
        )
        == 1,
        "shelter_rows_match_quality": len(shelter_rows) == quality.get("source_rows"),
        "od_rows_match_expected": od.get("rows") == od.get("expected_rows"),
        "boundary_status": (
            read_json(region_root / "boundary_metadata.json").get("boundary_status")
            if (region_root / "boundary_metadata.json").is_file()
            else None
        ),
        "manifest_file_count": len(manifest.get("files", {})),
    }


def build_manifest() -> dict[str, Any]:
    regions = load_regions()
    files = [describe_file(path) for path in source_paths()]
    missing = [entry["path"] for entry in files if not entry["exists"]]
    lfs_pointers = [entry["path"] for entry in files if entry["lfs_pointer"]]
    checks = [region_check(region_id, regions) for region_id in REGION_IDS]
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Stage 0 read-only ShelterPlan-Eval source baseline",
        "git": {
            "branch": git("branch", "--show-current"),
            "commit": git("rev-parse", "HEAD"),
            "integration_base": "14d686aa0043dad513eb9c165b873f04a6b5e80f",
            "historical_reference": git("rev-parse", "origin/feature/benchmark-grounded"),
        },
        "policy": {
            "old_a_v2_data_modified": False,
            "historical_merge_data_merged": False,
            "lfs_objects_read": len(lfs_pointers) == 0,
            "lfs_pointer_files": lfs_pointers,
            "missing_files": missing,
        },
        "regions": checks,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a read-only A v2 source manifest")
    parser.add_argument("--output", default="sources/source_manifest.json")
    args = parser.parse_args()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
