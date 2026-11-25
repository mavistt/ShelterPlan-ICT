"""Read-only Module A adapter. It never writes into A-owned paths."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any
from collections import Counter

import yaml

from backend.errors import DataUnavailableError, DataValidationError
from backend.models import (
    ADataAvailability, ADataBundle, ADataManifest, DemandNode, ODEdge, Shelter,
)


_TEXT_ARTIFACT_SUFFIXES = {".csv", ".geojson", ".json", ".yaml", ".yml", ".txt"}


def _artifact_bytes(path: Path) -> bytes:
    """Read an artifact in the byte representation used by the A manifest.

    Git stores the A release text artifacts with LF line endings.  On Windows,
    a repository with ``core.autocrlf=true`` may materialize those same files
    as CRLF without changing their logical content.  Normalize only declared
    text artifacts; binary Parquet/GeoTIFF/PBF files remain byte-for-byte
    strict.
    """
    raw = path.read_bytes()
    if path.suffix.lower() not in _TEXT_ARTIFACT_SUFFIXES:
        return raw
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    canonical = normalized.encode("utf-8")
    return (b"\xef\xbb\xbf" if has_bom else b"") + canonical


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(_artifact_bytes(path))
    return digest.hexdigest()


def _raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _reconstructed_crlf_bytes(path: Path) -> bytes | None:
    if path.suffix.lower() not in _TEXT_ARTIFACT_SUFFIXES:
        return None
    raw = path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    rebuilt = normalized.encode("utf-8").replace(b"\n", b"\r\n")
    return (b"\xef\xbb\xbf" if has_bom else b"") + rebuilt


def _read_geojson(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("type") != "FeatureCollection":
            raise ValueError("expected a GeoJSON FeatureCollection")
        crs = raw.get("crs")
        crs_text = json.dumps(crs).upper() if crs is not None else ""
        if crs is not None and "4326" not in crs_text and "CRS84" not in crs_text:
            raise ValueError("GeoJSON CRS is not EPSG:4326")
        features = raw.get("features", [])
        for index, feature in enumerate(features):
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates")
            if geometry.get("type") != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
                raise ValueError(f"feature {index} must have a Point geometry")
            lon, lat = float(coordinates[0]), float(coordinates[1])
            if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f"feature {index} has invalid WGS84 coordinates")
        return [feature.get("properties", {}) for feature in features]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise DataValidationError(f"Cannot read {path.name}: {exc}") from exc


def _unique(rows: list[Any], key: str, label: str) -> None:
    values = [getattr(row, key) for row in rows]
    if len(values) != len(set(values)):
        raise DataValidationError(f"{label} contains duplicate {key} values")


def _infer_repo_root(config_path: Path) -> Path:
    """Find the repository root for both flat and nested adapter paths."""
    resolved = config_path.resolve()
    for candidate in (resolved.parent, *resolved.parents):
        if (candidate / "configs").is_dir() and (candidate / "data").is_dir():
            return candidate
    # Preserve the old fallback for isolated callers, but make the nested
    # adapter case above deterministic and explicit.
    return resolved.parent.parent


def _validate_region_rows(rows: list[dict[str, Any]], region_id: str, label: str) -> None:
    for index, row in enumerate(rows):
        if str(row.get("region_id")) != region_id:
            raise DataValidationError(
                f"{label} row {index} belongs to a different region",
                details={"expected_region_id": region_id, "received_region_id": row.get("region_id")},
            )


def _a7_summary_path(paths: dict[str, Any]) -> str:
    configured = paths.get("a7_quality_summary")
    if configured:
        return str(configured)
    return (Path(paths["demand_nodes"]).parent / "a7_quality_summary.json").as_posix()


def _validate_manifest_artifact(root: Path, relpath: str, manifest_files: dict[str, Any]) -> dict[str, Any]:
    record = manifest_files.get(relpath)
    if not isinstance(record, dict) or not isinstance(record.get("sha256"), str):
        raise DataValidationError("Manifest is missing a required file hash", details={"path": relpath})
    path = (root / relpath).resolve()
    if path != root and root not in path.parents:
        raise DataValidationError("Manifest artifact path escapes the project root", details={"path": relpath})
    if not path.is_file():
        raise DataValidationError("Manifest artifact is absent", details={"path": relpath})
    raw_bytes = path.stat().st_size
    raw_sha256 = _raw_sha256(path)
    canonical = _artifact_bytes(path)
    canonical_bytes = len(canonical)
    canonical_sha256 = hashlib.sha256(canonical).hexdigest()
    reconstructed = _reconstructed_crlf_bytes(path)
    reconstructed_bytes = len(reconstructed) if reconstructed is not None else None
    reconstructed_sha256 = hashlib.sha256(reconstructed).hexdigest() if reconstructed is not None else None
    raw_match = record.get("bytes") == raw_bytes and record.get("sha256") == raw_sha256
    reconstructed_match = (
        reconstructed is not None
        and record.get("bytes") == reconstructed_bytes
        and record.get("sha256") == reconstructed_sha256
    )
    if not raw_match and not reconstructed_match:
        raise DataValidationError("Manifest artifact size or SHA-256 does not match", details={
            "path": relpath,
            "status": "CONTENT_MISMATCH",
            "expected_bytes": record.get("bytes"),
            "actual_bytes": raw_bytes,
            "canonical_bytes": canonical_bytes,
            "reconstructed_crlf_bytes": reconstructed_bytes,
            "expected_sha256": record.get("sha256"),
            "actual_sha256": raw_sha256,
            "canonical_sha256": canonical_sha256,
            "reconstructed_crlf_sha256": reconstructed_sha256,
        })
    match_mode = "MATCH_RAW" if raw_match else "MATCH_RECONSTRUCTED_CRLF"
    matched_bytes, matched_sha256 = (
        (raw_bytes, raw_sha256) if raw_match else (reconstructed_bytes, reconstructed_sha256)
    )
    result = {
        "path": relpath,
        "bytes": matched_bytes,
        "sha256": matched_sha256,
        "matched": True,
        "match_mode": match_mode,
        "raw_bytes": raw_bytes,
        "raw_sha256": raw_sha256,
        "canonical_bytes": canonical_bytes,
        "canonical_sha256": canonical_sha256,
        "reconstructed_crlf_bytes": reconstructed_bytes,
        "reconstructed_crlf_sha256": reconstructed_sha256,
    }
    return {key: value for key, value in result.items() if value is not None}


def _read_travel_matrix(
    path: Path,
    data_version: str,
    demand_ids: set[str],
    shelter_ids: set[str],
    max_snap_distance_m: float,
    expected_region_id: str | None = None,
) -> tuple[list[ODEdge], dict[str, Any]]:
    """Stream Parquet row groups into the sparse OD model without a pandas copy."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        parquet = pq.ParquetFile(path)
    except Exception as exc:
        raise DataValidationError(f"Cannot read travel_matrix.parquet: {exc}") from exc

    required = {"demand_id", "shelter_id", "distance_m", "time_min", "reachable", "reason", "demand_snap_m", "shelter_snap_m", "walking_speed_m_per_s", "data_version"}
    if expected_region_id is not None:
        required.add("region_id")
    schema = parquet.schema_arrow
    columns = set(schema.names)
    if not required.issubset(columns):
        raise DataValidationError("Travel matrix is missing required contract fields", details={"missing": sorted(required - columns)})
    string_fields = {"demand_id", "shelter_id", "reason", "data_version"}
    if "region_id" in columns:
        string_fields.add("region_id")
    for field in schema:
        if field.name in string_fields and not (pa.types.is_string(field.type) or pa.types.is_large_string(field.type)):
            raise DataValidationError(f"Travel matrix {field.name} must have a string type", details={"type": str(field.type)})
    if not pa.types.is_boolean(schema.field("reachable").type):
        raise DataValidationError("Travel matrix reachable field must be boolean")
    for column in ("distance_m", "time_min", "demand_snap_m", "shelter_snap_m", "walking_speed_m_per_s"):
        field_type = schema.field(column).type
        if not (pa.types.is_integer(field_type) or pa.types.is_floating(field_type)):
            raise DataValidationError(f"Travel matrix {column} must be numeric", details={"type": str(field_type)})

    expected_rows = len(demand_ids) * len(shelter_ids)
    actual_rows = int(parquet.metadata.num_rows)
    if actual_rows != expected_rows:
        raise DataValidationError("Travel matrix does not contain the complete demand-by-shelter relation", details={"expected_rows": expected_rows, "actual_rows": actual_rows})
    demand_index = {did: index for index, did in enumerate(sorted(demand_ids))}
    shelter_index = {sid: index for index, sid in enumerate(sorted(shelter_ids))}
    seen_pairs: set[int] = set()
    version_counts: Counter[str] = Counter()
    reason_counts: Counter[tuple[str, bool]] = Counter()
    allowed_reasons = {"ok", "demand_snap_too_far", "shelter_snap_too_far", "no_path"}
    edges: list[ODEdge] = []
    selected = ["demand_id", "shelter_id", "distance_m", "time_min", "reachable", "reason", "demand_snap_m", "shelter_snap_m", "walking_speed_m_per_s", "data_version"]
    if "region_id" in columns:
        selected.insert(0, "region_id")
    try:
        for batch in parquet.iter_batches(batch_size=16384, columns=selected):
            arrays = {name: batch.column(name).to_pylist() for name in selected}
            for values in zip(*(arrays[name] for name in selected)):
                if "region_id" in columns:
                    row_region, did, sid, distance, time_min, reachable, reason, demand_snap, shelter_snap, speed, version = values
                else:
                    row_region = None
                    did, sid, distance, time_min, reachable, reason, demand_snap, shelter_snap, speed, version = values
                if not isinstance(did, str) or not isinstance(sid, str) or not isinstance(reason, str) or not isinstance(version, str):
                    raise DataValidationError("Travel matrix IDs, reasons and data_version must be non-null strings")
                if expected_region_id is not None and row_region != expected_region_id:
                    raise DataValidationError("Travel matrix region_id does not match config", details={"expected": expected_region_id, "found": row_region})
                if did not in demand_index or sid not in shelter_index:
                    raise DataValidationError("Travel matrix contains dangling demand/shelter foreign keys", details={"demand_id": did, "shelter_id": sid})
                pair_index = demand_index[did] * len(shelter_index) + shelter_index[sid]
                if pair_index in seen_pairs:
                    raise DataValidationError("Travel matrix contains duplicate OD keys")
                seen_pairs.add(pair_index)
                if version != data_version:
                    raise DataValidationError("Travel matrix data_version does not match config", details={"expected": data_version, "found": version})
                if not all(math.isfinite(float(value)) and float(value) >= 0 for value in (demand_snap, shelter_snap)):
                    raise DataValidationError("Travel matrix snap distances must be finite and non-negative")
                if not math.isfinite(float(speed)) or float(speed) <= 0:
                    raise DataValidationError("Travel matrix walking speed must be finite and positive")
                if reason not in allowed_reasons:
                    raise DataValidationError("Travel matrix contains an undocumented reason", details={"reason": reason})
                reachable = bool(reachable)
                if reachable:
                    if reason != "ok" or distance is None or time_min is None:
                        raise DataValidationError("Reachable OD rows require reason='ok' and finite time/distance")
                    if not all(math.isfinite(float(value)) and float(value) >= 0 for value in (distance, time_min)):
                        raise DataValidationError("Reachable OD time and distance must be finite and non-negative")
                    if float(demand_snap) > max_snap_distance_m + 1e-6 or float(shelter_snap) > max_snap_distance_m + 1e-6:
                        raise DataValidationError("Reachable OD row exceeds A's configured snap-distance rule")
                else:
                    if reason == "ok" or distance is not None or time_min is not None:
                        raise DataValidationError("Unreachable OD rows must have a non-ok reason and null time/distance")
                version_counts[version] += 1
                reason_counts[(reason, reachable)] += 1
                edges.append(ODEdge(
                    demand_id=did, shelter_id=sid,
                    time_min=None if time_min is None else float(time_min),
                    distance_m=None if distance is None else float(distance),
                    reachable=reachable, reason=reason,
                ))
    except DataValidationError:
        raise
    except Exception as exc:
        raise DataValidationError(f"Cannot parse travel_matrix.parquet: {exc}") from exc
    if len(seen_pairs) != expected_rows:
        raise DataValidationError("Travel matrix does not contain every demand/shelter pair", details={"expected_rows": expected_rows, "unique_rows": len(seen_pairs)})
    return edges, {
        "row_count": actual_rows,
        "columns": schema.names,
        "dtypes": {field.name: str(field.type) for field in schema},
        "version_counts": dict(version_counts),
        "reason_counts": {f"{reason}|{reachable}": count for (reason, reachable), count in reason_counts.items()},
    }


