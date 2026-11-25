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
TEXT_SUFFIXES = {
    ".csv",
    ".geojson",
    ".graphml",
    ".json",
    ".md",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
DATASET_IDENTITY_VERSION = "dataset-content-v1"
ADMISSION_POLICY_VERSION = "admission-gate-v2"


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


def normalized_text_sha256(path: Path) -> str | None:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def line_ending_counts(raw: bytes) -> dict[str, int | str]:
    """Return byte-level line-ending evidence without changing the source file."""
    crlf = raw.count(b"\r\n")
    cr = raw.count(b"\r")
    lf = raw.count(b"\n")
    lone_cr = cr - crlf
    lone_lf = lf - crlf
    if crlf and not lone_cr and not lone_lf:
        mode = "CRLF"
    elif lone_lf and not crlf and not lone_cr:
        mode = "LF"
    elif lone_cr and not crlf and not lone_lf:
        mode = "CR"
    elif not crlf and not lone_cr and not lone_lf:
        mode = "NONE"
    else:
        mode = "MIXED"
    return {
        "crlf_count": crlf,
        "lf_count": lf,
        "cr_count": cr,
        "lone_lf_count": lone_lf,
        "lone_cr_count": lone_cr,
        "mode": mode,
    }


def reconstructed_crlf_bytes(path: Path) -> bytes | None:
    """Rebuild the declared text artifact using UTF-8 CRLF bytes in memory."""
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return None
    try:
        raw = path.read_bytes()
        has_bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    rebuilt = normalized.encode("utf-8").replace(b"\n", b"\r\n")
    return (b"\xef\xbb\xbf" if has_bom else b"") + rebuilt


def verify_declared_file(relative_path: str, expected: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / relative_path
    expected_bytes = expected.get("bytes")
    expected_sha256 = expected.get("sha256")
    raw = path.read_bytes() if path.is_file() else None
    endings = line_ending_counts(raw) if raw is not None else {
        "crlf_count": None,
        "lf_count": None,
        "cr_count": None,
        "lone_lf_count": None,
        "lone_cr_count": None,
        "mode": None,
    }
    rebuilt = reconstructed_crlf_bytes(path) if path.is_file() else None
    result: dict[str, Any] = {
        "path": relative_path,
        "exists": path.is_file(),
        "expected_bytes": expected_bytes,
        "actual_bytes": path.stat().st_size if path.is_file() else None,
        "expected_sha256": expected_sha256,
        "actual_sha256": sha256_file(path) if path.is_file() else None,
        "normalized_text_sha256": normalized_text_sha256(path) if path.is_file() else None,
        "reconstructed_crlf_bytes": len(rebuilt) if rebuilt is not None else None,
        "reconstructed_crlf_sha256": hashlib.sha256(rebuilt).hexdigest() if rebuilt is not None else None,
        "raw_match": False,
        "normalized_text_match": False,
        "reconstructed_crlf_match": False,
        "allowed_text_normalization": path.suffix.lower() in TEXT_SUFFIXES,
        "lfs_pointer": is_lfs_pointer(path) if path.is_file() else False,
        **endings,
        "status": "MISSING",
    }
    if not result["exists"]:
        return result
    if result["lfs_pointer"]:
        result["status"] = "LFS_POINTER"
        return result
    result["raw_match"] = (
        result["actual_bytes"] == expected_bytes
        and result["actual_sha256"] == expected_sha256
    )
    if result["raw_match"]:
        result["status"] = "MATCH_RAW"
        return result
    result["reconstructed_crlf_match"] = (
        rebuilt is not None
        and len(rebuilt) == expected_bytes
        and result["reconstructed_crlf_sha256"] == expected_sha256
    )
    if result["reconstructed_crlf_match"]:
        result["status"] = "MATCH_RECONSTRUCTED_CRLF"
        return result
    result["normalized_text_match"] = (
        result["allowed_text_normalization"]
        and result["normalized_text_sha256"] == expected_sha256
    )
    result["status"] = "CONTENT_MISMATCH"
    return result


def verify_region_manifest(region_id: str) -> dict[str, Any]:
    manifest_path = ROOT / "data/processed/regions" / region_id / "manifest.json"
    if not manifest_path.is_file():
        return {
            "region_id": region_id,
            "manifest_path": str(manifest_path.relative_to(ROOT)).replace("\\", "/"),
            "manifest_exists": False,
            "manifest_sha256": None,
            "manifest_region_id": None,
            "manifest_data_version": None,
            "file_checks": [],
            "failure_count": 1,
            "raw_match_count": 0,
            "normalized_text_match_count": 0,
            "all_declared_files_match": False,
        }
    manifest = read_json(manifest_path)
    checks: list[dict[str, Any]] = []
    for relative_path, expected in manifest.get("files", {}).items():
        checks.append(verify_declared_file(relative_path, expected))
    adapter = manifest.get("b_adapter", {})
    if adapter.get("path"):
        checks.append(verify_declared_file(adapter["path"], adapter))
    raw_mismatches = [item for item in checks if item["status"] != "MATCH_RAW"]
    content_failures = [
        item for item in checks
        if item["status"] not in {"MATCH_RAW", "MATCH_RECONSTRUCTED_CRLF"}
    ]
    return {
        "region_id": region_id,
        "manifest_path": str(manifest_path.relative_to(ROOT)).replace("\\", "/"),
        "manifest_exists": True,
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_region_id": manifest.get("region_id"),
        "manifest_data_version": manifest.get("data_version"),
        "file_checks": checks,
        # Keep failure_count as the strict raw-byte count for backwards-compatible
        # reports.  content_failure_count is the data-integrity count.
        "failure_count": len(raw_mismatches),
        "raw_mismatch_count": len(raw_mismatches),
        "content_failure_count": len(content_failures),
        "raw_match_count": sum(item["status"] == "MATCH_RAW" for item in checks),
        "reconstructed_crlf_match_count": sum(
            item["status"] == "MATCH_RECONSTRUCTED_CRLF" for item in checks
        ),
        "normalized_text_match_count": sum(
            bool(item.get("normalized_text_match")) for item in checks
        ),
        "all_declared_files_match": not raw_mismatches,
        "all_content_match": not content_failures,
    }


def write_manifest_blocking_report(manifest: dict[str, Any]) -> Path:
    raw_mismatches: list[dict[str, Any]] = []
    content_failures: list[dict[str, Any]] = []
    for region in manifest.get("a_v2_manifests", []):
        rows = [
            {"region_id": region["region_id"], **item}
            for item in region.get("file_checks", [])
            if item["status"] != "MATCH_RAW"
        ]
        raw_mismatches.extend(rows)
        content_failures.extend(
            item for item in rows if item["status"] not in {"MATCH_RECONSTRUCTED_CRLF"}
        )
    report_path = ROOT / "reports/preflight_blocking_report.md"
    lines = [
        "# A v2 Manifest Blocking Report",
        "",
        "本报告由 sources.preflight 自动生成。它只记录校验结果，不修改 A v2 Manifest 或源数据。",
        "",
        f"- raw 字节不匹配文件数：{len(raw_mismatches)}",
        f"- CRLF 内存重建后完全吻合：{sum(item['status'] == 'MATCH_RECONSTRUCTED_CRLF' for item in raw_mismatches)}",
        f"- 真实内容不一致/缺失/LFS 文件数：{len(content_failures)}",
        "- 结论：当前 A7/发布准入仍不能标记为 VERIFIED；但 CRLF 重建吻合项有证据表明是传输格式差异，不应误报为内容损坏。",
        "- 文本只允许 UTF-8 BOM/换行重建证据；Parquet、TIFF、PBF、XLSX 等二进制文件继续要求 raw bytes 严格匹配。",
        "",
        "| region_id | path | status | line endings | CRLF count | LF count | expected bytes | actual bytes | reconstructed CRLF bytes | expected SHA-256 | actual SHA-256 | reconstructed CRLF SHA-256 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for item in raw_mismatches:
        lines.append(
            "| {region_id} | {path} | {status} | {mode} | {crlf_count} | {lf_count} | {expected_bytes} | {actual_bytes} | {reconstructed_crlf_bytes} | {expected_sha256} | {actual_sha256} | {reconstructed_crlf_sha256} |".format(
                **item
            )
        )
    if not raw_mismatches:
        lines.extend(["", "没有失败文件；逐文件校验通过。"])
    lines.extend(
        [
            "",
            "根因判断：MATCH_RECONSTRUCTED_CRLF 表示把当前 UTF-8 文本在内存中重建为 CRLF 后，bytes 和 SHA-256 与 Manifest 完全一致。该证据支持 CRLF/LF 传输差异；CONTENT_MISMATCH、MISSING、LFS_POINTER 仍是实际阻塞，不能重写 Manifest 来消除。",
            "",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


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
    if shelter_csv.is_file():
        with shelter_csv.open(encoding="utf-8-sig", newline="") as handle:
            shelter_rows = list(csv.DictReader(handle))
    else:
        shelter_rows = []
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
    a_v2_manifests = [verify_region_manifest(region_id) for region_id in REGION_IDS]
    manifest_raw_mismatches = sum(item["raw_mismatch_count"] for item in a_v2_manifests)
    content_failures = sum(item["content_failure_count"] for item in a_v2_manifests)
    reconstructed_crlf = sum(item["reconstructed_crlf_match_count"] for item in a_v2_manifests)
    identity_payload = {
        "identity_version": DATASET_IDENTITY_VERSION,
        "admission_policy_version": ADMISSION_POLICY_VERSION,
        "files": sorted(
            [
                {
                    "path": entry["path"],
                    "bytes": entry["bytes"],
                    "sha256": entry["sha256"],
                }
                for entry in files
                if entry["exists"]
            ],
            key=lambda item: item["path"],
        ),
        "regions": sorted(
            [
                {
                    "region_id": check["region_id"],
                    "data_version": check["data_version"],
                    "enabled": check["enabled"],
                    "source_rows": check["source_rows"],
                    "od_rows": check["od_rows"],
                    "boundary_status": check["boundary_status"],
                }
                for check in checks
            ],
            key=lambda item: item["region_id"],
        ),
    }
    dataset_content_hash = hashlib.sha256(
        json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    generated_at = datetime.now(timezone.utc).isoformat()
    git_metadata = {
        "branch": git("branch", "--show-current"),
        "commit": git("rev-parse", "HEAD"),
        "integration_base": "14d686aa0043dad513eb9c165b873f04a6b5e80f",
        "historical_reference": git("rev-parse", "origin/feature/benchmark-grounded"),
    }
    return {
        "schema_version": 1,
        # These fields are audit-run metadata, not the dataset identity.
        "generated_at_utc": generated_at,
        "purpose": "Stage 0 read-only ShelterPlan-Eval source baseline",
        "run_metadata": {"generated_at_utc": generated_at, "git": git_metadata},
        "git": git_metadata,
        "dataset_identity": identity_payload,
        "dataset_content_hash": dataset_content_hash,
        "policy": {
            "old_a_v2_data_modified": False,
            "historical_merge_data_merged": False,
            "lfs_objects_read": len(lfs_pointers) == 0,
            "lfs_pointer_files": lfs_pointers,
            "missing_files": missing,
            "a_v2_manifest_all_match": manifest_raw_mismatches == 0,
            "a_v2_manifest_failure_count": manifest_raw_mismatches,
            "a_v2_manifest_raw_mismatch_count": manifest_raw_mismatches,
            "a_v2_manifest_reconstructed_crlf_count": reconstructed_crlf,
            "a_v2_manifest_content_all_match": content_failures == 0,
            "a_v2_manifest_content_failure_count": content_failures,
            "dataset_identity_version": DATASET_IDENTITY_VERSION,
            "admission_policy_version": ADMISSION_POLICY_VERSION,
        },
        "regions": checks,
        "a_v2_manifests": a_v2_manifests,
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
    blocking_report = write_manifest_blocking_report(manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"blocking_report={blocking_report.relative_to(ROOT)}")
    return 1 if manifest["policy"]["a_v2_manifest_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
