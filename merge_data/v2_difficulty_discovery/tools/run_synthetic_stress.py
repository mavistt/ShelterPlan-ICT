"""Run segregated synthetic solver stress and one-factor ablation experiments."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import random
import sys
import threading
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "merge_data" / "tools"))

import backend.engine as engine
from backend.models import DemandNode, ImprovementOption, ODEdge, PlanningInput, PlanningScenario, Shelter
from backend.verifier.checks import verify_assignment, verify_budgeted
from grounded_experiment_runner import expansion_oracle, maxflow_service, valid_option_set

OUT = ROOT / "merge_data" / "v2_difficulty_discovery"
SEED = 20261114
SIZES = (8, 16, 32, 48, 64)
LIMITS = (2.0, 10.0)

try:
    import psutil
except ImportError:  # pragma: no cover - environment dependent
    psutil = None


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def synthetic_option(option_id: str, shelter_id: str, delta: int, cost: int,
                     policy: str = "standalone", group: str | None = None) -> ImprovementOption:
    return ImprovementOption(
        option_id=option_id, shelter_id=shelter_id, action="synthetic_capacity_expansion",
        delta_capacity_persons=float(delta), cost_units=float(cost), eligible=True,
        source_status="FULLY_SYNTHETIC_STRESS", hypothetical=True, human_confirmed=False,
        combination_policy=policy, exclusive_group=group,
    )


def make_stress_case(size: int, kind: str, seed: int) -> tuple[PlanningInput, PlanningScenario, list[ImprovementOption], dict]:
    rng = random.Random(seed)
    data_version = f"synthetic-v2-{kind}-{size}-{seed}"
    if kind == "simple":
        shelter_count = size
        demand_count = size * 2
        shelters = [Shelter(shelter_id=f"S{j:03d}", capacity_persons=20, verified_status="synthetic") for j in range(shelter_count)]
        demands = [DemandNode(demand_id=f"D{j:04d}", pop_est=100, population_status="synthetic") for j in range(demand_count)]
        edges = []
        for site in range(shelter_count):
            for k in range(2):
                did = f"D{site*2+k:04d}"
                edges.append(ODEdge(demand_id=did, shelter_id=f"S{site:03d}", time_min=2.0 + k, distance_m=100.0, reachable=True, reason="ok"))
        options = []
        for j in range(size):
            delta = rng.randint(15, 85)
            cost = 5
            options.append(synthetic_option(f"O{j:03d}", f"S{j:03d}", delta, cost))
        budget = float((size // 2) * 5)
        metadata = {"case_kind": kind, "candidate_count": size, "demand_nodes": demand_count,
                    "shelters": shelter_count, "od_edges": len(edges), "overlap": "none; each demand has degree 1",
                    "budget": budget, "exclusive_groups": 0, "generation_seed": seed}
    else:
        shelter_count = max(4, size // 2)
        demand_count = max(32, size * 2)
        shelters = [Shelter(shelter_id=f"S{j:03d}", capacity_persons=rng.randint(8, 24), verified_status="synthetic") for j in range(shelter_count)]
        demands = [DemandNode(demand_id=f"D{j:04d}", pop_est=rng.randint(25, 65), population_status="synthetic") for j in range(demand_count)]
        edges = []
        degree = min(6, max(3, shelter_count // 4))
        for did in range(demand_count):
            selected_shelters = rng.sample(range(shelter_count), degree)
            for offset, site in enumerate(selected_shelters):
                edges.append(ODEdge(demand_id=f"D{did:04d}", shelter_id=f"S{site:03d}", time_min=2.0 + ((site + offset) % 12), distance_m=100.0, reachable=True, reason="ok"))
        options = []
        for j in range(size):
            site = j % shelter_count
            delta = rng.randint(25, 90)
            cost = rng.randint(2, 15)
            options.append(synthetic_option(f"O{j:03d}", f"S{site:03d}", delta, cost, "exclusive_group", f"ALT_S{site:03d}"))
        budget = float(round(sum(row.cost_units for row in options) * 0.45))
        metadata = {"case_kind": kind, "candidate_count": size, "demand_nodes": demand_count,
                    "shelters": shelter_count, "od_edges": len(edges), "mean_od_degree": len(edges) / demand_count,
                    "overlap": "random multi-shelter demand neighborhoods with 2 alternatives per shelter",
                    "budget": budget, "exclusive_groups": shelter_count, "generation_seed": seed}
    data = PlanningInput(
        data_version=data_version, data_status="SYNTHETIC_FIXTURE", demands=demands,
        shelters=shelters, od_edges=edges, source_manifest_hash=None,
        source_label="FULLY_SYNTHETIC_STRESS",
    )
    scenario = PlanningScenario(
        scenario_id=f"{kind}-{size}-{seed}", data_version=data_version,
        analysis_mode="demo", population_demand_ratio=1.0,
        max_walk_time_min=15.0, budget_units=budget,
    )
    metadata["data_status"] = "SYNTHETIC_FIXTURE"
    metadata["source_label"] = "FULLY_SYNTHETIC_STRESS"
    metadata["option_provenance"] = "all options, costs and capacities are hypothetical synthetic values"
    return data, scenario, options, metadata


def make_greedy_trap(seed: int = SEED) -> tuple[PlanningInput, PlanningScenario, list[ImprovementOption], dict]:
    shelters = [Shelter(shelter_id=f"T{j}", capacity_persons=0, verified_status="synthetic") for j in range(8)]
    demands = [DemandNode(demand_id=f"TD{j}", pop_est=100, population_status="synthetic") for j in range(8)]
    edges = [ODEdge(demand_id=f"TD{j}", shelter_id=f"T{j}", time_min=1, distance_m=1, reachable=True, reason="ok") for j in range(8)]
    options = [
        synthetic_option("A", "T0", 61, 12),
        synthetic_option("B", "T1", 50, 10),
        synthetic_option("C", "T2", 50, 10),
    ] + [synthetic_option(f"D{j}", f"T{j+3}", 1, 1) for j in range(5)]
    version = f"synthetic-v2-greedy-trap-{seed}"
    data = PlanningInput(data_version=version, data_status="SYNTHETIC_FIXTURE", demands=demands,
                         shelters=shelters, od_edges=edges, source_label="FULLY_SYNTHETIC_STRESS")
    scenario = PlanningScenario(scenario_id="synthetic-greedy-trap", data_version=version,
                                analysis_mode="demo", population_demand_ratio=1,
                                max_walk_time_min=15, budget_units=20)
    return data, scenario, options, {
        "case_kind": "greedy_counterexample", "candidate_count": 8, "demand_nodes": 8,
        "shelters": 8, "od_edges": 8, "overlap": "none; one demand per shelter",
        "budget": 20, "generation_seed": seed,
        "hand_check": "A ratio=61/12>50/10 for B/C; after A, neither B nor C fits. Greedy can add five 1-person decoys. B+C serves 100.",
        "source_label": "FULLY_SYNTHETIC_STRESS",
    }


def greedy_primary(data, scenario, options, budget: float) -> dict:
    active = [row for row in options if row.eligible]
    selected: list[ImprovementOption] = []
    remaining = active.copy()
    spent = 0.0
    current = maxflow_service(data, scenario)
    trace = []
    while remaining:
        choices = []
        for option in remaining:
            if spent + option.cost_units > budget + 1e-8:
                continue
            trial = tuple(selected + [option])
            if not valid_option_set(trial):
                continue
            deltas = {}
            for row in trial:
                deltas[row.shelter_id] = deltas.get(row.shelter_id, 0.0) + row.delta_capacity_persons
            value = maxflow_service(data, scenario, deltas)
            gain = value["served_demand"] - current["served_demand"]
            choices.append((gain / max(option.cost_units, 1e-9), gain, option.option_id, option, value))
        if not choices:
            break
        _ratio, gain, _oid, option, best = max(choices, key=lambda row: (row[0], row[1], row[2]))
        if gain <= 1e-8:
            break
        selected.append(option); remaining.remove(option); spent += option.cost_units
        trace.append({"option_id": option.option_id, "marginal_gain_persons": gain,
                      "gain_per_cost": gain / max(option.cost_units, 1e-9), "served_after": best["served_demand"]})
        current = best
    deltas = {}
    for row in selected:
        deltas[row.shelter_id] = deltas.get(row.shelter_id, 0.0) + row.delta_capacity_persons
    assignment = engine.solve_assignment(data, scenario, deltas)
    verifier = verify_assignment(data, scenario, assignment, deltas)
    return {"served_demand": assignment.served_demand, "selected_option_ids": [row.option_id for row in selected],
            "cost_units": spent, "verification_status": verifier.verification_status,
            "engine": "same primary gain/cost/tie-break and policy rules; independent max-flow evaluates primary gains",
            "exact_b_primary_greedy": False, "selection_trace": trace}


@contextmanager
def memory_sampler():
    info = {"rss_before_bytes": None, "rss_after_bytes": None, "peak_rss_sampled_bytes": None,
            "python_heap_peak_bytes": None, "measurement": "psutil 10ms RSS sampling and tracemalloc; native HiGHS allocations captured only by RSS sampling"}
    process = psutil.Process() if psutil else None
    stop = threading.Event()
    peak = [0]
    if process:
        info["rss_before_bytes"] = process.memory_info().rss
        peak[0] = info["rss_before_bytes"]
    def sample():
        if process:
            while not stop.is_set():
                try:
                    peak[0] = max(peak[0], process.memory_info().rss)
                except Exception:
                    pass
                stop.wait(0.01)
    worker = threading.Thread(target=sample, daemon=True)
    tracemalloc.start()
    worker.start()
    try:
        yield info
    finally:
        stop.set(); worker.join(timeout=0.5)
        _current, python_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        info["python_heap_peak_bytes"] = python_peak
        if process:
            info["rss_after_bytes"] = process.memory_info().rss
            info["peak_rss_sampled_bytes"] = peak[0]
        else:
            info["measurement"] = "psutil unavailable; tracemalloc captures Python heap only, not native solver allocations"


def solve_mip(data, scenario, options, limit):
    original_limit, original_milp = engine.MAX_BUDGET_SOLVE_SECONDS, engine.milp
    calls = []
    def traced(*args, **kwargs):
        started = time.perf_counter()
        result = original_milp(*args, **kwargs)
        objective = kwargs.get("c", args[0] if args else None)
        calls.append({
            "stage_index": len(calls) + 1, "runtime_seconds": time.perf_counter() - started,
            "status_code": int(result.status), "success": bool(result.success), "message": str(result.message),
            "objective_value_minimization": getattr(result, "fun", None),
            "best_bound_minimization": getattr(result, "mip_dual_bound", None),
            "relative_gap": getattr(result, "mip_gap", None), "node_count": getattr(result, "mip_node_count", None),
            "time_limit_seconds": (kwargs.get("options") or {}).get("time_limit"),
            "variable_count": len(objective) if objective is not None else None,
        })
        return result
    engine.MAX_BUDGET_SOLVE_SECONDS = float(limit)
    engine.milp = traced
    started = time.perf_counter()
    result, error = None, None
    memory = {}
    try:
        with memory_sampler() as memory:
            result = engine.optimize_budgeted(data, scenario, options, scenario.budget_units)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        runtime = time.perf_counter() - started
        engine.milp = original_milp
        engine.MAX_BUDGET_SOLVE_SECONDS = original_limit
    return result, calls, runtime, error, memory


def independent_reference(data, scenario, options):
    if len(options) <= 8:
        return expansion_oracle(data, scenario, options)
    deltas = {}
    for row in options:
        deltas[row.shelter_id] = deltas.get(row.shelter_id, 0.0) + row.delta_capacity_persons
    upper = maxflow_service(data, scenario, deltas)
    return {"oracle_status": "VALID_UPPER_BOUND_ALL_OPTIONS_IGNORE_BUDGET_AND_EXCLUSIVITY",
            "objective_upper_bound_served_demand": upper["served_demand"],
            "rounding_error_upper_bound_persons": upper["rounding_error_upper_bound_persons"],
            "method": "independent NetworkX Dinitz max-flow with all expansions; ignores budget and option exclusions, so it is a valid upper bound"}


def case_document(data, scenario, options, metadata):
    return {"provenance": "FULLY_SYNTHETIC_STRESS", "metadata": metadata,
            "planning_input": data.model_dump(), "scenario": scenario.model_dump(),
            "options": [row.model_dump() for row in options]}


def run_one(data, scenario, options, metadata, limit, greedy, oracle):
    result, calls, runtime, error, memory = solve_mip(data, scenario, options, limit)
    verifier = verify_budgeted(data, scenario, options, result, scenario.budget_units) if result else None
    first = calls[0] if calls else {}
    bound = -first["best_bound_minimization"] if isinstance(first.get("best_bound_minimization"), (int, float)) else None
    return {
        "case_id": f"SYN-{metadata['case_kind']}-N{metadata['candidate_count']}-TL{int(limit)}",
        "provenance": "FULLY_SYNTHETIC_STRESS", "case_kind": metadata["case_kind"],
        "candidate_count": len(options), "time_limit_seconds": limit,
        "solver_status": result.get("solver_status") if result else "SOLVER_TIMEOUT_OR_ERROR",
        "optimality_proven": bool(result and result.get("optimality_proven")),
        "best_feasible_served_demand": result.get("objective_value") if result else None,
        "primary_best_bound_served_demand": bound,
        "primary_relative_gap": first.get("relative_gap"),
        "runtime_seconds": runtime, "stage_trace": calls,
        "lexicographic_stages": result.get("mip_stages") if result else [],
        "memory": memory, "verifier": verifier, "greedy": greedy,
        "mip_minus_greedy_persons": (result["objective_value"] - greedy["served_demand"]) if result and greedy.get("served_demand") is not None else None,
        "oracle": oracle, "error": error,
        "metadata": metadata,
    }


def alter_for_ablation(base, dimension):
    data, scenario, options, meta = base
    demands, shelters, edges = list(data.demands), list(data.shelters), list(data.od_edges)
    new_options = list(options)
    new_budget = scenario.budget_units
    change_description = ""
    if dimension == "loosen_budget":
        new_budget = float(round(new_budget * 1.8))
        change_description = "Budget increased by 80%; all graph and option values fixed."
    elif dimension == "reduce_exclusivity":
        new_options = [row.model_copy(update={"combination_policy": "stackable", "exclusive_group": None}) for row in options]
        change_description = "All named groups and same-shelter non-stacking restrictions removed by changing policy to stackable; graph, costs, deltas and budget fixed."
    elif dimension == "increase_overlap":
        # Preserve edge count/degree while rewiring endpoints to the same service hubs.
        rng = random.Random(SEED + 701)
        shelter_ids = [row.shelter_id for row in shelters]
        hubs = shelter_ids[:max(2, len(shelter_ids) // 4)]
        by_demand: dict[str, list[ODEdge]] = {}
        for edge in edges:
            by_demand.setdefault(edge.demand_id, []).append(edge)
        rewired = []
        for did, rows in by_demand.items():
            degree = len(rows)
            choice = rng.sample(hubs, min(degree, len(hubs)))
            if len(choice) < degree:
                others = [sid for sid in shelter_ids if sid not in choice]
                choice += rng.sample(others, degree - len(choice))
            for original_edge, sid in zip(rows, choice):
                rewired.append(ODEdge(demand_id=did, shelter_id=sid, time_min=original_edge.time_min,
                                      distance_m=original_edge.distance_m, reachable=True, reason="ok"))
        edges = rewired
        change_description = "OD edge count and every demand degree preserved; shelter endpoints rewired to create more shared neighborhoods."
    elif dimension == "increase_candidates":
        current = len(options)
        rng = random.Random(SEED + 702)
        shelter_ids = [row.shelter_id for row in shelters]
        for j in range(16):
            sid = shelter_ids[j % len(shelter_ids)]
            new_options.append(synthetic_option(f"ABL_EXTRA_{j:02d}", sid, rng.randint(20, 80), rng.randint(2, 15), "exclusive_group", f"ALT_{sid}"))
        change_description = f"Added 16 options ({current}→{len(new_options)}); graph and original options fixed; budget fixed in absolute units."
    elif dimension == "increase_demand_nodes":
        existing = len(demands)
        rng = random.Random(SEED + 703)
        rng.shuffle(shelters)
        shelters = list(data.shelters)
        shelter_ids = [row.shelter_id for row in shelters]
        for j in range(32):
            did = f"ABL_D{j:03d}"
            demands.append(DemandNode(demand_id=did, pop_est=rng.randint(25, 65), population_status="synthetic"))
            for site in rng.sample(shelter_ids, 3):
                edges.append(ODEdge(demand_id=did, shelter_id=site, time_min=3.0, distance_m=100, reachable=True, reason="ok"))
        change_description = f"Added 32 demand nodes ({existing}→{len(demands)}); existing nodes/edges/options/budget fixed."
    elif dimension == "increase_od_density":
        shelter_ids = [row.shelter_id for row in shelters]
        by_demand: dict[str, set[str]] = {}
        for edge in edges:
            by_demand.setdefault(edge.demand_id, set()).add(edge.shelter_id)
        rng = random.Random(SEED + 704)
        extra = []
        for did, existing_sites in by_demand.items():
            target = min(len(shelter_ids), len(existing_sites) + 3)
            candidates = [sid for sid in shelter_ids if sid not in existing_sites]
            for sid in rng.sample(candidates, max(0, target - len(existing_sites))):
                extra.append(ODEdge(demand_id=did, shelter_id=sid, time_min=4.0, distance_m=100, reachable=True, reason="ok"))
        edges.extend(extra)
        change_description = f"Added {len(extra)} legal edges (+3 degree per demand where possible); nodes, options and budget fixed."
    else:
        raise ValueError(dimension)
    new_data = PlanningInput(data_version=f"{data.data_version}-{dimension}", data_status="SYNTHETIC_FIXTURE",
                             demands=demands, shelters=shelters, od_edges=edges,
                             source_label="FULLY_SYNTHETIC_STRESS")
    new_scenario = scenario.model_copy(update={"scenario_id": f"ablation-{dimension}",
                                               "data_version": new_data.data_version,
                                               "budget_units": new_budget})
    new_meta = {**meta, "ablation_dimension": dimension, "change_description": change_description,
                "budget": new_budget, "demand_nodes": len(demands), "shelters": len(shelters),
                "od_edges": len(edges), "candidate_count": len(new_options),
                "source_label": "FULLY_SYNTHETIC_STRESS"}
    return new_data, new_scenario, new_options, new_meta


def main() -> None:
    stress_cases = []
    for kind in ("simple", "competitive"):
        for size in SIZES:
            seed = SEED + size * (1 if kind == "simple" else 100) + (0 if kind == "simple" else 17)
            data, scenario, options, metadata = make_stress_case(size, kind, seed)
            fixture = case_document(data, scenario, options, metadata)
            fixture_path = OUT / "scenarios" / "synthetic_stress" / f"{kind}_n{size}.json"
            write_json(fixture_path, fixture)
            greedy = greedy_primary(data, scenario, options, scenario.budget_units)
            oracle = independent_reference(data, scenario, options)
            for limit in LIMITS:
                stress_cases.append(run_one(data, scenario, options, metadata, limit, greedy, oracle))
                write_json(OUT / "runs" / "synthetic_stress" / f"{stress_cases[-1]['case_id']}.json",
                           {"fixture_path": str(fixture_path.relative_to(OUT)), "result": stress_cases[-1]})
                row = stress_cases[-1]
                print(json.dumps({"case": row["case_id"], "status": row["solver_status"],
                                  "objective": row["best_feasible_served_demand"], "bound": row["primary_best_bound_served_demand"],
                                  "gap": row["primary_relative_gap"], "seconds": row["runtime_seconds"],
                                  "verify": (row["verifier"] or {}).get("verification_status")}, ensure_ascii=False))

    trap_data, trap_scenario, trap_options, trap_meta = make_greedy_trap()
    trap_fixture = case_document(trap_data, trap_scenario, trap_options, trap_meta)
    trap_path = OUT / "scenarios" / "synthetic_stress" / "greedy_counterexample_n8.json"
    write_json(trap_path, trap_fixture)
    trap_greedy = greedy_primary(trap_data, trap_scenario, trap_options, trap_scenario.budget_units)
    trap_oracle = independent_reference(trap_data, trap_scenario, trap_options)
    trap_result = run_one(trap_data, trap_scenario, trap_options, trap_meta, 10.0, trap_greedy, trap_oracle)
    write_json(OUT / "runs" / "synthetic_stress" / "SYN-greedy-counterexample.json", trap_result)
    print(json.dumps({"case": "SYN-greedy-counterexample", "greedy": trap_greedy["served_demand"],
                      "mip": trap_result["best_feasible_served_demand"], "oracle": trap_oracle.get("objective_served_demand"),
                      "status": trap_result["solver_status"]}, ensure_ascii=False))

    base = make_stress_case(16, "competitive", SEED + 16 * 100 + 17)
    base_data, base_scenario, base_options, base_meta = base
    ablations = []
    base_greedy = greedy_primary(base_data, base_scenario, base_options, base_scenario.budget_units)
    base_result = run_one(base_data, base_scenario, base_options, base_meta, 10.0, base_greedy,
                          independent_reference(base_data, base_scenario, base_options))
    base_result["case_id"] = "ABL-BASE-COMPETITIVE-16"
    ablations.append(base_result)
    write_json(OUT / "runs" / "ablation" / "ABL-BASE-COMPETITIVE-16.json", {"result": base_result, "fixture": case_document(*base)})
    dimensions = ("loosen_budget", "reduce_exclusivity", "increase_overlap", "increase_candidates", "increase_demand_nodes", "increase_od_density")
    for dimension in dimensions:
        case = alter_for_ablation(base, dimension)
        data, scenario, options, metadata = case
        greedy = greedy_primary(data, scenario, options, scenario.budget_units)
        oracle = independent_reference(data, scenario, options)
        result = run_one(data, scenario, options, metadata, 10.0, greedy, oracle)
        result["case_id"] = f"ABL-{dimension.upper()}"
        ablations.append(result)
        write_json(OUT / "runs" / "ablation" / f"{result['case_id']}.json", {"result": result, "fixture": case_document(*case)})
        print(json.dumps({"case": result["case_id"], "status": result["solver_status"],
                          "objective": result["best_feasible_served_demand"], "bound": result["primary_best_bound_served_demand"],
                          "gap": result["primary_relative_gap"], "seconds": result["runtime_seconds"],
                          "verify": (result["verifier"] or {}).get("verification_status")}, ensure_ascii=False))

    document = {
        "phase": "C_SYNTHETIC_STRESS_AND_D_ABLATION", "generator_version": "v2-synthetic-0.1.0",
        "created_utc": datetime.now(timezone.utc).isoformat(), "seed": SEED,
        "synthetic_provenance": "FULLY_SYNTHETIC_STRESS; no Module A population, shelter or OD values were copied into these fixtures.",
        "stress_sizes": list(SIZES), "time_limits_seconds": list(LIMITS),
        "stress_case_count": len(stress_cases), "stress_cases": stress_cases,
        "greedy_counterexample": trap_result, "ablation_cases": ablations,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "numpy": __import__("numpy").__version__, "scipy": __import__("scipy").__version__,
                        "networkx": __import__("networkx").__version__, "psutil_available": psutil is not None},
        "classification": {"optimality": "solver_status OPTIMAL only when all lexicographic MIP stages were proven; FEASIBLE remains unproven.",
                           "large_oracle": "For candidate sets above 8, independent all-expansions max-flow is an upper bound that ignores budget and exclusions.",
                           "memory": "RSS is sampled every 10 ms and Python heap peak is separately reported; very brief native peaks may be missed."},
    }
    write_json(OUT / "oracles" / "phase_c_d_synthetic_results.json", document)
    print(json.dumps({"stress_runs": len(stress_cases), "ablation_runs": len(ablations),
                      "optimal_stress_runs": sum(row["solver_status"] == "OPTIMAL" for row in stress_cases),
                      "feasible_or_timeout": sum(row["solver_status"] != "OPTIMAL" for row in stress_cases)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
