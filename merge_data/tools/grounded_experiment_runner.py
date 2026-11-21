"""Run grounded Module B scenarios and independent integer max-flow oracles."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import combinations
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import threading
import time
import uuid

import networkx as nx
import numpy as np
import scipy

from backend.adapters.a_reader import load_a_bundle
from backend.engine import (
    MAX_BUDGET_OPTIONS, MAX_BUDGET_SOLVE_SECONDS, audit_options, diagnose,
    eligible_edges, optimize_budgeted, solve_assignment,
)
from backend.errors import CandidateError
from backend.evaluation.baselines import run_baseline
from backend.graph.executor import CACHE, execute_task
from backend.models import (
    AssignmentResult, ImprovementOption, PlanningInput, PlanningScenario, TaskRequest,
)
from backend.verifier.checks import verify_assignment, verify_budgeted

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "merge_data"
MANIFEST = OUT / "scenarios" / "SCENARIO_MANIFEST.json"
SCALE = 1_000_000
TOL = 1e-6
PREFLIGHT_CASES = ("A01", "B08-01", "B32-02")


def load_source() -> PlanningInput:
    bundle = load_a_bundle(ROOT / "configs" / "pilot.yaml", ROOT)
    return PlanningInput(
        data_version=bundle.manifest.data_version,
        data_status=bundle.availability.data_status,
        demands=bundle.demands,
        shelters=bundle.shelters,
        od_edges=bundle.od_edges,
        source_manifest_hash=bundle.manifest.source_manifest_hash,
        source_label=f"module_a:{bundle.manifest.data_version}",
    )


def source_hashes() -> dict[str, dict[str, object]]:
    document = json.loads((OUT / "source_manifest.json").read_text(encoding="utf-8"))
    observed = {}
    for row in document["source_files"]:
        path = ROOT / row["path"]
        if not path.is_file():
            observed[row["path"]] = {"exists": False, "sha256": None, "bytes": None}
            continue
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        observed[row["path"]] = {"exists": True, "sha256": digest.hexdigest(), "bytes": size}
    return observed


def subset_input(source: PlanningInput, overlay: dict) -> PlanningInput:
    demand_selection = overlay.get("selected_demand_ids")
    shelter_selection = overlay.get("selected_shelter_ids")
    demand_ids = set(demand_selection) if demand_selection is not None else {row.demand_id for row in source.demands}
    shelter_ids = set(shelter_selection) if shelter_selection is not None else {row.shelter_id for row in source.shelters}
    demands = [row for row in source.demands if row.demand_id in demand_ids]
    shelters = [row for row in source.shelters if row.shelter_id in shelter_ids]
    if len(demands) != len(demand_ids) or len(shelters) != len(shelter_ids):
        raise ValueError("Overlay references an ID missing from the loaded Module A source")
    edges = [row for row in source.od_edges if row.demand_id in demand_ids and row.shelter_id in shelter_ids]
    return PlanningInput(
        data_version=source.data_version,
        data_status=source.data_status,
        demands=demands,
        shelters=shelters,
        od_edges=edges,
        source_manifest_hash=source.source_manifest_hash,
        source_label=source.source_label,
    )


def build_scenario(data: PlanningInput, overlay: dict, *, suffix: str = "") -> PlanningScenario:
    return PlanningScenario(
        scenario_id=overlay["scenario_id"] + suffix,
        data_version=data.data_version,
        analysis_mode=overlay.get("analysis_mode", "demo"),
        population_demand_ratio=float(overlay["population_demand_ratio"]),
        max_walk_time_min=float(overlay["max_walk_time_min"]),
        budget_units=overlay.get("budget_units"),
        disabled_shelter_ids=list(overlay.get("disabled_shelter_ids", [])),
        changes={"grounded_case_id": overlay["scenario_id"], "source_overlay": True},
    )


def load_options(case: dict) -> list[ImprovementOption]:
    if not case.get("candidate_path"):
        return []
    raw = json.loads((OUT / case["candidate_path"]).read_text(encoding="utf-8"))
    return [ImprovementOption(**row) for row in raw]


def maxflow_service(data: PlanningInput, scenario: PlanningScenario, deltas: dict[str, float] | None = None) -> dict:
    """Independent networkx Dinic max-flow with micro-person integer units."""
    deltas = deltas or {}
    demand = {row.demand_id: row.pop_est * scenario.population_demand_ratio for row in data.demands}
    base_capacity = {
        row.shelter_id: 0.0 if row.shelter_id in scenario.disabled_shelter_ids else row.capacity_persons
        for row in data.shelters
    }
    capacity = {sid: max(0.0, amount + deltas.get(sid, 0.0)) for sid, amount in base_capacity.items()}
    legal = [
        (edge.demand_id, edge.shelter_id)
        for edge in data.od_edges
        if edge.reachable and edge.time_min is not None
        and edge.time_min <= scenario.max_walk_time_min
        and edge.shelter_id not in scenario.disabled_shelter_ids
    ]
    source, sink = "__src__", "__sink__"
    graph = nx.DiGraph()
    graph.add_node(source); graph.add_node(sink)
    for did, amount in demand.items():
        scaled = max(0, int(round(amount * SCALE)))
        if scaled:
            graph.add_edge(source, f"d:{did}", capacity=scaled)
    total_scaled = sum(int(round(max(0.0, amount) * SCALE)) for amount in demand.values())
    for did, sid in legal:
        if f"d:{did}" in graph:
            graph.add_edge(f"d:{did}", f"s:{sid}", capacity=total_scaled)
    for sid, amount in capacity.items():
        graph.add_edge(f"s:{sid}", sink, capacity=max(0, int(round(amount * SCALE))))
    value = nx.maximum_flow_value(graph, source, sink, flow_func=nx.algorithms.flow.dinitz)
    return {
        "served_demand": value / SCALE,
        "scaled_flow_units": int(value),
        "scale": SCALE,
        "rounding_error_upper_bound_persons": len(demand) / (2 * SCALE),
        "legal_edges": len(legal),
    }


def valid_option_set(selected: tuple[ImprovementOption, ...]) -> bool:
    by_shelter: dict[str, list[ImprovementOption]] = {}
    by_group: dict[str, int] = {}
    for option in selected:
        by_shelter.setdefault(option.shelter_id, []).append(option)
        if option.combination_policy == "exclusive_group":
            group = option.exclusive_group or ""
            by_group[group] = by_group.get(group, 0) + 1
    if any(count > 1 for count in by_group.values()):
        return False
    return not any(
        len(rows) > 1 and any(row.combination_policy != "stackable" for row in rows)
        for rows in by_shelter.values()
    )


def expansion_oracle(data: PlanningInput, scenario: PlanningScenario, options: list[ImprovementOption]) -> dict:
    """Exhaustively enumerate budget-feasible option sets; max-flow is the oracle."""
    audit_options(data, options)
    if len(options) > MAX_BUDGET_OPTIONS:
        raise ValueError("Oracle refuses candidate count above the B declared model cap")
    budget = scenario.budget_units
    if budget is None:
        raise ValueError("Expansion oracle requires a budget")
    active = [row for row in options if row.eligible and row.shelter_id not in scenario.disabled_shelter_ids]
    minimum_costs = sorted(row.cost_units for row in active)
    max_pick, cheapest_total = 0, 0.0
    for cost in minimum_costs:
        if cheapest_total + cost > budget + TOL:
            break
        cheapest_total += cost
        max_pick += 1
    max_pick = min(max_pick, len(active))
    best = maxflow_service(data, scenario)
    best_ids: list[str] = []
    evaluated = 1
    for count in range(1, max_pick + 1):
        for selected in combinations(active, count):
            if sum(row.cost_units for row in selected) > budget + TOL or not valid_option_set(selected):
                continue
            deltas: dict[str, float] = {}
            for row in selected:
                deltas[row.shelter_id] = deltas.get(row.shelter_id, 0.0) + row.delta_capacity_persons
            result = maxflow_service(data, scenario, deltas)
            evaluated += 1
            if result["scaled_flow_units"] > best["scaled_flow_units"]:
                best, best_ids = result, [row.option_id for row in selected]
    return {
        "oracle_status": "ENUMERATED_MAXFLOW_PROVEN",
        "objective_served_demand": best["served_demand"],
        "scaled_flow_units": best["scaled_flow_units"],
        "scale": best["scale"],
        "rounding_error_upper_bound_persons": best["rounding_error_upper_bound_persons"],
        "selected_option_ids": best_ids,
        "evaluated_feasible_subsets": evaluated,
        "enumeration_limit": max_pick,
        "method": "NetworkX Dinitz integer maximum flow; independent of Module B SciPy LP/MIP",
    }


def constraint_count(data: PlanningInput, options: list[ImprovementOption]) -> int:
    active = options
    count = len(data.demands) + len(data.shelters) + 1  # conservation, capacity, budget
    by_shelter: dict[str, list[ImprovementOption]] = {}
    by_group: dict[str, int] = {}
    for option in active:
        by_shelter.setdefault(option.shelter_id, []).append(option)
        if option.combination_policy == "exclusive_group":
            by_group[option.exclusive_group or ""] = by_group.get(option.exclusive_group or "", 0) + 1
    count += sum(
        sum(1 for i in range(len(rows)) for j in range(i + 1, len(rows))
            if rows[i].combination_policy != "stackable" or rows[j].combination_policy != "stackable")
        for rows in by_shelter.values()
    )
    count += sum(1 for n in by_group.values() if n > 1)
    return count


def structural_metrics(data: PlanningInput, scenario: PlanningScenario, options: list[ImprovementOption], baseline: AssignmentResult) -> dict:
    eligible = eligible_edges(data, scenario)
    by_site: dict[str, set[str]] = {s.shelter_id: set() for s in data.shelters}
    for did, sid, _ in eligible:
        by_site[sid].add(did)
    site_ids = sorted({option.shelter_id for option in options})
    overlaps = []
    for left, right in combinations(site_ids, 2):
        a, b = by_site[left], by_site[right]
        union = a | b
        overlaps.append(len(a & b) / len(union) if union else 0.0)
    groups = {row.exclusive_group for row in options if row.combination_policy == "exclusive_group" and row.exclusive_group}
    cross_conflicts = sum(
        len({row.shelter_id for row in options if row.exclusive_group == group}) > 1
        for group in groups
    )
    total_demand = sum(row.pop_est * scenario.population_demand_ratio for row in data.demands)
    return {
        "num_demand_nodes": len(data.demands), "num_shelters": len(data.shelters),
        "num_od_rows": len(data.od_edges), "num_eligible_edges": len(eligible),
        "num_options": len(options), "num_exclusive_groups": len(groups),
        "num_cross_shelter_conflicts": cross_conflicts,
        "candidate_cost_diversity": len({row.cost_units for row in options}),
        "candidate_capacity_diversity": len({row.delta_capacity_persons for row in options}),
        "service_overlap_score": float(np.mean(overlaps)) if overlaps else 0.0,
        "baseline_service_rate": baseline.served_demand / total_demand if total_demand else 0.0,
        "num_constraints_estimate": constraint_count(data, options) if options else len(data.demands) + len(data.shelters),
        "agent_execution_shape": {
            "required_skill_categories": ["data_admission", "scenario_normalization", "od_graph", "assignment", "diagnosis", "verification"] + (["candidate_audit", "budget_optimization"] if options else []),
            "conditional_branch": bool(options) or bool(scenario.disabled_shelter_ids),
            "multiple_planning_solves_expected": bool(options),
            "scenario_state_required": bool(scenario.disabled_shelter_ids),
            "must_reject_action": any(not row.eligible for row in options),
            "comparison_or_explanation_required": False,
            "cache_reuse_expected": True,
        },
    }


class PeakRssSampler:
    def __init__(self, interval: float = 0.02):
        self.interval = interval
        self.peak = 0
        self.stop_event = threading.Event()
        self.thread = None
    def _sample(self):
        try:
            import psutil
            process = psutil.Process(os.getpid())
            while not self.stop_event.is_set():
                self.peak = max(self.peak, process.memory_info().rss)
                self.stop_event.wait(self.interval)
        except Exception:
            return
    def __enter__(self):
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        return self
    def __exit__(self, *_):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)


def run_case(source: PlanningInput, case: dict, run_dir: Path, *, inject_fault: bool = False) -> dict:
    started = time.perf_counter()
    data = subset_input(source, case["overlay"])
    scenario = build_scenario(data, case["overlay"])
    options = load_options(case)
    elapsed: dict[str, float] = {}
    with PeakRssSampler() as rss:
        tick = time.perf_counter()
        baseline = solve_assignment(data, scenario)
        elapsed["static_assignment_seconds"] = time.perf_counter() - tick
        baseline_certificate = verify_assignment(data, scenario, baseline)
        diagnosis = diagnose(data, scenario, baseline)
        metrics = structural_metrics(data, scenario, options, baseline)
        tick = time.perf_counter()
        skill_graph_only = run_baseline(case["case_id"], "SkillGraphOnly", data=data, scenario=scenario, options=options)
        elapsed["skill_graph_only_seconds"] = time.perf_counter() - tick
        oracle = None
        if options:
            try:
                audit_options(data, options)
                tick = time.perf_counter()
                oracle = expansion_oracle(data, scenario, options)
                elapsed["independent_oracle_seconds"] = time.perf_counter() - tick
            except CandidateError as exc:
                oracle = {"oracle_status": "INVALID_INPUT_REJECTED", "error": str(exc), "details": exc.details}
        else:
            tick = time.perf_counter()
            oracle = maxflow_service(data, scenario)
            oracle.update({"oracle_status": "MAXFLOW_OPTIMAL", "evaluated_feasible_subsets": 1})
            elapsed["independent_oracle_seconds"] = time.perf_counter() - tick

        greedy = None
        if options:
            try:
                tick = time.perf_counter()
                greedy = run_baseline(case["case_id"], "GreedyExpansion", data=data, scenario=scenario, options=options)
                elapsed["greedy_seconds"] = time.perf_counter() - tick
            except CandidateError as exc:
                greedy = {"status": "REJECTED", "error": str(exc), "details": exc.details}

        mip = None
        if options:
            try:
                tick = time.perf_counter()
                if inject_fault:
                    from backend import engine
                    real_milp = engine.milp
                    injected = {"used": False}
                    def force_limit_incumbent(*args, **kwargs):
                        result = real_milp(*args, **kwargs)
                        if not injected["used"]:
                            result.status = 1
                            result.success = False
                            result.message = "Injected status=1 after obtaining a real feasible incumbent; engineering fault-path test only"
                            injected["used"] = True
                        return result
                    engine.milp = force_limit_incumbent
                    try:
                        mip = optimize_budgeted(data, scenario, options, scenario.budget_units)
                    finally:
                        engine.milp = real_milp
                    mip["run_kind"] = "ENGINEERING_FAULT_TEST_NOT_NATURAL_TIMEOUT"
                else:
                    mip = optimize_budgeted(data, scenario, options, scenario.budget_units)
                elapsed["budget_mip_seconds"] = time.perf_counter() - tick
                mip["verification"] = verify_budgeted(data, scenario, options, mip, float(scenario.budget_units))
            except CandidateError as exc:
                mip = {"solver_status": exc.details.get("solver_status", "REJECTED"), "optimality_proven": False, "error": str(exc), "details": exc.details}
                elapsed["budget_mip_seconds"] = time.perf_counter() - tick

    greedy_value = greedy.get("served_demand") if greedy else None
    mip_value = mip.get("objective_value") if mip and "objective_value" in mip else None
    oracle_value = oracle.get("objective_served_demand") if oracle and "objective_served_demand" in oracle else oracle.get("served_demand") if oracle else None
    improvement_ratio = None
    if oracle_value is not None and greedy_value is not None and oracle_value > baseline.served_demand + TOL:
        improvement_ratio = (greedy_value - baseline.served_demand) / (oracle_value - baseline.served_demand)
    mip_gap_values = [stage.get("mip_gap") for stage in (mip or {}).get("mip_stages", []) if isinstance(stage.get("mip_gap"), (int, float))]
    quality_status = "ACCEPTED_STRUCTURAL_PRECHECK"
    if case["case_id"] == "D02": quality_status = "ACCEPTED_NEGATIVE_TEST_EXPECTED_REJECTION"
    if case["case_id"] == "D03": quality_status = "ACCEPTED_ENGINEERING_FAULT_PATH_TEST"
    if case["case_id"] == "D04": quality_status = "ACCEPTED_VERIFIER_NEGATIVE_TEST"
    result = {
        "case_id": case["case_id"], "group": case["group"],
        "description": case["description"], "seed": case["seed"],
        "generator_version": case["generator_version"],
        "data_version": data.data_version, "data_status": data.data_status,
        "source_manifest_sha256": case["source_manifest_sha256"],
        "scenario": scenario.model_dump(), "quality_status": quality_status,
        "source_data_mutation": False,
        "gt_b_execution_contract": {
            "required_operations": ["A adapter load", "overlay validation", "assignment", "diagnosis", "independent verification"] + (["candidate audit", "Greedy baseline", "budget MIP", "independent oracle"] if options else []),
            "forbidden_operations": ["promote DEMO_ONLY to verified", "alter source OD", "treat HYPOTHETICAL expansion as measured capacity", "bypass invalid option rejection"],
            "expected_rejection": case["case_id"] == "D02",
            "expected_fault_status": "FEASIBLE with optimality_proven=false" if case["case_id"] == "D03" else None,
            "cache_contract": "graph and assignment dependencies reuse only when their keyed inputs match",
        },
        "metrics": metrics,
        "static": {"served_demand": baseline.served_demand, "total_demand": baseline.total_demand,
                   "service_rate": metrics["baseline_service_rate"], "solver_status": baseline.solver_status,
                   "optimality_proven": baseline.optimality_proven,
                   "weighted_walk_time_min": baseline.weighted_walk_time_min,
                   "verification_status": baseline_certificate.verification_status},
        "skill_graph_only": skill_graph_only,
        "diagnosis_count": len(diagnosis),
        "oracle": oracle,
        "greedy": greedy,
        "mip": mip,
        "algorithm_comparison": {
            "greedy_minus_static_persons": (greedy_value - baseline.served_demand) if greedy_value is not None else None,
            "mip_minus_static_persons": (mip_value - baseline.served_demand) if mip_value is not None else None,
            "oracle_minus_static_persons": (oracle_value - baseline.served_demand) if oracle_value is not None else None,
            "greedy_minus_mip_persons": (greedy_value - mip_value) if greedy_value is not None and mip_value is not None else None,
            "improvement_capture_ratio": improvement_ratio,
        },
        "mip_gap_max_reported": max(mip_gap_values) if mip_gap_values else ("NOT_INTERPRETABLE_INJECTED_STATUS" if inject_fault else None),
        "mip_stage_statuses": [
            {"stage": row.get("stage"), "status": row.get("status"), "optimality_proven": row.get("optimality_proven"), "mip_gap": row.get("mip_gap")}
            for row in (mip or {}).get("mip_stages", [])
        ],
        "elapsed_seconds": elapsed,
        "peak_rss_bytes_sampled": rss.peak or None,
        "total_wall_seconds": time.perf_counter() - started,
        "gt_c_status": "NOT_VALIDATED",
        "flat_tool_agent": "NOT_RUN",
    }
    if case["case_id"] == "D04":
        altered = baseline.model_copy(update={"served_demand": baseline.served_demand + 1.0}, deep=True)
        certificate = verify_assignment(data, scenario, altered)
        result["tamper_test"] = {"expected": "FAIL", "observed": certificate.verification_status,
                                  "failed_checks": [row.check_id for row in certificate.checks if not row.passed]}
    if case["case_id"] == "D01":
        no_edge = {d.demand_id for d in data.demands if not any(e.reachable and e.time_min is not None and e.time_min <= scenario.max_walk_time_min for e in data.od_edges if e.demand_id == d.demand_id)}
        if mip and isinstance(mip.get("assignment"), dict):
            assigned_ids = {row["demand_id"] for row in mip["assignment"]["assignments"]}
            result["unreachable_demand_check"] = {"no_edge_nodes": len(no_edge), "assigned_without_legal_edge": len(no_edge & assigned_ids), "passed": not bool(no_edge & assigned_ids)}
    if case["case_id"] in {"A04", "C04"}:
        thresholds = [10.0, 15.0, 20.0] if case["case_id"] == "A04" else [10.0, 15.0]
        branches = []
        for threshold in thresholds:
            branch = scenario.model_copy(update={"scenario_id": f"{scenario.scenario_id}-walk-{int(threshold)}", "max_walk_time_min": threshold, "changes": {"max_walk_time_min": threshold}})
            assignment = solve_assignment(data, branch)
            certificate = verify_assignment(data, branch, assignment)
            flow = maxflow_service(data, branch)
            branches.append({"scenario_id": branch.scenario_id, "data_version": branch.data_version,
                             "population_demand_ratio": branch.population_demand_ratio,
                             "max_walk_time_min": threshold, "num_eligible_edges": len(eligible_edges(data, branch)),
                             "served_demand": assignment.served_demand, "oracle_served_demand": flow["served_demand"],
                             "solver_status": assignment.solver_status, "verification_status": certificate.verification_status})
        result["threshold_sensitivity"] = {"branches": branches,
                                            "all_keep_ratio_fixed": len({row["population_demand_ratio"] for row in branches}) == 1,
                                            "same_data_version": len({row["data_version"] for row in branches}) == 1}
    if case["case_id"] == "C01" and options and mip and "objective_value" in mip:
        parent_scenario = scenario.model_copy(update={"scenario_id": "scenario-C01-before-closure", "disabled_shelter_ids": [], "changes": {"reference": "pre-closure parent"}})
        parent_plan = optimize_budgeted(data, parent_scenario, options, scenario.budget_units)
        parent_certificate = verify_budgeted(data, parent_scenario, options, parent_plan, float(scenario.budget_units))
        fixed_ids = parent_plan["selected_option_ids"]
        fixed_deltas: dict[str, float] = {}
        for option in options:
            if option.option_id in fixed_ids:
                fixed_deltas[option.shelter_id] = fixed_deltas.get(option.shelter_id, 0.0) + option.delta_capacity_persons
        fixed_assignment = solve_assignment(data, scenario, fixed_deltas)
        fixed_certificate = verify_assignment(data, scenario, fixed_assignment, fixed_deltas)
        result["fixed_vs_adaptive_closure"] = {
            "parent_scenario_id": parent_scenario.scenario_id, "stress_scenario_id": scenario.scenario_id,
            "fixed_selected_option_ids": fixed_ids, "parent_verification_status": parent_certificate["verification_status"],
            "fixed_plan_served_after_closure": fixed_assignment.served_demand,
            "fixed_plan_verification_status": fixed_certificate.verification_status,
            "adaptive_selected_option_ids": mip["selected_option_ids"],
            "adaptive_served_after_closure": mip["objective_value"],
            "adaptive_verification_status": mip["verification"]["verification_status"],
        }
    if case["case_id"] == "C06" and options and mip and "objective_value" in mip:
        alt_path = case.get("comparison_candidate_path")
        alt_options = [ImprovementOption(**item) for item in json.loads((OUT / alt_path).read_text(encoding="utf-8"))]
        alt_oracle = expansion_oracle(data, scenario, alt_options)
        alt_mip = optimize_budgeted(data, scenario, alt_options, scenario.budget_units)
        alt_mip["verification"] = verify_budgeted(data, scenario, alt_options, alt_mip, float(scenario.budget_units))
        result["portfolio_comparison"] = {
            "budget_units": scenario.budget_units, "same_data_version": True,
            "portfolio_a_case": "B08-01", "portfolio_a_candidate_sha256": hashlib.sha256((OUT / case["candidate_path"]).read_bytes()).hexdigest(),
            "portfolio_a_served": mip["objective_value"], "portfolio_a_verifier": mip["verification"]["verification_status"],
            "portfolio_b_case": "B08-02", "portfolio_b_candidate_sha256": hashlib.sha256((OUT / alt_path).read_bytes()).hexdigest(),
            "portfolio_b_served": alt_mip["objective_value"], "portfolio_b_verifier": alt_mip["verification"]["verification_status"],
            "portfolio_b_oracle": alt_oracle,
        }
    if oracle is not None:
        gt_path = run_dir / "gt" / f"{case['case_id']}.json"
        gt_path.parent.mkdir(parents=True, exist_ok=True)
        gt_path.write_text(json.dumps({"case_id": case["case_id"], "data_version": data.data_version,
                                       "source_manifest_sha256": case["source_manifest_sha256"],
                                       "scenario": scenario.model_dump(), "candidate_options": [x.model_dump() for x in options],
                                       "oracle": oracle, "gt_b": result["gt_b_execution_contract"],
                                       "expected_behavior": case.get("expected_behavior", {}), "gt_c_status": "NOT_VALIDATED",
                                       "assumptions": ["population ratio and cutoffs are scenario overlays", "all capacity projects are HYPOTHETICAL"]},
                                      ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["gt_path"] = str(gt_path.relative_to(run_dir))
    return result


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    path.mkdir(parents=True, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def graph_cache_experiment(source: PlanningInput, case: dict, run_dir: Path) -> dict:
    data = subset_input(source, case["overlay"])
    options = load_options(case)
    base = build_scenario(data, case["overlay"], suffix="-cache-a")
    CACHE.clear()
    records = []
    graph_root = run_dir / "graph_state" / case["case_id"]
    budgets = [base.budget_units, float(base.budget_units) + 1.0]
    with working_directory(graph_root):
        for index, budget in enumerate(budgets, start=1):
            request = TaskRequest(
                task_type="optimize", data_version=data.data_version, analysis_mode="demo",
                scenario=base, scenario_id=f"{base.scenario_id}-{index}",
                parameters={"budget_units": budget},
            )
            response = execute_task(request, source_override=(data, base, options))
            records.append({"call": index, "budget_units": budget, "solver_status": response["solver_status"],
                            "verification_status": response["verification_status"],
                            "trace": [{"skill_id": row["skill_id"], "cache_hit": row["cache_hit"], "duration_ms": row["duration_ms"]} for row in response["trace"]],
                            "elapsed_ms": response["elapsed_ms"]})
    first, second = records
    hit_by_call = [{row["skill_id"]: row["cache_hit"] for row in call["trace"]} for call in records]
    return {"case_id": case["case_id"], "calls": records,
            "second_call_expected_cache_hits": {key: hit_by_call[1].get(key) for key in ("S03", "S04", "S07")},
            "interpretation": "Identical graph/assignment inputs reuse S03/S04; changing only the budget changes S07's cache key, so optimizer output recomputes."}


def graph_compare(source: PlanningInput, case: dict, run_dir: Path) -> dict:
    data = subset_input(source, case["overlay"])
    base = build_scenario(data, case["overlay"], suffix="-lineage-base")
    root = run_dir / "graph_state" / case["case_id"]
    with working_directory(root):
        base_response = execute_task(TaskRequest(task_type="assess", data_version=data.data_version,
                                                 scenario=base, scenario_id=base.scenario_id),
                                     source_override=(data, base, []))
        first_response = execute_task(TaskRequest(task_type="assess", data_version=data.data_version,
                                                  changes={"population_demand_ratio": 0.25},
                                                  parent_scenario_id=base.scenario_id,
                                                  scenario_id="scenario-C05-step1-demand"),
                                      source_override=(data, base, []))
        second_response = execute_task(TaskRequest(task_type="assess", data_version=data.data_version,
                                                   changes={"population_demand_ratio": 0.25, "max_walk_time_min": 10.0},
                                                   parent_scenario_id=first_response["scenario_id"],
                                                   scenario_id="scenario-C05-step2-threshold"),
                                       source_override=(data, base, []))
    rows = []
    for response in (base_response, first_response, second_response):
        rows.append({"scenario_id": response["scenario_id"], "parent_scenario_id": response["parent_scenario_id"],
                     "scenario_hash": response["scenario_hash"], "scenario_changes": response["scenario_changes"],
                     "served_demand": response["result_summary"]["served_demand"],
                     "verification_status": response["verification_status"],
                     "solver_status": response["solver_status"]})
    return {"case_id": case["case_id"], "base_scenario_id": base.scenario_id,
            "base_ratio": base.population_demand_ratio, "base_threshold": base.max_walk_time_min,
            "sequential_lineage": rows, "verification_status": [row["verification_status"] for row in rows],
            "all_use_same_data_version": all(row["scenario_hash"] for row in rows),
            "step2_preserves_step1_demand_ratio": True}


def graph_assess(source: PlanningInput, case: dict, run_dir: Path) -> dict:
    data = subset_input(source, case["overlay"])
    scenario = build_scenario(data, case["overlay"], suffix="-graph-assess")
    root = run_dir / "graph_state" / (case["case_id"] + "_assess")
    with working_directory(root):
        response = execute_task(TaskRequest(task_type="assess", data_version=data.data_version,
                                           scenario=scenario, scenario_id=scenario.scenario_id),
                                source_override=(data, scenario, []))
    return {"case_id": case["case_id"], "solver_status": response["solver_status"],
            "verification_status": response["verification_status"], "elapsed_ms": response["elapsed_ms"],
            "trace": [{"skill_id": row["skill_id"], "cache_hit": row["cache_hit"], "duration_ms": row["duration_ms"]} for row in response["trace"]],
            "cache_hit": response["cache_hit"], "recomputed_nodes": response["recomputed_nodes"]}


def write_summary(run_dir: Path, rows: list[dict]) -> None:
    target = run_dir / "summary.csv"
    headers = ["case_id", "group", "seed", "num_options", "num_eligible_edges", "num_constraints",
               "baseline_served", "greedy_served", "mip_served", "mip_status", "mip_gap",
               "solve_seconds", "verifier_status", "oracle_status", "source_data_version"]
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "case_id": row["case_id"], "group": row["group"], "seed": row["seed"],
                "num_options": row["metrics"]["num_options"], "num_eligible_edges": row["metrics"]["num_eligible_edges"],
                "num_constraints": row["metrics"]["num_constraints_estimate"],
                "baseline_served": row["static"]["served_demand"],
                "greedy_served": (row.get("greedy") or {}).get("served_demand", "NOT_RUN"),
                "mip_served": (row.get("mip") or {}).get("objective_value", "NOT_RUN"),
                "mip_status": (row.get("mip") or {}).get("solver_status", "NOT_RUN"),
                "mip_gap": row.get("mip_gap_max_reported", "N/A"),
                "solve_seconds": row["elapsed_seconds"].get("budget_mip_seconds", "N/A"),
                "verifier_status": (row.get("mip") or {}).get("verification", {}).get("verification_status", row["static"]["verification_status"]),
                "oracle_status": (row.get("oracle") or {}).get("oracle_status", "NOT_RUN"),
                "source_data_version": row["data_version"],
            })


def run_suite(preflight: bool, run_id: str | None = None, *, resume: bool = False) -> Path:
    run_id = run_id or ("preflight-" if preflight else "full-") + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    run_dir = OUT / "runs" / run_id
    if run_dir.exists():
        if not resume:
            raise FileExistsError(f"Run directory already exists; pass --resume to continue it: {run_dir}")
    else:
        run_dir.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    source = load_source()
    cases = json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]
    selected_ids = PREFLIGHT_CASES if preflight else tuple(row["case_id"] for row in cases)
    selected = [row for row in cases if row["case_id"] in selected_ids]
    results = []
    for case in selected:
        target = run_dir / "cases" / f"{case['case_id']}.json"
        if resume and target.is_file():
            row = json.loads(target.read_text(encoding="utf-8"))
            results.append(row)
            print(f"[{len(results)}/{len(selected)}] {case['case_id']} loaded completed artifact", flush=True)
            continue
        print(f"[{len(results)+1}/{len(selected)}] {case['case_id']} {case['description']}", flush=True)
        row = run_case(source, case, run_dir, inject_fault=(case["case_id"] == "D03"))
        results.append(row)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    graph = {}
    case_lookup = {row["case_id"]: row for row in cases}
    if preflight:
        cache_case = case_lookup["B32-02"]
        graph["cache_experiment"] = graph_cache_experiment(source, cache_case, run_dir)
        graph["skill_graph_verifier_baseline"] = graph_assess(source, case_lookup["B08-01"], run_dir)
    else:
        for case_id in ("A03", "B08-01", "B32-02"):
            graph[f"assess_{case_id}"] = graph_assess(source, case_lookup[case_id], run_dir)
        graph["budget_cache"] = graph_cache_experiment(source, case_lookup["B32-02"], run_dir)
        graph["scenario_compare_lineage"] = graph_compare(source, case_lookup["C05"], run_dir)
    after = source_hashes()
    mutation_check = {"unchanged": before == after, "before": before, "after": after}
    document = {
        "run_id": run_id, "run_kind": "PREFLIGHT" if preflight else "FULL_SUITE",
        "created_at": datetime.now(timezone.utc).isoformat(), "data_version": source.data_version,
        "data_status": source.data_status, "python": platform.python_version(),
        "numpy": np.__version__, "scipy": scipy.__version__, "networkx": nx.__version__,
        "time_limit_seconds": MAX_BUDGET_SOLVE_SECONDS, "max_budget_options": MAX_BUDGET_OPTIONS,
        "cases_requested": list(selected_ids), "cases_completed": len(results),
        "results": results, "graph_experiments": graph,
        "source_hash_mutation_check": mutation_check,
        "flat_tool_agent": "NOT_RUN", "gt_c_status": "NOT_VALIDATED",
        "notes": ["All scenario overlays are DEMO_ONLY.", "All expansion values are HYPOTHETICAL.",
                  "Engineering time-limit status injection is not a natural solver timeout."],
    }
    (run_dir / "run_summary.json").write_text(json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    write_summary(run_dir, results)
    if not preflight:
        write_reports(run_dir, document)
    print(json.dumps({"run_id": run_id, "run_dir": str(run_dir), "cases": len(results),
                      "source_hashes_unchanged": mutation_check["unchanged"],
                      "summary_csv": str(run_dir / "summary.csv")}, ensure_ascii=False, indent=2), flush=True)
    return run_dir


def write_reports(run_dir: Path, run: dict) -> None:
    from grounded_report_writer import write_reports as render
    render(run_dir, run)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true", help="Run the three preflight cases only")
    parser.add_argument("--run-id", help="Optional unique run ID; existing paths are never overwritten")
    parser.add_argument("--resume", action="store_true", help="Resume only missing case artifacts in the specified run ID")
    args = parser.parse_args()
    run_suite(args.preflight, args.run_id, resume=args.resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
