"""Bounded search for difficult, A-GIS-anchored hypothetical expansion sets."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import sys
import time

import networkx as nx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "merge_data" / "tools"))

import backend.engine as engine
from backend.evaluation.baselines import run_baseline
from backend.models import DemandNode, ImprovementOption, ODEdge, PlanningInput, PlanningScenario
from backend.verifier.checks import verify_assignment, verify_budgeted
from grounded_experiment_runner import (
    expansion_oracle, load_options, load_source, maxflow_service,
    source_hashes, valid_option_set,
)

OUT = ROOT / "merge_data" / "v2_difficulty_discovery"
MANIFEST = ROOT / "merge_data" / "scenarios" / "SCENARIO_MANIFEST.json"
PHASE_A = OUT / "oracles" / "phase_a_real_structure.json"
SEED = 20261114
MAX_FULL_MIP = 30
TOL = 1e-5
FLOW_SCALE = 1_000_000


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def option_dict(option: ImprovementOption) -> dict:
    return option.model_dump()


def reachable_demand_by_shelter(data, scenario) -> dict[str, float]:
    by_id = {row.demand_id: row.pop_est * scenario.population_demand_ratio for row in data.demands}
    demand_sets: dict[str, set[str]] = {}
    for edge in data.od_edges:
        if (edge.reachable and edge.time_min is not None
                and edge.time_min <= scenario.max_walk_time_min):
            demand_sets.setdefault(edge.shelter_id, set()).add(edge.demand_id)
    return {sid: sum(by_id[did] for did in dids) for sid, dids in demand_sets.items()}


def cheap_upper_gain(option: ImprovementOption, reachable_demand: dict[str, float]) -> float:
    return min(option.delta_capacity_persons, reachable_demand.get(option.shelter_id, 0.0))


def budget_only_subset_upper_bound(options: list[ImprovementOption], budget: float) -> int:
    """Exact count under budget alone (thus an upper bound after exclusivity)."""
    scale = 100
    costs = [int(round(row.cost_units * scale)) for row in options]
    cap = int(math.floor(budget * scale + 1e-8))
    if cap > 250_000:
        # A bounded multiplicity polynomial still gives a safe upper bound.
        dp = [0] * (len(options) + 1)
        dp[0] = 1
        for _ in options:
            for k in range(len(options), 0, -1):
                dp[k] += dp[k - 1]
        return sum(dp)
    dp = [0] * (cap + 1)
    dp[0] = 1
    for cost in costs:
        for value in range(cap, cost - 1, -1):
            dp[value] += dp[value - cost]
    return sum(dp)


def make_option(option_id: str, shelter_id: str, delta: float, cost: float,
                policy: str = "standalone", group: str | None = None) -> ImprovementOption:
    return ImprovementOption(
        option_id=option_id, shelter_id=shelter_id, action="expand_capacity",
        delta_capacity_persons=float(delta), cost_units=float(cost), eligible=True,
        source_status="HYPOTHETICAL_A_GIS_ANCHORED_V2", hypothetical=True,
        human_confirmed=False, combination_policy=policy, exclusive_group=group,
    )


def targeted_trap(reference: list[ImprovementOption]) -> tuple[list[ImprovementOption], dict]:
    # These gains were measured on the unchanged A OD graph in Phase A.
    by_key = {(row.shelter_id, int(round(row.delta_capacity_persons))): row for row in reference}
    choices = [
        ("zs_5151e10626", 60, 11, "V2_TRAP_A"),
        ("zs_737caba8e8", 50, 10, "V2_TRAP_B"),
        ("zs_fed820d94e", 50, 10, "V2_TRAP_C"),
    ]
    rows = []
    for sid, delta, cost, option_id in choices:
        if (sid, delta) not in by_key:
            raise RuntimeError(f"Phase A reference option missing: {sid}/{delta}")
        base = by_key[(sid, delta)]
        rows.append(make_option(option_id, sid, delta, cost))
        if not base.hypothetical:
            raise RuntimeError("Reference capacity option was unexpectedly not hypothetical")
    return rows, {
        "construction": "Three measured single-site capacity gains from Phase A; only hypothetical costs and total budget changed.",
        "measured_gain_targets_persons": {"V2_TRAP_A": 60, "V2_TRAP_B": 50, "V2_TRAP_C": 50},
        "hypothetical_costs": {"V2_TRAP_A": 11, "V2_TRAP_B": 10, "V2_TRAP_C": 10},
        "budget_units": 20,
        "expected_ratio_order": ["V2_TRAP_A", "V2_TRAP_B", "V2_TRAP_C"],
        "provenance": "A demand, shelter, OD and capacities unchanged; all three projects and costs explicitly hypothetical.",
    }


def generate_candidates(data, scenario, reference: list[ImprovementOption]) -> list[dict]:
    rng = random.Random(SEED)
    shelters = [row.shelter_id for row in data.shelters]
    sizes = [8, 16, 24, 32]
    budget_fracs = [0.22, 0.45, 0.72]
    exclusivity = ["low", "medium", "high"]
    records = []
    trap, trap_params = targeted_trap(reference)
    records.append({
        "candidate_id": "REAL-000-TARGETED-TRAP", "seed": SEED,
        "generator_stream_index": 0, "candidate_count": len(trap), "budget_units": 20,
        "profile": "measured_gain_ratio_knapsack_trap", "options": trap,
        "generation_reason": trap_params["construction"], "targeted": True,
        "targeted_parameters": trap_params,
    })
    for index in range(1, 100):
        size = sizes[(index - 1) % len(sizes)]
        profile_index = ((index - 1) // len(sizes)) % 9
        fraction = budget_fracs[profile_index % 3]
        exclusivity_level = exclusivity[profile_index // 3]
        deltas = [10, 20, 30, 40, 50, 60, 80, 100, 150]
        costs = [1, 2, 3, 4, 5, 7, 9, 11, 14]
        options = []
        site_counts: dict[str, int] = {}
        group_ids = [f"XG{n:02d}" for n in range(max(2, size // 4))]
        for slot in range(size):
            sid = rng.choice(shelters)
            site_counts[sid] = site_counts.get(sid, 0) + 1
            if site_counts[sid] > 1:
                policy, group = "stackable", None
            elif exclusivity_level == "high" and rng.random() < 0.65:
                policy, group = "exclusive_group", rng.choice(group_ids)
            elif exclusivity_level == "medium" and rng.random() < 0.35:
                policy, group = "exclusive_group", rng.choice(group_ids)
            else:
                policy, group = "standalone", None
            options.append(make_option(
                f"R{index:03d}_O{slot:02d}", sid, rng.choice(deltas), rng.choice(costs), policy, group,
            ))
        total_cost = sum(row.cost_units for row in options)
        budget = max(min(row.cost_units for row in options), round(total_cost * fraction, 2))
        profile = f"budget_{int(fraction*100)}pct_exclusivity_{exclusivity_level}"
        records.append({
            "candidate_id": f"REAL-{index:03d}", "seed": SEED, "generator_stream_index": index,
            "candidate_count": size, "budget_units": budget,
            "profile": profile, "options": options,
            "generation_reason": "Seeded cost/capacity/group variation over original A shelters; no A graph field changed.",
            "targeted": False,
        })
    return records


def prefilter_record(data, scenario, record, reachable_demand) -> dict:
    options = record["options"]
    budget = record["budget_units"]
    gains = [cheap_upper_gain(row, reachable_demand) for row in options]
    ratio_order = sorted(zip(options, gains), key=lambda pair: (pair[1] / max(pair[0].cost_units, 1e-9), pair[1], pair[0].option_id), reverse=True)
    projected_spend, projected_gain = 0.0, 0.0
    for option, gain in ratio_order:
        if projected_spend + option.cost_units <= budget + 1e-8:
            projected_spend += option.cost_units
            projected_gain += gain
    upper_gain = sum(gains)
    subset_upper = budget_only_subset_upper_bound(options, budget)
    passed = upper_gain > 0 and projected_gain > 0 and subset_upper > 1
    return {
        "candidate_id": record["candidate_id"], "seed": record["seed"],
        "generator_stream_index": record["generator_stream_index"],
        "candidate_count": len(options), "budget_units": budget, "profile": record["profile"],
        "targeted": record["targeted"], "cheap_prefilter_status": "PASS" if passed else "REJECT",
        "filter_reason": "positive reachable-demand upper bound and at least one budget-feasible nonempty subset" if passed else "no estimated reachable gain or no nonempty budget-feasible subset",
        "reachable_demand_gain_upper_bound_sum": upper_gain,
        "ratio_greedy_projection_persons_not_solved": projected_gain,
        "ratio_greedy_projection_cost_units": projected_spend,
        "budget_only_feasible_subset_count_upper_bound_including_empty": subset_upper,
        "selected_for_full_optimization": False,
        "full_optimization_status": "NOT_SELECTED_BY_BOUNDED_PREFILTER",
        "options": [option_dict(row) for row in options],
        "generation_reason": record["generation_reason"],
        "targeted_parameters": record.get("targeted_parameters"),
    }


class CompactAFlow:
    """Exact primary max-flow on a compact view of A; isolates and degree-1 demands are aggregated."""
    def __init__(self, data, scenario):
        self.data, self.scenario = data, scenario
        self.demand = {row.demand_id: int(round(row.pop_est * scenario.population_demand_ratio * FLOW_SCALE)) for row in data.demands}
        self.shelter_caps = {row.shelter_id: int(round(row.capacity_persons * FLOW_SCALE)) for row in data.shelters}
        self.legal_by_demand: dict[str, list[str]] = {}
        for edge in data.od_edges:
            if (edge.reachable and edge.time_min is not None and edge.time_min <= scenario.max_walk_time_min
                    and edge.shelter_id not in scenario.disabled_shelter_ids):
                self.legal_by_demand.setdefault(edge.demand_id, []).append(edge.shelter_id)
        self.multi = {did: sids for did, sids in self.legal_by_demand.items() if len(sids) > 1}
        self.degree_one: dict[str, int] = {}
        for did, sids in self.legal_by_demand.items():
            if len(sids) == 1:
                self.degree_one[sids[0]] = self.degree_one.get(sids[0], 0) + self.demand.get(did, 0)
        self.rounding_error_upper_bound_persons = (len(data.demands) + len(data.shelters)) / (2 * FLOW_SCALE)

    def __call__(self, deltas: dict[str, float] | None = None) -> dict:
        deltas = deltas or {}
        caps = {sid: max(0, amount + int(round(deltas.get(sid, 0.0) * FLOW_SCALE))) for sid, amount in self.shelter_caps.items()}
        source, sink = "__src__", "__sink__"
        graph = nx.DiGraph()
        graph.add_node(source); graph.add_node(sink)
        total = sum(self.demand.values())
        for sid, amount in self.degree_one.items():
            graph.add_edge(source, f"s:{sid}", capacity=amount)
        for did, sids in self.multi.items():
            amount = self.demand.get(did, 0)
            if amount <= 0:
                continue
            node = f"d:{did}"
            graph.add_edge(source, node, capacity=amount)
            for sid in sids:
                graph.add_edge(node, f"s:{sid}", capacity=total)
        for sid, capacity in caps.items():
            graph.add_edge(f"s:{sid}", sink, capacity=capacity)
        value = nx.maximum_flow_value(graph, source, sink, flow_func=nx.algorithms.flow.dinitz)
        return {"served_demand": value / FLOW_SCALE, "scaled_flow_units": int(value),
                "rounding_error_upper_bound_persons": self.rounding_error_upper_bound_persons}


def exact_flow_greedy(data, scenario, options, budget) -> dict:
    active = [row for row in options if row.eligible and row.shelter_id not in scenario.disabled_shelter_ids]
    selected: list[ImprovementOption] = []
    spent = 0.0
    flow = CompactAFlow(data, scenario)
    current = flow()
    trace = []
    remaining = active.copy()
    while remaining:
        choices = []
        for option in remaining:
            if spent + option.cost_units > budget + 1e-8:
                continue
            trial = selected + [option]
            if not valid_option_set(tuple(trial)):
                continue
            deltas: dict[str, float] = {}
            for row in trial:
                deltas[row.shelter_id] = deltas.get(row.shelter_id, 0.0) + row.delta_capacity_persons
            result = flow(deltas)
            gain = result["served_demand"] - current["served_demand"]
            choices.append((gain / max(option.cost_units, 1e-9), gain, option.option_id, option, result))
        if not choices:
            break
        _ratio, gain, _oid, option, best = max(choices, key=lambda row: (row[0], row[1], row[2]))
        if gain <= 1e-8:
            break
        selected.append(option); remaining.remove(option); spent += option.cost_units
        trace.append({"selected_option_id": option.option_id, "marginal_gain_persons": gain,
                      "gain_per_cost": gain / max(option.cost_units, 1e-9), "served_after": best["served_demand"]})
        current = best
    deltas = {}
    for row in selected:
        deltas[row.shelter_id] = deltas.get(row.shelter_id, 0.0) + row.delta_capacity_persons
    assignment = engine.solve_assignment(data, scenario, deltas)
    cert = verify_assignment(data, scenario, assignment, deltas)
    return {
        "engine": "same Module B gain/cost/tie-break and combination rules; independent max-flow used to evaluate primary service gains",
        "served_demand": assignment.served_demand, "selected_option_ids": [row.option_id for row in selected],
        "cost_units": spent, "verification_status": cert.verification_status,
        "exact_b_primary_greedy": False, "selection_trace": trace,
    }


def compact_real_oracle_input(data, scenario):
    """Contract isolated and degree-one demand nodes without changing max-served flow."""
    demand_by_id = {row.demand_id: row for row in data.demands}
    eligible = [edge for edge in data.od_edges if edge.reachable and edge.time_min is not None
                and edge.time_min <= scenario.max_walk_time_min
                and edge.shelter_id not in scenario.disabled_shelter_ids]
    by_demand: dict[str, list[ODEdge]] = {}
    for edge in eligible:
        by_demand.setdefault(edge.demand_id, []).append(edge)
    single_by_shelter: dict[str, float] = {}
    multi_ids = set()
    for did, rows in by_demand.items():
        if len(rows) == 1:
            sid = rows[0].shelter_id
            single_by_shelter[sid] = single_by_shelter.get(sid, 0.0) + demand_by_id[did].pop_est
        elif len(rows) > 1:
            multi_ids.add(did)
    compact_demands = [DemandNode(demand_id=did, pop_est=demand_by_id[did].pop_est,
                                  population_status=demand_by_id[did].population_status)
                       for did in sorted(multi_ids)]
    compact_demands.extend(DemandNode(demand_id=f"__V2_DEG1_{sid}", pop_est=amount,
                                      population_status="aggregated_degree_one")
                           for sid, amount in sorted(single_by_shelter.items()))
    compact_edges = [row for row in eligible if row.demand_id in multi_ids]
    compact_edges.extend(ODEdge(demand_id=f"__V2_DEG1_{sid}", shelter_id=sid,
                                time_min=0.0, distance_m=0.0, reachable=True, reason="ok")
                         for sid in sorted(single_by_shelter))
    compact = PlanningInput(
        data_version=data.data_version, data_status=data.data_status, demands=compact_demands,
        shelters=data.shelters, od_edges=compact_edges, source_manifest_hash=data.source_manifest_hash,
        source_label="A_GIS_ANCHORED_EXACT_FLOW_PROJECTION",
    )
    return compact, {
        "projection": "isolated demand nodes removed and all degree-one demand nodes aggregated by sole reachable shelter; multi-edge demand nodes/OD edges and all shelters/capacities retained",
        "original_demand_nodes": len(data.demands), "projected_demand_nodes": len(compact_demands),
        "original_eligible_edges": len(eligible), "projected_eligible_edges": len(compact_edges),
        "isolated_demand_nodes_removed": len(data.demands) - len(by_demand),
        "degree_one_demand_nodes_aggregated": sum(1 for rows in by_demand.values() if len(rows) == 1),
        "max_served_objective_preserved": True,
        "rounding_error_upper_bound_persons": (len(data.demands) + len(data.shelters)) / (2 * 1_000_000),
    }


def exact_real_expansion_oracle(data, scenario, options):
    compact, projection = compact_real_oracle_input(data, scenario)
    result = expansion_oracle(compact, scenario, options)
    result["original_a_input_projection"] = projection
    result["rounding_error_upper_bound_persons"] = projection["rounding_error_upper_bound_persons"]
    result["method"] += "; exact degree-one/isolated-demand contraction of the unchanged A graph"
    return result


def run_official_greedy(data, scenario, options) -> dict:
    result = run_baseline("", "GreedyExpansion", data=data, scenario=scenario, options=options)
    result["engine"] = "backend.evaluation.baselines.run_baseline.GreedyExpansion"
    result["exact_b_primary_greedy"] = True
    return result


def solve_with_bound_trace(data, scenario, options, budget, time_limit: float) -> tuple[dict | None, list[dict], float, str | None]:
    original_limit = engine.MAX_BUDGET_SOLVE_SECONDS
    original_milp = engine.milp
    calls: list[dict] = []

    def traced_milp(*args, **kwargs):
        started = time.perf_counter()
        result = original_milp(*args, **kwargs)
        objective = kwargs.get("c", args[0] if args else None)
        calls.append({
            "call_index": len(calls) + 1, "elapsed_seconds": time.perf_counter() - started,
            "status_code": int(result.status), "success": bool(result.success),
            "message": str(result.message), "objective_value_minimization": getattr(result, "fun", None),
            "best_bound_minimization": getattr(result, "mip_dual_bound", None),
            "relative_gap": getattr(result, "mip_gap", None), "node_count": getattr(result, "mip_node_count", None),
            "time_limit_seconds": (kwargs.get("options") or {}).get("time_limit"),
            "variable_count": len(objective) if objective is not None else None,
        })
        return result

    engine.MAX_BUDGET_SOLVE_SECONDS = float(time_limit)
    engine.milp = traced_milp
    started = time.perf_counter()
    result, error = None, None
    try:
        result = engine.optimize_budgeted(data, scenario, options, budget)
    except Exception as exc:  # preserve timeout/no-incumbent evidence
        error = f"{type(exc).__name__}: {exc}"
    finally:
        elapsed = time.perf_counter() - started
        engine.milp = original_milp
        engine.MAX_BUDGET_SOLVE_SECONDS = original_limit
    return result, calls, elapsed, error


def main() -> None:
    data = load_source()
    phase_a = json.loads(PHASE_A.read_text(encoding="utf-8"))
    case_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ref_case = next(row for row in case_manifest["cases"] if row["case_id"] == "B08-01")
    reference = load_options(ref_case)
    scenario_base = PlanningScenario(
        scenario_id="v2-grounded-search-base", data_version=data.data_version,
        analysis_mode="demo", population_demand_ratio=0.20,
        max_walk_time_min=15.0, budget_units=20.0,
    )
    hashes_before = source_hashes()
    generated = generate_candidates(data, scenario_base, reference)
    reachable_demand = reachable_demand_by_shelter(data, scenario_base)
    screening = [prefilter_record(data, scenario_base, record, reachable_demand) for record in generated]
    # Deliberately stratify by size and retain all targeted designs. No more than 30 MIPs.
    shortlist_ids = {"REAL-000-TARGETED-TRAP"}
    for size in (8, 16, 24, 32):
        group = [row for row in screening if row["candidate_count"] == size]
        group.sort(key=lambda row: (row["ratio_greedy_projection_persons_not_solved"],
                                    row["reachable_demand_gain_upper_bound_sum"],
                                    -row["budget_units"]), reverse=True)
        shortlist_ids.update(row["candidate_id"] for row in group[:2])
    shortlist = [row for row in screening if row["candidate_id"] in shortlist_ids and row["cheap_prefilter_status"] == "PASS"]
    shortlist.sort(key=lambda row: (not row["targeted"], row["candidate_count"], row["candidate_id"]))
    if len(shortlist) > MAX_FULL_MIP:
        shortlist = shortlist[:MAX_FULL_MIP]
    selected_ids = {row["candidate_id"] for row in shortlist}
    for row in screening:
        if row["candidate_id"] in selected_ids:
            row["selected_for_full_optimization"] = True
            row["full_optimization_status"] = "SELECTED_FOR_FULL_OPTIMIZATION"
    records_by_id = {record["candidate_id"]: record for record in generated}
    case_results = []
    all_mip_count = 0
    for index, screen in enumerate(shortlist, 1):
        record = records_by_id[screen["candidate_id"]]
        options = record["options"]
        scenario = scenario_base.model_copy(update={
            "scenario_id": f"v2-grounded-{record['candidate_id']}",
            "budget_units": record["budget_units"],
        })
        # Use the production Greedy for candidate sets up to 8; larger ones use a primary-objective-equivalent flow evaluator.
        greedy = run_official_greedy(data, scenario, options) if len(options) <= 8 else exact_flow_greedy(data, scenario, options, record["budget_units"])
        mip, trace, runtime, error = solve_with_bound_trace(data, scenario, options, record["budget_units"], 10.0)
        all_mip_count += 1
        verification = verify_budgeted(data, scenario, options, mip, record["budget_units"]) if mip else None
        primary_call = trace[0] if trace else {}
        primary_bound = primary_call.get("best_bound_minimization")
        maximization_bound = -primary_bound if isinstance(primary_bound, (int, float)) else None
        oracle = None
        if mip and mip.get("optimality_proven") and isinstance(greedy.get("served_demand"), (int, float)) and mip["objective_value"] > greedy["served_demand"] + TOL:
            if len(options) <= 12:
                oracle = exact_real_expansion_oracle(data, scenario, options)
            else:
                oracle = {"oracle_status": "NOT_RUN_CANDIDATE_SET_OVER_12", "reason": "Independent exhaustive proof bounded to at most 12 candidates in this real-graph search."}
        gap = None if not mip or not isinstance(greedy.get("served_demand"), (int, float)) else mip["objective_value"] - greedy["served_demand"]
        row = {
            "candidate_id": record["candidate_id"], "seed": record["seed"], "candidate_count": len(options),
            "profile": record["profile"], "budget_units": record["budget_units"],
            "data_status": data.data_status, "source_label": data.source_label,
            "source_manifest_sha256": data.source_manifest_hash,
            "hypothetical_options_only": all(option.hypothetical and option.source_status.startswith("HYPOTHETICAL") for option in options),
            "greedy": greedy, "mip": mip, "mip_error": error, "mip_runtime_seconds": runtime,
            "mip_primary_best_bound_served_demand": maximization_bound,
            "mip_primary_relative_gap": primary_call.get("relative_gap"),
            "mip_stage_trace": trace,
            "verifier": verification,
            "oracle": oracle,
            "mip_minus_greedy_persons": gap,
            "greedy_failure_confirmed": bool(mip and mip.get("optimality_proven") and verification and verification["verification_status"] == "PASS_DEMO" and oracle and oracle.get("oracle_status") == "ENUMERATED_MAXFLOW_PROVEN" and oracle["objective_served_demand"] > greedy["served_demand"] + oracle["rounding_error_upper_bound_persons"] + TOL),
            "filter_record": {k: v for k, v in screen.items() if k not in {"options", "generation_reason", "targeted_parameters"}},
        }
        case_results.append(row)
        write_json(OUT / "runs" / "real_gis_search" / f"{record['candidate_id']}.json", {
            "scenario": scenario.model_dump(), "options": [option_dict(item) for item in options],
            "generation": record.get("targeted_parameters") or {"reason": record["generation_reason"]},
            "result": row,
        })
        print(json.dumps({"candidate_id": row["candidate_id"], "n": row["candidate_count"],
                          "greedy": greedy.get("served_demand"), "mip": mip.get("objective_value") if mip else None,
                          "status": mip.get("solver_status") if mip else error, "seconds": runtime,
                          "oracle": oracle.get("oracle_status") if oracle else "NOT_REQUIRED"}, ensure_ascii=False))
    hashes_after = source_hashes()
    unchanged = hashes_before == hashes_after
    search_document = {
        "phase": "B_GROUNDED_SEARCH", "generator_version": "v2-grounded-0.1.0", "seed": SEED,
        "created_utc": datetime.now(timezone.utc).isoformat(), "data_version": data.data_version,
        "data_status": data.data_status, "source_label": data.source_label,
        "source_manifest_sha256": data.source_manifest_hash,
        "source_hashes_before": hashes_before, "source_hashes_after": hashes_after,
        "source_hashes_unchanged": unchanged,
        "limits": {"cheap_prefilters_requested": 100, "cheap_prefilters_run": len(screening), "full_mip_limit": MAX_FULL_MIP, "full_mips_run": all_mip_count, "per_instance_seconds": 10},
        "prefilter_summary": {"pass": sum(row["cheap_prefilter_status"] == "PASS" for row in screening), "rejected": sum(row["cheap_prefilter_status"] == "REJECT" for row in screening), "selected_for_mip": len(shortlist)},
        "screening_records": screening,
        "completed_cases": case_results,
        "confirmed_greedy_failures": [row["candidate_id"] for row in case_results if row["greedy_failure_confirmed"]],
        "all_mip_runs_within_limit": all_mip_count <= MAX_FULL_MIP,
        "limitations": ["All new capacities and costs are hypothetical; A GIS nodes, site capacities and OD edges were loaded unchanged.", "For candidate sets over 8, the comparative greedy is the same primary-objective ratio/tie-break algorithm evaluated by independent max-flow, not a second call to the slower LP-based B baseline.", "Independent exhaustive proof is applied only where the MIP result shows a material primary-objective gap and the option count is at most 12."],
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "numpy": __import__("numpy").__version__, "scipy": __import__("scipy").__version__, "networkx": __import__("networkx").__version__},
    }
    write_json(OUT / "oracles" / "phase_b_grounded_search.json", search_document)
    (OUT / "scenarios" / "real_gis_search" / "screening_records.json").write_text(json.dumps(screening, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "scenarios" / "real_gis_search" / "selected_candidates.json").write_text(json.dumps([{"candidate_id": row["candidate_id"], "options": [option_dict(item) for item in records_by_id[row["candidate_id"]]["options"]], "budget_units": records_by_id[row["candidate_id"]]["budget_units"]} for row in shortlist], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"screened": len(screening), "passed": search_document["prefilter_summary"]["pass"], "full_mips": all_mip_count,
                      "confirmed_greedy_failures": search_document["confirmed_greedy_failures"], "source_hashes_unchanged": unchanged}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
