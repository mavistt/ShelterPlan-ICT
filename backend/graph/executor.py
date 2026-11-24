"""Bounded task execution with observable trace records and cached dependencies."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import platform
import time
import uuid
from pathlib import Path
from pydantic import ValidationError
import yaml

from backend.engine import MAX_BUDGET_OPTIONS, MAX_BUDGET_SOLVE_SECONDS, MAX_LP_SOLVE_SECONDS, audit_options, diagnose, eligible_edges, optimize_budgeted, solve_assignment
from backend.errors import DataUnavailableError, DataValidationError, ScenarioError, SolverTimeoutError
from backend.graph.planner import PlanDecision, validate_plan
from backend.graph.registry import SKILL_GRAPH_VERSION, SKILL_VERSION
from backend.models import ImprovementOption, PlanningInput, PlanningScenario, TaskRequest
from backend.storage import DependencyCache, JsonResultStore, canonical_hash, find_scenario_version, save_scenario_version
from backend.verifier.checks import verify_assignment, verify_budgeted


FIXTURE_ROOT = (Path(__file__).resolve().parents[2] / "tests" / "module_b" / "fixtures").resolve()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE = DependencyCache()
SOURCE_CACHE = DependencyCache()


def _validated_scenario(raw: dict) -> PlanningScenario:
    try:
        return PlanningScenario(**raw)
    except ValidationError as exc:
        raise ScenarioError("Scenario fields failed validation", details={"validation_errors": exc.errors(include_input=False)}) from exc


def load_fixture(reference: str) -> tuple[PlanningInput, PlanningScenario, list[ImprovementOption]]:
    path = Path(reference)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    path = path.resolve()
    try:
        path.relative_to(FIXTURE_ROOT)
    except ValueError as exc:
        raise DataValidationError("Fixture path must be inside tests/module_b/fixtures") from exc
    if not path.is_file() or path.suffix.lower() != ".json":
        raise DataValidationError("Fixture must be an existing JSON file inside the fixture directory")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = PlanningInput(
            data_version=raw["data_version"], data_status="SYNTHETIC_FIXTURE",
            demands=raw["demands"], shelters=raw["shelters"], od_edges=raw["od_edges"],
            source_label=f"synthetic_fixture:{raw['fixture_id']}",
        )
        scenario_raw = raw["scenario"]
        scenario = PlanningScenario(
            scenario_id=f"scenario-{raw['fixture_id']}", data_version=raw["data_version"],
            analysis_mode="demo", population_demand_ratio=scenario_raw["population_demand_ratio"],
            max_walk_time_min=scenario_raw["max_walk_time_min"], budget_units=scenario_raw.get("budget_units"),
        )
        options = [ImprovementOption(**row) for row in raw.get("improvement_options", [])]
        return data, scenario, options
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DataValidationError(f"Invalid synthetic fixture: {exc}") from exc


def _scenario_defaults(module_b: dict, adapter: dict) -> tuple[float, float]:
    """Resolve defaults from B first, then an optional A adapter section."""
    b_defaults = module_b.get("module_b") or {}
    adapter_defaults = adapter.get("scenario_defaults") or {}
    ratio = b_defaults.get("default_population_demand_ratio", adapter_defaults.get("population_demand_ratio", 0.2))
    walk = b_defaults.get("default_max_walk_time_min", adapter_defaults.get("max_walk_time_min", 15.0))
    return float(ratio), float(walk)


def _source_cache_key(config_path: Path) -> str:
    manifest_path: Path | None = None
    try:
        adapter = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        manifest_rel = (adapter.get("paths") or {}).get("manifest")
        if manifest_rel:
            manifest_path = PROJECT_ROOT / str(manifest_rel)
    except (OSError, yaml.YAMLError):
        pass
    stats = []
    for path in (config_path, manifest_path):
        if path is None:
            continue
        try:
            stat = path.stat()
            stats.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            stats.append((str(path), "missing"))
    return canonical_hash(stats)


def module_a_source_registry(module_b_config: dict) -> dict[str, dict]:
    """Load the explicit, region-keyed allowlist of Module A v2 adapters."""
    module_b = module_b_config.get("module_b") or {}
    configured_sources = module_b.get("region_source_configs") or {}
    if not isinstance(configured_sources, dict):
        raise DataValidationError(
            "Module B region_source_configs must be a region-to-config mapping",
            details={"error_code": "SOURCE_REGISTRY_INVALID"},
        )
    sources = dict(configured_sources)
    default_region = module_b.get("region_id")
    default_config = module_b.get("source_config")
    if default_region and default_config:
        existing = sources.get(str(default_region))
        if existing is not None and Path(str(existing)) != Path(str(default_config)):
            raise DataValidationError(
                "Module B default source_config conflicts with its region allowlist entry",
                details={"error_code": "SOURCE_REGISTRY_INVALID", "region_id": str(default_region)},
            )
        sources[str(default_region)] = default_config
    if not sources:
        raise DataUnavailableError(
            "Module B has no allowed Module A source configurations",
            details={"error_code": "DATA_UNAVAILABLE"},
        )

    allowed_config_root = (PROJECT_ROOT / "configs" / "module_a_v2_b").resolve()
    registry: dict[str, dict] = {}
    version_regions: dict[str, str] = {}
    for raw_region_id, source_reference in sources.items():
        region_id = str(raw_region_id)
        if not region_id or not isinstance(source_reference, str) or not source_reference:
            raise DataValidationError(
                "Module B source allowlist contains an invalid region or config path",
                details={"error_code": "SOURCE_REGISTRY_INVALID", "region_id": region_id},
            )
        source_path = Path(source_reference)
        if source_path.is_absolute():
            raise DataValidationError(
                "Module A source config paths must be project-relative",
                details={"error_code": "SOURCE_REGISTRY_INVALID", "region_id": region_id},
            )
        config_path = (PROJECT_ROOT / source_path).resolve()
        try:
            config_path.relative_to(allowed_config_root)
        except ValueError as exc:
            raise DataValidationError(
                "Module A source config is outside the allowed v2 config directory",
                details={"error_code": "SOURCE_REGISTRY_INVALID", "region_id": region_id},
            ) from exc
        if config_path.suffix.lower() not in {".yaml", ".yml"} or not config_path.is_file():
            raise DataUnavailableError(
                "An allowlisted Module A source config is missing or invalid",
                details={"error_code": "DATA_UNAVAILABLE", "region_id": region_id},
            )
        try:
            adapter = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise DataValidationError(
                "Could not read an allowlisted Module A source config",
                details={"error_code": "SOURCE_REGISTRY_INVALID", "region_id": region_id},
            ) from exc
        project = adapter.get("project") or {}
        configured_region = str(project.get("region_id") or "")
        data_version = str(project.get("data_version") or "")
        if configured_region != region_id or not data_version:
            raise DataValidationError(
                "Allowlisted Module A config identity does not match its region key",
                details={
                    "error_code": "SOURCE_REGISTRY_INVALID",
                    "region_id": region_id,
                    "configured_region_id": configured_region or None,
                    "data_version_present": bool(data_version),
                },
            )
        if data_version in version_regions:
            raise DataValidationError(
                "Module A data_version must identify exactly one allowlisted region",
                details={"error_code": "SOURCE_REGISTRY_AMBIGUOUS", "data_version": data_version},
            )
        version_regions[data_version] = region_id
        registry[region_id] = {
            "config_path": config_path,
            "adapter": adapter,
            "data_version": data_version,
            "data_status": project.get("data_status"),
        }
    return registry


def load_task_source(
    fixture_id: str | None,
    requested_region_id: str | None = None,
    requested_data_version: str | None = None,
) -> tuple[PlanningInput, PlanningScenario, list[ImprovementOption]]:
    if fixture_id:
        if fixture_id not in {"toy_case", "mixed_bottlenecks"}:
            raise DataValidationError("Unknown fixture_id", details={"allowed_fixture_ids": ["toy_case", "mixed_bottlenecks"]})
        return load_fixture(FIXTURE_ROOT / f"{fixture_id}.json")
    from backend.adapters.a_reader import load_a_bundle
    module_b_path = PROJECT_ROOT / "configs" / "module_b.yaml"
    try:
        module_b_config = yaml.safe_load(module_b_path.read_text(encoding="utf-8"))
        module_b = module_b_config.get("module_b") or {}
        registry = module_a_source_registry(module_b_config)
        selected_source = None
        legacy_adapter = None

        if requested_region_id:
            selected_source = registry.get(requested_region_id)
            if selected_source is None:
                raise DataValidationError(
                    "Requested region_id is not in the Module A source allowlist",
                    details={"error_code": "REGION_NOT_ALLOWED", "received": requested_region_id, "allowed_region_ids": sorted(registry)},
                )
            if requested_data_version and selected_source["data_version"] != requested_data_version:
                raise ScenarioError(
                    "Requested data_version does not match the allowlisted region source",
                    details={"error_code": "VERSION_MISMATCH", "region_id": requested_region_id, "expected": selected_source["data_version"], "received": requested_data_version},
                )
        elif requested_data_version:
            selected_source = next((source for source in registry.values() if source["data_version"] == requested_data_version), None)
            if selected_source is None:
                # Keep support for explicitly versioned legacy pilot sessions.
                # It is never selected when a region is supplied or by default.
                legacy_path = (PROJECT_ROOT / "configs" / "pilot.yaml").resolve()
                legacy_adapter = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) or {}
                legacy_version = (legacy_adapter.get("project") or {}).get("data_version")
                if requested_data_version != legacy_version:
                    raise ScenarioError(
                        "Requested data_version does not match an allowed Module A source",
                        details={"error_code": "VERSION_MISMATCH", "received": requested_data_version, "allowed_data_versions": sorted(source["data_version"] for source in registry.values())},
                    )
        else:
            default_region = str(module_b.get("region_id") or "")
            selected_source = registry.get(default_region)
            if selected_source is None:
                raise DataUnavailableError(
                    "Module B default region is not present in the Module A source allowlist",
                    details={"error_code": "SOURCE_REGISTRY_INVALID", "region_id": default_region or None},
                )

        if selected_source is not None:
            config_path = selected_source["config_path"]
            pilot = selected_source["adapter"]
        elif legacy_adapter is not None:
            legacy_path = (PROJECT_ROOT / "configs" / "pilot.yaml").resolve()
            config_path, pilot = legacy_path, legacy_adapter
        else:
            raise DataUnavailableError("No Module A source matched the requested identity", details={"error_code": "DATA_UNAVAILABLE"})

        configured_region_id = (pilot.get("project") or {}).get("region_id")
        if requested_region_id and configured_region_id and requested_region_id != configured_region_id:
            raise DataValidationError(
                "Requested region_id does not match the configured Module A source",
                details={"error_code": "REGION_MISMATCH", "expected": configured_region_id, "received": requested_region_id},
            )
        if not config_path.is_file():
            raise DataUnavailableError("Configured Module A adapter is missing", details={"error_code": "DATA_UNAVAILABLE", "path": str(config_path)})
        cache_key = _source_cache_key(config_path)
        cached = SOURCE_CACHE.get(cache_key)
        bundle = cached if cached is not None else load_a_bundle(config_path, PROJECT_ROOT)
        if cached is None:
            SOURCE_CACHE.put(cache_key, bundle)
        region_id = bundle.region_id or configured_region_id
        if requested_region_id and not region_id:
            raise DataValidationError("Requested source does not declare region_id", details={"error_code": "REGION_MISSING"})
        if requested_data_version and bundle.manifest.data_version != requested_data_version:
            raise DataValidationError(
                "Loaded Module A source has a different data_version",
                details={"error_code": "VERSION_MISMATCH", "expected": requested_data_version, "found": bundle.manifest.data_version},
            )
        if requested_region_id and region_id and requested_region_id != region_id:
            raise DataValidationError(
                "Loaded Module A source has a different region_id",
                details={"error_code": "REGION_MISMATCH", "expected": requested_region_id, "found": region_id},
            )
        data = PlanningInput(
            region_id=region_id,
            data_version=bundle.manifest.data_version, data_status=bundle.availability.data_status,
            demands=bundle.demands, shelters=bundle.shelters, od_edges=bundle.od_edges,
            source_manifest_hash=bundle.manifest.source_manifest_hash,
            source_label=f"module_a:{bundle.manifest.data_version}",
        )
        ratio, walk = _scenario_defaults(module_b_config, pilot)
        scenario = PlanningScenario(
            scenario_id=f"scenario-{bundle.manifest.data_version}",
            region_id=region_id,
            data_version=bundle.manifest.data_version, analysis_mode="demo",
            population_demand_ratio=ratio,
            max_walk_time_min=walk,
        )
        return data, scenario, []
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise DataValidationError(f"Could not load configured Module A source: {exc}") from exc


def _scenario_for_request(data: PlanningInput, base: PlanningScenario, request: TaskRequest) -> PlanningScenario:
    raw = base.model_dump()
    raw.update({"analysis_mode": request.analysis_mode})
    if request.region_id is not None and request.region_id != data.region_id:
        raise ScenarioError("Request region_id does not match selected source", details={"expected": data.region_id, "received": request.region_id, "error_code": "REGION_MISMATCH"})
    if request.data_version is not None and request.data_version != data.data_version:
        raise ScenarioError("Request data_version does not match selected source", details={"expected": data.data_version, "received": request.data_version, "error_code": "VERSION_MISMATCH"})
    if request.scenario is not None:
        if data.region_id is not None and request.scenario.region_id is not None and request.scenario.region_id != data.region_id:
            raise ScenarioError("Scenario region_id does not match selected source", details={"expected": data.region_id, "received": request.scenario.region_id, "error_code": "REGION_MISMATCH"})
        if request.scenario.data_version != data.data_version:
            raise ScenarioError("Scenario data_version does not match selected source", details={"expected": data.data_version, "received": request.scenario.data_version, "error_code": "VERSION_MISMATCH"})
        raw.update(request.scenario.model_dump())
    elif request.changes:
        allowed = {"population_demand_ratio", "max_walk_time_min", "budget_units", "disabled_shelter_ids"}
        unknown = set(request.changes) - allowed
        if unknown:
            raise ScenarioError("Scenario change contains unsupported fields", details={"fields": sorted(unknown)})
        raw.update(request.changes)
    if request.scenario_id:
        raw["scenario_id"] = request.scenario_id
    if request.parent_scenario_id:
        raw["parent_scenario_id"] = request.parent_scenario_id
    if request.changes:
        raw["changes"] = request.changes
    raw["data_version"] = data.data_version
    raw["region_id"] = data.region_id
    if not request.scenario_id and (request.changes or request.parent_scenario_id):
        scenario_key = {key: value for key, value in raw.items() if key != "scenario_id"}
        raw["scenario_id"] = f"scenario-{canonical_hash(scenario_key)[:16]}"
    return _validated_scenario(raw)


def _node_record(skill_id: str, input_hash: str, output: object, start: float, status="SUCCEEDED", cache_hit=False, error_code=None) -> dict:
    return {
        "skill_id": skill_id, "skill_version": SKILL_VERSION,
        "input_hash": input_hash, "output_hash": canonical_hash(output),
        "duration_ms": round((time.perf_counter() - start) * 1000, 3),
        "status": status, "error_code": error_code, "cache_hit": cache_hit,
    }


def execute_task(request: TaskRequest, *, source_override: tuple[PlanningInput, PlanningScenario, list[ImprovementOption]] | None = None) -> dict:
    allowed_parameters = {
        "assess": set(), "optimize": {"budget_units"},
        "diagnose_intervene": {"budget_units"},
        "stress_test": {"disabled_shelter_ids", "budget_units"},
        "compare": {"scenarios"},
        "sensitivity": {"parameter", "values"},
        "explain_result": set(),
    }
    unknown_parameters = set(request.parameters) - allowed_parameters[request.task_type]
    if unknown_parameters:
        raise ScenarioError("Request contains unsupported task parameters", details={"fields": sorted(unknown_parameters)})
    if request.improvement_options and request.task_type not in {"optimize", "diagnose_intervene", "stress_test", "sensitivity"}:
        raise ScenarioError("Improvement options are not used by this task type")
    # source_override is an internal evaluation hook. The HTTP API always calls
    # this function without it, so callers cannot inject data through a request.
    requested_version = request.data_version or (request.scenario.data_version if request.scenario else None)
    data, base_scenario, fixture_options = source_override or load_task_source(
        request.fixture_id, request.region_id, requested_version
    )
    if request.parent_scenario_id:
        parent = find_scenario_version(request.parent_scenario_id)
        if parent is None:
            raise ScenarioError("Parent scenario ID was not found", details={"error_code": "PARENT_SCENARIO_NOT_FOUND", "parent_scenario_id": request.parent_scenario_id})
        if parent.get("data_version") != data.data_version:
            raise ScenarioError("Parent scenario uses a different data version", details={"error_code": "VERSION_MISMATCH", "parent_data_version": parent.get("data_version"), "data_version": data.data_version})
    scenario = _scenario_for_request(data, base_scenario, request)
    options = request.improvement_options or fixture_options
    has_eligible_options = any(option.eligible and option.shelter_id not in scenario.disabled_shelter_ids for option in options)
    plan = validate_plan(request.task_type, request.proposed_skill_plan, has_candidates=has_eligible_options)
    run_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    task_id = request.task_id or str(uuid.uuid4())
    started = time.perf_counter()
    scenario_version = save_scenario_version(scenario.model_dump(), data.data_version)
    records: list[dict] = []
    if data.data_status == "SYNTHETIC_FIXTURE":
        warnings = ["SYNTHETIC_FIXTURE: not Module A data and not a real-world planning result."]
    else:
        warnings = ["DEMO_ONLY: Module A handoff has not passed real-pilot acceptance; do not claim real coverage or current capacity."]
    source_hash = data.source_manifest_hash or canonical_hash(data.model_dump())

    # S01 + S02: admission and normalized scenario.
    for skill_id, payload in (("S01", {"data_status": data.data_status, "analysis_mode": scenario.analysis_mode}), ("S02", scenario.model_dump())):
        tick = time.perf_counter()
        records.append(_node_record(skill_id, canonical_hash(payload), payload, tick))
    if scenario.analysis_mode == "verified":
        raise ScenarioError("Verified planning cannot run on synthetic fixture data", details={"error_code": "DATA_NOT_VERIFIED", "data_status": data.data_status})

    # S03 key intentionally excludes budget; a budget-only fork reuses this relation.
    graph_input = {"source_hash": source_hash, "data_version": data.data_version, "max_walk_time_min": scenario.max_walk_time_min, "disabled": sorted(scenario.disabled_shelter_ids), "skill_version": SKILL_VERSION}
    graph_key = canonical_hash(graph_input)
    tick = time.perf_counter()
    edges = CACHE.get(graph_key)
    graph_hit = edges is not None
    if edges is None:
        edges = eligible_edges(data, scenario)
        CACHE.put(graph_key, edges)
    records.append(_node_record("S03", graph_key, edges, tick, cache_hit=graph_hit))

    # S04 cache is isolated from budget and option selection.
    assignment_input = {**graph_input, "ratio": scenario.population_demand_ratio, "capacities": [(s.shelter_id, s.capacity_persons) for s in data.shelters]}
    assignment_key = canonical_hash(assignment_input)
    tick = time.perf_counter()
    base_result = CACHE.get(assignment_key)
    assignment_hit = base_result is not None
    if base_result is None:
        base_result = solve_assignment(data, scenario)
        if base_result.solver_status in {"OPTIMAL", "FEASIBLE"}:
            CACHE.put(assignment_key, base_result)
    if base_result.solver_status in {"SOLVER_TIMEOUT", "SOLVER_ERROR", "NO_FEASIBLE_SOLUTION"}:
        raise SolverTimeoutError("Assignment solver did not return a publishable solution", details={"solver_status": base_result.solver_status})
    records.append(_node_record("S04", assignment_key, base_result.model_dump(), tick, cache_hit=assignment_hit))
    result: dict = {"assignment": base_result.model_dump()}

    if request.task_type in {"assess", "diagnose_intervene", "explain_result"}:
        tick = time.perf_counter()
        diagnosis = diagnose(data, scenario, base_result)
        result["diagnosis"] = diagnosis
        records.append(_node_record("S05", canonical_hash({"assignment": assignment_key}), diagnosis, tick))

    if request.task_type == "optimize" or (request.task_type == "diagnose_intervene" and has_eligible_options):
        tick = time.perf_counter()
        audit_options(data, options)
        audit_output = [{"option_id": row.option_id, "eligible": row.eligible, "hypothetical": row.hypothetical, "source_status": row.source_status} for row in options]
        records.append(_node_record("S06", canonical_hash(options), audit_output, tick))
        budget = request.parameters.get("budget_units", scenario.budget_units)
        option_hash = canonical_hash(options)
        optimize_key = canonical_hash({"assignment_key": assignment_key, "options": option_hash, "budget": budget})
        tick = time.perf_counter()
        optimization = CACHE.get(optimize_key)
        optimize_hit = optimization is not None
        if optimization is None:
            optimization = optimize_budgeted(data, scenario, options, budget)
            if data.data_status != "SYNTHETIC_FIXTURE":
                # T04 evidence is produced by B from the same real data and
                # legal candidate set. C only projects and explains it.
                try:
                    from backend.evaluation.real_data_runner import _greedy_budget_curve
                    curve = _greedy_budget_curve(
                        data, scenario, options, [float(budget)], initial_assignment=base_result
                    )
                    greedy = curve.get("plans", {}).get(str(float(budget)))
                    if greedy is not None:
                        optimization["greedy_comparison"] = {
                            "status": "available",
                            "served_demand": float(greedy["served_demand_persons"]),
                            "selected_option_ids": list(greedy.get("selected_option_ids") or []),
                            "cost_units": float(greedy.get("cost_units") or 0.0),
                            "budget_units": float(budget),
                            "verification_status": greedy.get("verification_status"),
                            "method": "Module B real-data greedy baseline",
                        }
                except Exception as exc:
                    optimization["greedy_comparison"] = {
                        "status": "unavailable",
                        "reason": f"B greedy baseline failed: {exc.__class__.__name__}",
                    }
            CACHE.put(optimize_key, optimization)
        result["optimization"] = optimization
        records.append(_node_record("S07", optimize_key, optimization, tick, cache_hit=optimize_hit))
        if request.task_type == "optimize":
            tick = time.perf_counter()
            records.append(_node_record("S08", canonical_hash(optimization["assignment"]), {"weighted_walk_time_min": optimization["assignment"]["weighted_walk_time_min"]}, tick))
        if optimization["selected_option_ids"] and any(option.hypothetical for option in options if option.option_id in optimization["selected_option_ids"]):
            warnings.append("HYPOTHETICAL_IMPROVEMENT: selected option costs/capacity are user-supplied assumptions, not Module A or engineering facts.")

    if request.task_type == "diagnose_intervene" and not has_eligible_options:
        result["intervention"] = {"status": "NOT_APPLICABLE", "reason_code": "NO_ELIGIBLE_EXPANSION_OPTIONS", "message": "No eligible expansion option was supplied; bottleneck evidence is returned without an optimization claim."}

    if request.task_type == "stress_test":
        tick = time.perf_counter()
        disabled = request.parameters.get("disabled_shelter_ids", scenario.disabled_shelter_ids)
        stress_changes = {"disabled_shelter_ids": disabled}
        stressed = _validated_scenario({**scenario.model_dump(), "disabled_shelter_ids": disabled, "scenario_id": f"{scenario.scenario_id}-stress", "parent_scenario_id": scenario.scenario_id, "changes": stress_changes})
        stress_version = save_scenario_version(stressed.model_dump(), data.data_version)
        budget = request.parameters.get("budget_units", scenario.budget_units)
        selected_fixed = []
        fixed_deltas = {}
        if budget is not None and options:
            baseline_plan = optimize_budgeted(data, scenario, options, budget)
            selected_fixed = [row for row in options if row.option_id in baseline_plan["selected_option_ids"]]
            for row in selected_fixed:
                fixed_deltas[row.shelter_id] = fixed_deltas.get(row.shelter_id, 0.0) + row.delta_capacity_persons
        fixed = solve_assignment(data, stressed, fixed_deltas)
        adaptive = optimize_budgeted(data, stressed, options, budget) if budget is not None else None
        fixed_certificate = verify_assignment(data, stressed, fixed, fixed_deltas)
        adaptive_certificate = verify_budgeted(data, stressed, options, adaptive, float(budget)) if adaptive is not None else None
        if fixed_certificate.verification_status == "FAIL" or (adaptive_certificate and adaptive_certificate["verification_status"] == "FAIL"):
            raise ScenarioError("Independent verifier rejected a stress-test branch")
        result["stress"] = {
            "scenario_id": stressed.scenario_id, "parent_scenario_id": stressed.parent_scenario_id,
            "scenario_hash": stress_version["scenario_hash"], "changes": stress_changes,
            "fixed_plan_stress": {"selected_option_ids": [row.option_id for row in selected_fixed], "assignment": fixed.model_dump(), "verification": fixed_certificate.model_dump()},
            "adaptive_replan": ({**adaptive, "verification": adaptive_certificate} if adaptive is not None else None),
            "comparison_semantics": "fixed plan holds selected options constant; adaptive replan may reselect options",
        }
        records.append(_node_record("S09", canonical_hash({"scenario": scenario.model_dump(), "stress": request.parameters}), result["stress"], tick))

    if request.task_type == "sensitivity":
        tick = time.perf_counter()
        field = request.parameters.get("parameter", "budget_units")
        values = request.parameters.get("values", [0, 5, 10])
        if field not in {"budget_units", "population_demand_ratio", "max_walk_time_min"} or not isinstance(values, list) or not 1 <= len(values) <= 20:
            raise ScenarioError("Sensitivity requires 1–20 values for a supported parameter")
        points = []
        for value in values:
            try:
                changed = _validated_scenario({**scenario.model_dump(), field: value, "scenario_id": f"{scenario.scenario_id}-sensitivity-{len(points)}", "parent_scenario_id": scenario.scenario_id, "changes": {field: value}})
            except Exception as exc:
                raise ScenarioError("Sensitivity value is outside the scenario schema", details={"parameter": field, "value": value}) from exc
            if field == "budget_units":
                point = optimize_budgeted(data, changed, options, float(value))
                point_certificate = verify_budgeted(data, changed, options, point, float(value))
                if point_certificate["verification_status"] == "FAIL":
                    raise ScenarioError("Independent verifier rejected a sensitivity point")
                points.append({
                    "value": value,
                    "scenario_id": changed.scenario_id,
                    "scenario_hash": save_scenario_version(changed.model_dump(), data.data_version)["scenario_hash"],
                    "served_demand": point["objective_value"],
                    "weighted_walk_time_min": point["assignment"]["weighted_walk_time_min"],
                    "selected_option_ids": point["selected_option_ids"],
                    "solver_status": point["solver_status"],
                    "optimality_proven": point["optimality_proven"],
                    "verification_status": point_certificate["verification_status"],
                })
            else:
                point = solve_assignment(data, changed)
                point_certificate = verify_assignment(data, changed, point)
                if point_certificate.verification_status == "FAIL":
                    raise ScenarioError("Independent verifier rejected a sensitivity point")
                points.append({
                    "value": value,
                    "scenario_id": changed.scenario_id,
                    "scenario_hash": save_scenario_version(changed.model_dump(), data.data_version)["scenario_hash"],
                    "served_demand": point.served_demand,
                    "weighted_walk_time_min": point.weighted_walk_time_min,
                    "solver_status": point.solver_status,
                    "optimality_proven": point.optimality_proven,
                    "verification_status": point_certificate.verification_status,
                })
        result["sensitivity"] = {"parameter": field, "points": points}
        records.append(_node_record("S10", canonical_hash({"field": field, "values": values, "source": source_hash}), points, tick))

    if request.task_type == "compare":
        tick = time.perf_counter()
        scenarios = request.parameters.get("scenarios", [])
        if not scenarios or len(scenarios) > 12:
            raise ScenarioError("Compare requires 1–12 explicit scenario overrides")
        rows = []
        for index, changes in enumerate(scenarios):
            if not isinstance(changes, dict):
                raise ScenarioError("Each comparison override must be a JSON object")
            allowed = {"population_demand_ratio", "max_walk_time_min", "budget_units", "disabled_shelter_ids"}
            if set(changes) - allowed:
                raise ScenarioError("Comparison scenario has unsupported fields")
            changed = _validated_scenario({**scenario.model_dump(), **changes, "scenario_id": f"{scenario.scenario_id}-compare-{index}", "parent_scenario_id": scenario.scenario_id, "changes": changes})
            compare_version = save_scenario_version(changed.model_dump(), data.data_version)
            assigned = solve_assignment(data, changed)
            scenario_certificate = verify_assignment(data, changed, assigned)
            if scenario_certificate.verification_status == "FAIL":
                raise ScenarioError("Independent verifier rejected a comparison scenario")
            rows.append({"scenario_id": changed.scenario_id, "parent_scenario_id": changed.parent_scenario_id, "scenario_hash": compare_version["scenario_hash"], "changes": changes, "served_demand": assigned.served_demand, "weighted_walk_time_min": assigned.weighted_walk_time_min, "solver_status": assigned.solver_status, "verification_status": scenario_certificate.verification_status})
        result["comparison"] = {"data_version": data.data_version, "scenarios": rows, "unique_recommendation": None}
        records.append(_node_record("S11", canonical_hash({"scenario": scenario.model_dump(), "scenarios": scenarios}), rows, tick))

    if request.task_type == "explain_result":
        if not request.result_id:
            raise ScenarioError("explain_result requires a result_id", details={"required_user_action": "Provide a previously stored result_id."})
        previous = JsonResultStore("outputs/module_b/runs").get(request.result_id)
        if previous is None:
            raise ScenarioError("Result ID was not found", details={"error_code": "RESULT_NOT_FOUND", "result_id": request.result_id})
        if previous.get("data_version") != data.data_version:
            raise ScenarioError("Result data version differs from selected fixture", details={"error_code": "VERSION_MISMATCH"})
        if data.region_id is not None and previous.get("region_id") != data.region_id:
            raise ScenarioError("Result region differs from selected source", details={"error_code": "REGION_MISMATCH", "expected": data.region_id, "received": previous.get("region_id")})
        result["explanation"] = {
            "result_id": request.result_id,
            "claims": previous.get("result_summary", {}),
            "evidence_refs": [f"result:{request.result_id}/assignment", f"result:{request.result_id}/certificate"],
            "data_status": previous.get("data_status"),
            "region_id": previous.get("region_id"),
            "data_version": previous.get("data_version"),
            "result_detail": previous.get("result_detail"),
            "solver_status": previous.get("solver_status"),
            "verification_status": previous.get("verification_status"),
        }

    cert = verify_assignment(data, scenario, base_result)
    if cert.verification_status == "FAIL":
        raise ScenarioError("Independent verifier rejected the assignment", details={"certificate": cert.model_dump()})
    result_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())
    evidence = {
        "region_id": data.region_id,
        "source_hash": source_hash, "scenario_hash": canonical_hash(scenario.model_dump()),
        "source_manifest_hash": data.source_manifest_hash,
        "solver_settings_hash": canonical_hash({"method": "scipy-highs-lp-milp", "primary_tolerance": 1e-9, "lp_time_limit_seconds": MAX_LP_SOLVE_SECONDS, "budget_time_limit_seconds": MAX_BUDGET_SOLVE_SECONDS, "max_budget_options": MAX_BUDGET_OPTIONS}),
        "skill_graph_version": SKILL_GRAPH_VERSION,
        "solver_version": f"SciPy {__import__('scipy').__version__} HiGHS",
        "runtime": {"python": platform.python_version(), "random_seed": None, "deterministic": True},
    }
    optimization_certificate = None
    if "optimization" in result:
        budget_value = float(result["optimization"]["budget_units"])
        optimization_certificate = verify_budgeted(data, scenario, options, result["optimization"], budget_value)
        result["optimization_certificate"] = optimization_certificate
        if optimization_certificate["verification_status"] == "FAIL":
            raise ScenarioError("Independent verifier rejected the budgeted result", details={"certificate": optimization_certificate})
    records.append(_node_record("S12", canonical_hash({"result": result, "certificate": cert.model_dump(), "optimization_certificate": optimization_certificate}), evidence, time.perf_counter()))
    diagnosis_for_summary = result.get("diagnosis")
    if diagnosis_for_summary is None:
        diagnosis_for_summary = diagnose(data, scenario, base_result)
    return {
        "task_id": task_id, "job_id": run_id, "result_id": result_id,
        "scenario_id": scenario.scenario_id, "parent_scenario_id": scenario.parent_scenario_id,
        "scenario_changes": scenario.changes or request.changes, "scenario_hash": scenario_version["scenario_hash"],
        "region_id": data.region_id, "data_version": data.data_version, "data_status": data.data_status,
        "analysis_mode": scenario.analysis_mode, "source_label": data.source_label,
        "skill_graph_version": SKILL_GRAPH_VERSION, "execution_trace_id": trace_id,
        "plan_decision": {"status": plan.status, "reason_codes": plan.reason_codes, "composite_id": plan.composite_id, "nodes": plan.nodes},
        "solver_status": result.get("optimization", {}).get("solver_status", base_result.solver_status), "verification_status": cert.verification_status,
        "certificate": cert.model_dump(), "optimization_certificate": optimization_certificate, "warnings": warnings,
        "result_summary": {"served_demand": base_result.served_demand, "total_demand": base_result.total_demand, "weighted_walk_time_min": base_result.weighted_walk_time_min, "optimized_served_demand": result.get("optimization", {}).get("objective_value"), "diagnosed_unserved_nodes": len(diagnosis_for_summary)},
        "artifacts": [{"artifact_id": artifact_id, "kind": "result_detail", "media_type": "application/json"}],
        "result_detail": result, "trace": records,
        "provenance": evidence,
        "cache_hit": any(row["cache_hit"] for row in records),
        "recomputed_nodes": [row["skill_id"] for row in records if not row["cache_hit"]],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
