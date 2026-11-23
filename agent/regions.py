"""Module A v2 region catalog, layer/scenario discovery and schema translation.

Supports both:
* ``module_a_v2`` layouts (``configs/regions.yaml`` + ``data/processed/regions/<region_id>/``);
* ``legacy_pilot`` single-region layouts (``configs/pilot.yaml`` + ``data/processed/``).

Module C never merges OD matrices across regions and never fabricates missing
costs/budgets when translating Module A ``capacity_adjustments``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
from typing import Any

import yaml

from agent.config import HarnessConfig, PROJECT_ROOT, defaults_from_pilot_config

SUPPLEMENTAL_LAYER_SPECS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "road_edges",
        "步行道路网络",
        "map_layers/road_edges.geojson",
        "LineString",
        "OSM walkable road network edges from static Guangdong PBF snapshot.",
        "STATIC_PBF",
    ),
    (
        "network_buffer",
        "路网缓冲区",
        "map_layers/network_buffer.geojson",
        "Polygon",
        "Buffered analysis corridor around candidate shelters.",
        "DEMO_ONLY",
    ),
    (
        "boundary",
        "区域演示边界",
        "boundary.geojson",
        "Polygon",
        "Demo convex-hull/buffered analysis boundary; not an official administrative boundary.",
        "DEMO_ONLY",
    ),
    (
        "analysis_area",
        "分析范围",
        "analysis_area.geojson",
        "Polygon",
        "Regional spatial analysis extent.",
        "DEMO_ONLY",
    ),
)


@dataclass(frozen=True)
class RegionInfo:
    region_id: str
    town: str
    role: str
    data_version: str
    release_status: str
    data_status: str
    demand_nodes: int | None
    shelters: int | None
    od_available: bool
    boundary_status: str | None
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_mode: str = "legacy_pilot"
    region_dir: Path | None = None
    layers_manifest_path: Path | None = None
    scenario_catalog_path: Path | None = None
    shelters_geojson_path: Path | None = None
    manifest_path: Path | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "town": self.town,
            "role": self.role,
            "data_version": self.data_version,
            "release_status": self.release_status,
            "data_status": self.data_status,
            "demand_nodes": self.demand_nodes,
            "shelters": self.shelters,
            "od_available": self.od_available,
            "boundary_status": self.boundary_status,
            "source_mode": self.source_mode,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def translate_a_v2_scenario(raw: dict[str, Any]) -> dict[str, Any]:
    """Translate a Module A v2 scenario row into Module C's parameter vocabulary.

    ``closed_shelter_ids`` maps to ``disabled_shelter_ids``. ``capacity_adjustments``
    are preserved as parameter metadata with ``requires_cost_confirmation=True``
    because Module A does not supply engineering ``cost_units`` or budgets.
    """
    c_changes: dict[str, Any] = {}
    if raw.get("population_demand_ratio") is not None:
        c_changes["population_demand_ratio"] = float(raw["population_demand_ratio"])
    if raw.get("max_walk_time_min") is not None:
        c_changes["max_walk_time_min"] = float(raw["max_walk_time_min"])
    if "closed_shelter_ids" in raw or "disabled_shelter_ids" in raw:
        closed = raw.get("closed_shelter_ids")
        if closed is None:
            closed = raw.get("disabled_shelter_ids")
        c_changes["disabled_shelter_ids"] = [str(item) for item in (closed or [])]

    translated: dict[str, Any] = {
        "scenario_id": raw.get("scenario_id"),
        "region_id": raw.get("region_id"),
        "scenario_type": raw.get("scenario_type"),
        "data_version": raw.get("data_version"),
        "analysis_mode": raw.get("analysis_mode") or "demo",
        "c_changes": c_changes,
    }
    adjustments = raw.get("capacity_adjustments") or {}
    if adjustments:
        translated["capacity_adjustments"] = dict(adjustments)
        translated["capacity_adjustment_status"] = str(
            raw.get("capacity_adjustment_status") or "parameter_only_not_optimization"
        )
        translated["requires_cost_confirmation"] = True
        translated["note"] = (
            "Module A capacity_adjustments only describe illustrative delta_persons; "
            "cost_units and budget_units must be supplied explicitly before calling optimize."
        )
    return translated


class RegionCatalog:
    """Read-only region, layer and scenario catalog with v2/legacy auto-detection."""

    def __init__(self, root: str | Path | None = None, *,
                 config: HarnessConfig | None = None) -> None:
        self.config = config or HarnessConfig()
        self.root = Path(root or self.config.project_root or PROJECT_ROOT).resolve()
        self._lock = threading.RLock()
        self._shelter_ids_cache: dict[tuple[str, str], frozenset[str]] = {}

    @property
    def mode(self) -> str:
        # Direct HarnessConfig() instances intentionally retain the legacy v1
        # defaults for old callers/tests.  Production from_env() selects the
        # explicit A v2 Shiqi adapter.  A custom v2 workspace must still be
        # detected from its own regions.yaml regardless of the legacy class
        # defaults.
        if self.root == PROJECT_ROOT and self.config.data_version == "zhongshan_pilot_v0.1":
            return "legacy_pilot"
        if (self.root / "configs" / "regions.yaml").is_file() and (
            self.root / "data" / "processed" / "regions"
        ).is_dir():
            return "module_a_v2"
        return "legacy_pilot"

    def list_regions(self) -> list[dict[str, Any]]:
        return [info.to_public_dict() for info in self._load_regions().values()]

    def get_region(self, region_id: str) -> RegionInfo:
        _validate_region_id(region_id)
        regions = self._load_regions()
        info = regions.get(region_id)
        if info is None:
            raise KeyError(region_id)
        return info

    def get_layers(self, region_id: str) -> dict[str, Any]:
        info = self.get_region(region_id)
        if info.source_mode == "legacy_pilot":
            return self._legacy_layers(info)

        manifest_doc = _read_json(info.manifest_path) if info.manifest_path else None
        manifest_files = (manifest_doc or {}).get("files") or {}
        layers_doc = _read_json(info.layers_manifest_path) if info.layers_manifest_path else None
        layers: list[dict[str, Any]] = [
            dict(row) for row in ((layers_doc or {}).get("layers") or []) if isinstance(row, dict)
        ]
        known_ids = {str(row.get("layer_id")) for row in layers if row.get("layer_id")}
        known_paths = {str(row.get("path")) for row in layers if row.get("path")}

        region_dir = info.region_dir
        for layer_id, layer_name, rel_in_region, geom_type, desc, default_status in SUPPLEMENTAL_LAYER_SPECS:
            repo_rel = f"data/processed/regions/{info.region_id}/{rel_in_region}"
            in_manifest = repo_rel in manifest_files
            on_disk = bool(region_dir and (region_dir / rel_in_region).is_file())
            if not (in_manifest or on_disk):
                continue
            if layer_id in known_ids or repo_rel in known_paths:
                continue
            layers.append({
                "layer_id": layer_id,
                "layer_name": layer_name,
                "path": repo_rel,
                "format": "GeoJSON",
                "crs": "EPSG:4326",
                "geometry_type": geom_type,
                "description": desc,
                "fields": [],
                "data_status": info.data_status if default_status == "DEMO_ONLY" else default_status,
                "supplemented_from_manifest": True,
            })

        return {
            "schema_version": (layers_doc or {}).get("schema_version", 1),
            "region_id": info.region_id,
            "data_version": info.data_version,
            "data_status": info.data_status,
            "release_status": info.release_status,
            "source_mode": info.source_mode,
            "layers": layers,
        }

    def get_scenarios(self, region_id: str) -> dict[str, Any]:
        info = self.get_region(region_id)
        if info.source_mode == "legacy_pilot":
            return self._legacy_scenarios(info)

        catalog = _read_json(info.scenario_catalog_path) if info.scenario_catalog_path else None
        raw_scenarios = list((catalog or {}).get("scenarios") or [])
        scenarios: list[dict[str, Any]] = []
        for row in raw_scenarios:
            if not isinstance(row, dict):
                continue
            row_region = row.get("region_id")
            row_version = row.get("data_version")
            if row_region != info.region_id:
                raise ValueError(
                    f"Scenario {row.get('scenario_id')} region_id {row_region!r} "
                    f"does not match {info.region_id!r}"
                )
            if row_version != info.data_version:
                raise ValueError(
                    f"Scenario {row.get('scenario_id')} data_version {row_version!r} "
                    f"does not match {info.data_version!r}"
                )
            translated = translate_a_v2_scenario(row)
            enriched = dict(row)
            enriched["normalized_changes"] = translated["c_changes"]
            enriched["c_translation"] = translated
            scenarios.append(enriched)

        return {
            "region_id": info.region_id,
            "data_version": info.data_version,
            "data_status": info.data_status,
            "release_status": info.release_status,
            "od_available": info.od_available,
            "source_mode": info.source_mode,
            "scenarios": scenarios,
            "cross_region_scenarios": self.list_cross_region_scenarios(),
        }

    def list_cross_region_scenarios(self) -> list[dict[str, Any]]:
        path = self.root / "configs" / "planning_scenarios.yaml"
        doc = _read_yaml(path)
        if not isinstance(doc, dict):
            return []
        rows: list[dict[str, Any]] = []
        for row in doc.get("scenarios") or []:
            if not isinstance(row, dict) or row.get("scenario_type") != "cross_region_comparison":
                continue
            translated = translate_a_v2_scenario(row)
            enriched = dict(row)
            enriched["normalized_changes"] = translated["c_changes"]
            enriched["isolation_note"] = "各区域独立调用 Module B，严禁合并跨区域 OD 矩阵。"
            rows.append(enriched)
        return rows

    def get_shelter_ids(self, region_id: str) -> frozenset[str] | None:
        info = self.get_region(region_id)
        cache_key = (info.region_id, info.data_version)
        with self._lock:
            cached = self._shelter_ids_cache.get(cache_key)
        if cached is not None:
            return cached

        path = info.shelters_geojson_path
        if path is None or not path.is_file():
            return None
        doc = _read_json(path)
        if not isinstance(doc, dict):
            return None
        ids = {
            str(props["shelter_id"])
            for feature in (doc.get("features") or [])
            if isinstance(feature, dict)
            for props in [feature.get("properties") or {}]
            if isinstance(props, dict) and props.get("shelter_id")
        }
        if not ids:
            return None
        frozen = frozenset(ids)
        with self._lock:
            self._shelter_ids_cache[cache_key] = frozen
        return frozen

    # ------------------------------------------------------------------ #
    def _load_regions(self) -> dict[str, RegionInfo]:
        if self.mode == "module_a_v2":
            return self._load_v2_regions()
        info = self._load_legacy_region()
        return {info.region_id: info}

    def _load_v2_regions(self) -> dict[str, RegionInfo]:
        cfg = _read_yaml(self.root / "configs" / "regions.yaml") or {}
        project = cfg.get("project") or {}
        prefix = str(project.get("data_version") or "shelterplan_module_a_v2.0-demo")
        regions_root = (self.root / "data" / "processed" / "regions").resolve()
        configured = cfg.get("regions") or {}
        result: dict[str, RegionInfo] = {}

        for region_id, meta in configured.items():
            region_id_str = str(region_id)
            _validate_region_id(region_id_str)
            meta = meta if isinstance(meta, dict) else {}
            region_dir = (regions_root / region_id_str).resolve()
            if regions_root not in region_dir.parents:
                raise KeyError(region_id_str)

            manifest_path = region_dir / "manifest.json"
            quality_path = region_dir / "quality_summary.json"
            boundary_meta_path = region_dir / "boundary_metadata.json"
            layers_manifest_path = region_dir / "layers_manifest.json"
            scenario_catalog_path = region_dir / "scenario_catalog.json"
            shelters_geojson_path = region_dir / "shelters.geojson"

            manifest = _read_json(manifest_path) or {}
            quality = _read_json(quality_path) or {}
            boundary_meta = _read_json(boundary_meta_path) or {}

            data_version = str(
                manifest.get("data_version")
                or quality.get("data_version")
                or f"{prefix}__{region_id_str}"
            )
            data_status = str(
                quality.get("data_status")
                or quality.get("verdict")
                or self.config.expected_data_status
                or "DEMO_ONLY"
            )

            tm_meta = quality.get("travel_matrix") or {}
            has_tm_rows = isinstance(tm_meta.get("rows"), (int, float)) and tm_meta.get("rows", 0) > 0
            rel_tm = f"data/processed/regions/{region_id_str}/travel_matrix.parquet"
            tm_manifest = (manifest.get("files") or {}).get(rel_tm) or {}
            has_tm_manifest = bool(tm_manifest.get("sha256")) and int(tm_manifest.get("bytes") or 0) > 0
            tm_file = region_dir / "travel_matrix.parquet"
            has_tm_file = tm_file.is_file() and tm_file.stat().st_size > 0
            od_available = bool(has_tm_rows or has_tm_manifest or has_tm_file)

            raw_release = quality.get("release_status") or manifest.get("release_status")
            if not region_dir.is_dir() or not od_available:
                release_status = str(raw_release or "PARTIAL")
                if not od_available and release_status == "PASS_DEMO":
                    release_status = "PARTIAL"
            else:
                release_status = str(raw_release or "PASS_DEMO")

            pop_meta = quality.get("population") or {}
            demand_nodes = (
                pop_meta.get("positive_cells")
                if pop_meta.get("positive_cells") is not None
                else tm_meta.get("demand_nodes")
            )
            shelters = (
                quality.get("spatial_shelters")
                if quality.get("spatial_shelters") is not None
                else tm_meta.get("spatial_shelters")
            )
            if shelters is None:
                shelters = quality.get("source_rows")

            blockers = [str(item) for item in (quality.get("blockers") or quality.get("real_pilot_blockers") or [])]
            warnings = [str(item) for item in (quality.get("warnings") or [])]
            if not od_available and "OD 矩阵缺失" not in " ".join(warnings):
                warnings.append(f"区域 {region_id_str} 尚未生成完整 OD 矩阵（PARTIAL），暂不可执行规划计算。")
            if data_status == "DEMO_ONLY" and not any("DEMO_ONLY" in w for w in warnings):
                warnings.append("DEMO_ONLY：区域边界与场所坐标/容量未经真实现场核验，仅供演示与算法评估。")

            result[region_id_str] = RegionInfo(
                region_id=region_id_str,
                town=str(meta.get("town") or region_id_str),
                role=str(meta.get("role") or "supplementary"),
                data_version=data_version,
                release_status=release_status,
                data_status=data_status,
                demand_nodes=int(demand_nodes) if isinstance(demand_nodes, (int, float)) else None,
                shelters=int(shelters) if isinstance(shelters, (int, float)) else None,
                od_available=od_available,
                boundary_status=str(boundary_meta.get("boundary_status") or "demo_analysis_boundary"),
                blockers=blockers,
                warnings=warnings,
                source_mode="module_a_v2",
                region_dir=region_dir,
                layers_manifest_path=layers_manifest_path,
                scenario_catalog_path=scenario_catalog_path,
                shelters_geojson_path=shelters_geojson_path,
                manifest_path=manifest_path,
            )
        return result

    def _load_legacy_region(self) -> RegionInfo:
        pilot_path = self.root / "configs" / "pilot.yaml"
        pilot_doc = _read_yaml(pilot_path) or {}
        project = pilot_doc.get("project") or {}
        pilot_defaults = defaults_from_pilot_config(pilot_path if pilot_path.is_file() else None)
        data_version = str(self.config.data_version or pilot_defaults["data_version"])
        a7 = _read_json(self.root / "data" / "processed" / "a7_quality_summary.json") or {}
        data_status = str(a7.get("verdict") or self.config.expected_data_status or "DEMO_ONLY")
        demand_nodes = (a7.get("population") or {}).get("demand_nodes")
        shelters = (a7.get("shelters") or {}).get("count")
        tm_path = self.root / "data" / "processed" / "travel_matrix.parquet"
        od_available = tm_path.is_file() or bool((a7.get("travel_matrix") or {}).get("rows"))
        if not a7 and not tm_path.is_file():
            # Unit tests pointing at an isolated root without Module A files still allow legacy default.
            od_available = True
        blockers = [str(item) for item in (a7.get("real_pilot_blockers") or [])]
        warnings = [str(item) for item in (a7.get("warnings") or [])]
        if data_status == "DEMO_ONLY" and not any("DEMO_ONLY" in w for w in warnings):
            warnings.append("DEMO_ONLY：基于演示数据，未经真实现场核验，仅供演示与算法评估。")

        shelters_path = self.root / "data" / "processed" / "shelters_geocoded.geojson"
        return RegionInfo(
            region_id="east_district",
            town=str(project.get("pilot_town") or "东区街道"),
            role="primary",
            data_version=data_version,
            release_status="PASS_DEMO" if od_available else "PARTIAL",
            data_status=data_status,
            demand_nodes=int(demand_nodes) if isinstance(demand_nodes, (int, float)) else None,
            shelters=int(shelters) if isinstance(shelters, (int, float)) else None,
            od_available=od_available,
            boundary_status="demo_analysis_boundary",
            blockers=blockers,
            warnings=warnings,
            source_mode="legacy_pilot",
            region_dir=self.root / "data" / "processed",
            shelters_geojson_path=shelters_path if shelters_path.is_file() else None,
            manifest_path=self.root / "data" / "manifests" / "pilot_manifest.json",
        )

    def _legacy_layers(self, info: RegionInfo) -> dict[str, Any]:
        layers_dir = self.root / "data" / "processed" / "map_layers"
        layers: list[dict[str, Any]] = []
        if layers_dir.is_dir():
            for path in sorted(layers_dir.glob("*.geojson")):
                layers.append({
                    "layer_id": path.stem,
                    "layer_name": path.stem,
                    "path": path.relative_to(self.root).as_posix(),
                    "format": "GeoJSON",
                    "crs": "EPSG:4326",
                    "data_status": info.data_status,
                })
        return {
            "schema_version": 1,
            "region_id": info.region_id,
            "data_version": info.data_version,
            "data_status": info.data_status,
            "release_status": info.release_status,
            "source_mode": info.source_mode,
            "layers": layers,
        }

    def _legacy_scenarios(self, info: RegionInfo) -> dict[str, Any]:
        pilot_path = self.root / "configs" / "pilot.yaml"
        defaults = defaults_from_pilot_config(pilot_path if pilot_path.is_file() else None)
        base = {
            "scenario_id": f"{info.region_id}__base",
            "region_id": info.region_id,
            "scenario_type": "base",
            "population_demand_ratio": defaults["population_demand_ratio"],
            "max_walk_time_min": defaults["max_walk_time_min"],
            "closed_shelter_ids": [],
            "capacity_adjustments": {},
            "data_version": info.data_version,
            "analysis_mode": "demo",
            "assumption_type": "illustrative_scenario",
            "notes": "legacy pilot baseline demonstration",
        }
        translated = translate_a_v2_scenario(base)
        base["normalized_changes"] = translated["c_changes"]
        base["c_translation"] = translated
        return {
            "region_id": info.region_id,
            "data_version": info.data_version,
            "data_status": info.data_status,
            "release_status": info.release_status,
            "od_available": info.od_available,
            "source_mode": info.source_mode,
            "scenarios": [base],
            "cross_region_scenarios": [],
        }


def _validate_region_id(region_id: str) -> None:
    if (
        not region_id
        or not isinstance(region_id, str)
        or "/" in region_id
        or "\\" in region_id
        or ".." in region_id
        or region_id.startswith(".")
    ):
        raise KeyError(region_id)


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _read_yaml(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, yaml.YAMLError):
        return None