def inspect_data(config_path: str | Path, repo_root: str | Path | None = None) -> ADataAvailability:
    config_path = Path(config_path)
    root = Path(repo_root).resolve() if repo_root else _infer_repo_root(config_path)
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        paths = config["paths"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise DataValidationError(f"Invalid Module A config: {exc}") from exc
    keys = ("shelters_geojson", "demand_nodes", "travel_matrix", "manifest")
    expected = {key: str(paths[key]) for key in keys}
    expected["a7_quality_summary"] = _a7_summary_path(paths)
    all_keys = (*keys, "a7_quality_summary")
    existing = [(root / expected[key]).resolve().relative_to(root).as_posix() for key in all_keys if (root / expected[key]).is_file()]
    missing = [expected[key] for key in all_keys if not (root / expected[key]).is_file()]
    status = "MISSING" if missing else "DEMO_ONLY"
    notes = []
    if missing:
        notes.append("Processed Module A data or manifest is absent; real-data calculations are blocked.")
    notes.append("Module A quality audit is DEMO_ONLY; no real-planning result may be published.")
    return ADataAvailability(
        region_id=str((config.get("project") or {}).get("region_id")) if (config.get("project") or {}).get("region_id") else None,
        data_status=status, expected_paths=expected,
        existing_paths=existing, missing_paths=missing, notes=notes,
    )


def load_a_bundle(config_path: str | Path, repo_root: str | Path | None = None) -> ADataBundle:
    config_path = Path(config_path)
    root = Path(repo_root).resolve() if repo_root else _infer_repo_root(config_path)
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        project, paths = config["project"], config["paths"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise DataValidationError(f"Invalid Module A config: {exc}") from exc
    availability = inspect_data(config_path, root)
    if availability.missing_paths:
        raise DataUnavailableError(
            "Required Module A data is missing.",
            details={"missing_paths": availability.missing_paths, "data_status": availability.data_status},
        )

    shelter_rows = _read_geojson(root / paths["shelters_geojson"])
    demand_rows = _read_geojson(root / paths["demand_nodes"])
    region_id = str(project.get("region_id") or "") or None
    if region_id is not None:
        _validate_region_rows(shelter_rows, region_id, "shelters")
        _validate_region_rows(demand_rows, region_id, "demand nodes")
    try:
        shelters = []
        for row in shelter_rows:
            active = row.get("active", True)
            if not isinstance(active, bool):
                raise ValueError(f"shelter {row.get('shelter_id')} active must be boolean")
            shelters.append(Shelter(
                shelter_id=str(row["shelter_id"]),
                capacity_persons=float(row["capacity_persons"]),
                verified_status=str(row["verified_status"]),
                active=active,
            ))
        demands = [DemandNode(
            demand_id=str(row["demand_id"]), pop_est=float(row["pop_est"]),
            population_status=str(row["population_status"]),
        ) for row in demand_rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise DataValidationError(f"Required Module A field is absent or invalid: {exc}") from exc
    _unique(shelters, "shelter_id", "shelters")
    _unique(demands, "demand_id", "demand nodes")
    data_version = str(project["data_version"])
    demand_ids = {row.demand_id for row in demands}
    shelter_ids = {row.shelter_id for row in shelters}
    snap_limit = float(config.get("network", {}).get("max_snap_distance_m", 200.0))
    edges, matrix_profile = _read_travel_matrix(root / paths["travel_matrix"], data_version, demand_ids, shelter_ids, snap_limit, region_id)

    manifest_path = root / paths["manifest"]
    try:
        manifest_json = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataValidationError(f"Manifest is not valid JSON: {exc}") from exc
    manifest_version = manifest_json.get("data_version") or manifest_json.get("project", {}).get("data_version")
    if manifest_version is not None and str(manifest_version) != data_version:
        raise DataValidationError("Manifest data_version does not match config", details={"expected": data_version, "found": manifest_version})
    manifest_region = manifest_json.get("region_id") or manifest_json.get("project", {}).get("region_id")
    if region_id is not None and str(manifest_region) != region_id:
        raise DataValidationError("Manifest region_id does not match config", details={"expected": region_id, "found": manifest_region})
    a7_path = root / _a7_summary_path(paths)
    try:
        a7_summary = json.loads(a7_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataValidationError(f"A7 quality summary is not valid JSON: {exc}") from exc
    if str(a7_summary.get("data_version")) != data_version:
        raise DataValidationError("A7 data_version does not match config", details={"expected": data_version, "found": a7_summary.get("data_version")})
    if region_id is not None and str(a7_summary.get("region_id")) != region_id:
        raise DataValidationError("A7 region_id does not match config", details={"expected": region_id, "found": a7_summary.get("region_id")})
    if a7_summary.get("verdict") not in {"DEMO_ONLY", "VERIFIED"}:
        raise DataValidationError("A7 quality summary has an unknown verdict", details={"verdict": a7_summary.get("verdict")})
    if manifest_version is None:
        raise DataValidationError("Manifest does not declare its data_version")
    manifest_files = manifest_json.get("files")
    if not isinstance(manifest_files, dict):
        raise DataValidationError("Manifest does not contain a file integrity table")
    hash_checks = [
        _validate_manifest_artifact(root, str(paths[key]), manifest_files)
        for key in ("shelters_geojson", "demand_nodes", "travel_matrix")
    ]
    config_relpath = config_path.resolve().relative_to(root).as_posix()
    hash_checks.append(_validate_manifest_artifact(root, config_relpath, manifest_files))
    release_status = str(a7_summary["verdict"])
    blockers = a7_summary.get("blockers") or a7_summary.get("real_pilot_blockers") or []
    audit_note = f"A7 verdict={release_status}; real_pilot_accepted={bool(a7_summary.get('real_pilot_accepted', False))}; blockers={len(blockers)}."
    manifest = ADataManifest(
        region_id=region_id,
        data_version=data_version, source_manifest_hash=_sha256(manifest_path),
        release_status=release_status, manifest_path=str(manifest_path.relative_to(root)),
        validated_artifacts=hash_checks,
    )
    # A7's release gate is authoritative; a manifest alone never upgrades data to VERIFIED.
    data_status = "VERIFIED" if a7_summary.get("verdict") == "VERIFIED" and a7_summary.get("real_pilot_accepted") is True else "DEMO_ONLY"
    availability = availability.model_copy(update={
        "data_status": data_status,
        "notes": [*availability.notes, audit_note, f"Verified hashes for {len(hash_checks)} Module A inputs/config artifacts."],
    })
    return ADataBundle(
        region_id=region_id,
        availability=availability, manifest=manifest,
        demands=demands, shelters=shelters, od_edges=edges,
    )
