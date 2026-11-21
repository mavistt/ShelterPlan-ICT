"""Phase A: explain V1 Greedy/MIP outcomes and quantify real-graph interactions."""
from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
import sys
import time

import networkx as nx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "merge_data" / "tools"))

from backend.engine import eligible_edges, solve_assignment
from backend.models import PlanningScenario
from grounded_experiment_runner import load_options, load_source, subset_input

OUT = ROOT / "merge_data" / "v2_difficulty_discovery"
V1 = ROOT / "merge_data" / "gt" / "v1_greedy_mip_summary.json"
V1_MANIFEST = ROOT / "merge_data" / "scenarios" / "SCENARIO_MANIFEST.json"


def main() -> None:
    started = time.perf_counter()
    source = load_source()
    case_manifest = json.loads(V1_MANIFEST.read_text(encoding="utf-8"))
    b8_case = next(row for row in case_manifest["cases"] if row["case_id"] == "B08-01")
    options = load_options(b8_case)
    scenario = PlanningScenario(
        scenario_id="v2-phase-a-full-a-reference",
        data_version=source.data_version,
        analysis_mode="demo",
        population_demand_ratio=0.20,
        max_walk_time_min=15.0,
        budget_units=5.0,
    )
    eligible = eligible_edges(source, scenario)
    graph = nx.Graph()
    demand_ids = {row.demand_id for row in source.demands}
    shelter_ids = {row.shelter_id for row in source.shelters}
    graph.add_nodes_from((f"d:{item}", {"kind": "demand"}) for item in demand_ids)
    graph.add_nodes_from((f"s:{item}", {"kind": "shelter"}) for item in shelter_ids)
    graph.add_edges_from((f"d:{did}", f"s:{sid}") for did, sid, _ in eligible)
    components = []
    for component in nx.connected_components(graph):
        demands = sum(node.startswith("d:") for node in component)
        shelters = sum(node.startswith("s:") for node in component)
        if demands > 0 and shelters > 0:
            components.append({"demand_nodes": demands, "shelters": shelters, "vertices": len(component)})
    components.sort(key=lambda row: (-row["demand_nodes"], -row["shelters"]))

    base = solve_assignment(source, scenario)
    single_rows = []
    gains: dict[str, float] = {}
    for option in options:
        result = solve_assignment(source, scenario, {option.shelter_id: option.delta_capacity_persons})
        gain = result.served_demand - base.served_demand
        gains[option.option_id] = gain
        single_rows.append({"option_id": option.option_id, "shelter_id": option.shelter_id,
                            "cost_units": option.cost_units, "delta_capacity_persons_hypothetical": option.delta_capacity_persons,
                            "gain_persons": gain, "gain_per_cost": gain / max(option.cost_units, 1e-9),
                            "solver_status": result.solver_status})
    pair_rows = []
    for left, right in combinations(options, 2):
        delta = {left.shelter_id: left.delta_capacity_persons}
        delta[right.shelter_id] = delta.get(right.shelter_id, 0.0) + right.delta_capacity_persons
        result = solve_assignment(source, scenario, delta)
        pair_gain = result.served_demand - base.served_demand
        interaction = pair_gain - gains[left.option_id] - gains[right.option_id]
        pair_rows.append({"option_i": left.option_id, "option_j": right.option_id,
                          "gain_i": gains[left.option_id], "gain_j": gains[right.option_id],
                          "gain_ij": pair_gain, "interaction_persons": interaction,
                          "interaction_status": "overlap/diminishing" if interaction < -1e-5 else "complementary" if interaction > 1e-5 else "within_tolerance"})
    feasible_subsets = {}
    for budget in (5.0, 10.0, 20.0, 50.0, 100.0):
        count = 0
        for size in range(len(options) + 1):
            count += sum(sum(row.cost_units for row in subset) <= budget + 1e-8 for subset in combinations(options, size))
        feasible_subsets[str(int(budget))] = count

    v1_run = json.loads(V1.read_text(encoding="utf-8"))
    prior = []
    for row in v1_run["results"]:
        if "greedy" in row:
            greedy = row.get("greedy") or {}
            mip = row.get("mip") or {}
            greedy_served = greedy.get("served_demand")
            mip_served = mip.get("objective_value")
            mip_status = mip.get("solver_status")
            greedy_selected = greedy.get("selected_option_ids", [])
            mip_selected = mip.get("selected_option_ids", [])
            num_options = (row.get("metrics") or {}).get("num_options")
        else:
            greedy_served = row.get("greedy_served")
            mip_served = row.get("mip_served")
            mip_status = row.get("mip_status")
            greedy_selected = row.get("greedy_selected_option_ids", [])
            mip_selected = row.get("mip_selected_option_ids", [])
            num_options = row.get("num_options")
        if mip_served is None or not isinstance(greedy_served, (int, float)):
            continue
        prior.append({"case_id": row["case_id"], "num_options": num_options,
                      "greedy_served": greedy_served, "mip_served": mip_served,
                      "mip_status": mip_status,
                      "selected_sets_equal": set(greedy_selected) == set(mip_selected),
                      "greedy_selected": greedy_selected, "mip_selected": mip_selected})

    site_loads = base.shelter_loads
    site_by_id = {row.shelter_id: row for row in source.shelters}
    demand_population = {row.demand_id: row.pop_est * scenario.population_demand_ratio for row in source.demands}
    service_regions: dict[str, set[str]] = {sid: set() for sid in shelter_ids}
    for did, sid, _ in eligible:
        service_regions[sid].add(did)
    overlap_pairs = []
    for left, right in combinations(sorted(shelter_ids), 2):
        a, b = service_regions[left], service_regions[right]
        union = a | b
        intersection = a & b
        if not union:
            continue
        overlap_pairs.append({"shelter_i": left, "shelter_j": right,
                              "shared_demand_nodes": len(intersection),
                              "jaccard": len(intersection) / len(union)})
    overlap_pairs.sort(key=lambda row: (-row["jaccard"], -row["shared_demand_nodes"]))
    site_table = [{"shelter_id": sid, "capacity_unverified": site_by_id[sid].capacity_persons,
                   "base_load": site_loads[sid],
                   "utilization": (site_loads[sid] / site_by_id[sid].capacity_persons if site_by_id[sid].capacity_persons else None),
                   "unused_capacity_unverified": max(0.0, site_by_id[sid].capacity_persons - site_loads[sid]),
                   "eligible_demand_nodes": len(service_regions[sid]),
                   "eligible_demand_persons_upper_bound": sum(demand_population[did] for did in service_regions[sid])}
                  for sid in sorted(shelter_ids)]
    document = {
        "phase": "A1_A2", "seed": 20261114, "data_version": source.data_version,
        "data_status": source.data_status, "source_data_unchanged": True,
        "source_manifest_sha256": source.source_manifest_hash,
        "graph": {"demand_nodes": len(demand_ids), "shelters": len(shelter_ids),
                  "eligible_edges": len(eligible), "connected_components_with_edges": len(components),
                  "isolated_demand_nodes": len(demand_ids) - sum(row["demand_nodes"] for row in components),
                  "nontrivial_components": components},
        "default_scenario": scenario.model_dump(),
        "baseline": {"served_demand": base.served_demand, "total_demand": base.total_demand,
                     "unserved_demand": max(0.0, base.total_demand - base.served_demand),
                     "solver_status": base.solver_status},
        "sites": site_table, "service_overlap_pairs": overlap_pairs,
        "top_service_overlap_pairs": overlap_pairs[:20],
        "reference_options": [row.model_dump() for row in options],
        "single_option_gains": single_rows, "pairwise_interactions": pair_rows,
        "reference_budget_feasible_subsets_including_empty": feasible_subsets,
        "v1_greedy_vs_mip": {"cases_with_both": len(prior),
                             "same_primary_service_count": sum(abs(row["greedy_served"]-row["mip_served"]) <= 1e-4 for row in prior),
                             "same_selected_portfolio_count": sum(row["selected_sets_equal"] for row in prior),
                             "cases": prior},
        "elapsed_seconds": time.perf_counter() - started,
        "interpretation": {
            "proven_from_code": ["Greedy recomputes each feasible trial's assignment and marginal served gain every round.",
                                 "Greedy and MIP use the same budget, eligibility, shelter-stacking and exclusive-group rules.",
                                 "Greedy's local gain/cost choice is not an exhaustive global search.",
                                 "MIP optimizes four lexicographic stages; Greedy only directly targets primary served volume."],
            "measured_not_proven_structural": ["V1's curated portfolios produced essentially equal first-stage service.",
                                               "The budgeted option sets had few feasible subsets; capacity gains were often additive or diminishing on the selected A graph."],
        },
    }
    (OUT / "oracles" / "phase_a_real_structure.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"graph": document["graph"], "feasible_subset_counts": feasible_subsets,
                      "single_gains": [{k: row[k] for k in ("option_id", "gain_persons", "gain_per_cost")} for row in single_rows],
                      "interaction_counts": {status: sum(row["interaction_status"] == status for row in pair_rows) for status in {row["interaction_status"] for row in pair_rows}},
                      "v1_cases": document["v1_greedy_vs_mip"]["cases_with_both"],
                      "same_primary": document["v1_greedy_vs_mip"]["same_primary_service_count"],
                      "same_portfolio": document["v1_greedy_vs_mip"]["same_selected_portfolio_count"],
                      "elapsed_seconds": document["elapsed_seconds"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
