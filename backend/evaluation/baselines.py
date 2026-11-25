"""Runnable non-LLM baselines over shared synthetic planning inputs."""
from __future__ import annotations

from itertools import combinations

from backend.engine import audit_options, optimize_budgeted, solve_assignment
from backend.graph.executor import load_fixture
from backend.graph.planner import fallback_plan
from backend.models import TaskRequest
from backend.verifier.checks import verify_assignment, verify_budgeted


def integer_max_flow(data, scenario) -> int | None:
    """Independent Dinic max-flow oracle for integer toy-case primary objectives."""
    demand = {d.demand_id: d.pop_est * scenario.population_demand_ratio for d in data.demands}
    cap = {s.shelter_id: (0 if s.shelter_id in scenario.disabled_shelter_ids else s.capacity_persons) for s in data.shelters}
    return _integer_flow(data, scenario, cap, demand)


def _integer_flow(data, scenario, cap, demand) -> int | None:
    if any(abs(x - round(x)) > 1e-8 for x in [*demand.values(), *cap.values()]):
        return None
    legal = [(e.demand_id, e.shelter_id) for e in data.od_edges if e.reachable and e.time_min is not None and e.time_min <= scenario.max_walk_time_min and e.shelter_id not in scenario.disabled_shelter_ids]
    source, sink = "__source__", "__sink__"
    graph: dict[str, list[list]] = {}
    def add(u, v, capacity):
        graph.setdefault(u, []); graph.setdefault(v, [])
        forward = [v, int(capacity), len(graph[v])]
        reverse = [u, 0, len(graph[u])]
        graph[u].append(forward); graph[v].append(reverse)
    total = sum(int(round(x)) for x in demand.values())
    for did, value in demand.items():
        add(source, f"d:{did}", value)
    for did, sid in legal:
        add(f"d:{did}", f"s:{sid}", total)
    for sid, value in cap.items():
        add(f"s:{sid}", sink, value)
    flow = 0
    while True:
        level = {source: 0}
        queue = [source]
        for u in queue:
            for v, residual, _ in graph.get(u, []):
                if residual > 0 and v not in level:
                    level[v] = level[u] + 1; queue.append(v)
        if sink not in level:
            break
        pos = {u: 0 for u in graph}
        def send(u, amount):
            if u == sink: return amount
            while pos[u] < len(graph[u]):
                edge = graph[u][pos[u]]
                v, residual, reverse_index = edge
                if residual > 0 and level.get(v) == level[u] + 1:
                    pushed = send(v, min(amount, residual))
                    if pushed:
                        edge[1] -= pushed
                        graph[v][reverse_index][1] += pushed
                        return pushed
                pos[u] += 1
            return 0
        while True:
            pushed = send(source, total)
            if not pushed: break
            flow += pushed
    return flow


def integer_expansion_oracle(data, scenario, options) -> int | None:
    """Enumerate feasible options and use independent max flow for each subset."""
    if len(options) > 12:
        return None
    budget = scenario.budget_units
    if budget is None:
        return integer_max_flow(data, scenario)
    best = 0
    for count in range(len(options) + 1):
        for selected in combinations(options, count):
            if any(not row.eligible or row.shelter_id in scenario.disabled_shelter_ids for row in selected):
                continue
            if sum(row.cost_units for row in selected) > budget + 1e-8:
                continue
            by_shelter = {}
            for row in selected:
                by_shelter.setdefault(row.shelter_id, []).append(row)
            if any(len(rows) > 1 and any(row.combination_policy != "stackable" for row in rows) for rows in by_shelter.values()):
                continue
            exclusive_groups = [row.exclusive_group for row in selected if row.combination_policy == "exclusive_group"]
            if len(exclusive_groups) != len(set(exclusive_groups)):
                continue
            cap = {s.shelter_id: (0 if s.shelter_id in scenario.disabled_shelter_ids else s.capacity_persons) for s in data.shelters}
            for row in selected:
                cap[row.shelter_id] += row.delta_capacity_persons
            demand = {d.demand_id: d.pop_est * scenario.population_demand_ratio for d in data.demands}
            result = _integer_flow(data, scenario, cap, demand)
            if result is not None:
                best = max(best, result)
    return best


def run_baseline(fixture_id: str, baseline_id: str, *, data=None, scenario=None, options=None) -> dict:
    if data is None or scenario is None or options is None:
        loaded_data, loaded_scenario, loaded_options = load_fixture(f"tests/module_b/fixtures/{fixture_id}.json")
        data = loaded_data if data is None else data
        scenario = loaded_scenario if scenario is None else scenario
        options = loaded_options if options is None else options
    if baseline_id == "StaticWorkflow":
        outcome = solve_assignment(data, scenario)
        certificate = verify_assignment(data, scenario, outcome)
        return {"served_demand": outcome.served_demand, "weighted_walk_time_min": outcome.weighted_walk_time_min, "solver_status": outcome.solver_status, "feasible": certificate.verification_status == "PASS_DEMO", "published": certificate.verification_status == "PASS_DEMO", "verification_status": certificate.verification_status, "verifier": "independent certificate executed"}
    if baseline_id == "GreedyExpansion":
        budget = scenario.budget_units or 0
        audit_options(data, options)
        remaining = [row for row in options if row.eligible and row.shelter_id not in scenario.disabled_shelter_ids]
        selected = []
        spent = 0.0
        current = solve_assignment(data, scenario)
        while remaining:
            choices = []
            for option in remaining:
                if not option.eligible or spent + option.cost_units > budget: continue
                trial = selected + [option]
                grouped = {}
                for row in trial:
                    grouped.setdefault(row.shelter_id, []).append(row)
                if any(len(rows) > 1 and any(row.combination_policy != "stackable" for row in rows) for rows in grouped.values()):
                    continue
                groups = [row.exclusive_group for row in trial if row.combination_policy == "exclusive_group"]
                if len(groups) != len(set(groups)):
                    continue
                deltas = {}
                for row in trial:
                    deltas[row.shelter_id] = deltas.get(row.shelter_id, 0.0) + row.delta_capacity_persons
                result = solve_assignment(data, scenario, deltas)
                gain = result.served_demand - current.served_demand
                choices.append((gain / max(option.cost_units, 1e-9), gain, option.option_id, option, result, deltas))
            if not choices: break
            _ratio, gain, _id, option, best, best_deltas = max(choices, key=lambda row: (row[0], row[1], row[2]))
            if gain <= 1e-8: break
            selected.append(option); remaining.remove(option); spent += option.cost_units
            current = best
        certificate = verify_assignment(data, scenario, current, best_deltas if selected else {})
        budget_ok = spent <= budget + 1e-8
        verified = certificate.verification_status == "PASS_DEMO" and budget_ok
        return {"served_demand": current.served_demand, "weighted_walk_time_min": current.weighted_walk_time_min, "selected_option_ids": [row.option_id for row in selected], "solver_status": current.solver_status, "feasible": verified, "published": verified, "verification_status": certificate.verification_status if budget_ok else "FAIL", "budget_units": budget, "cost_units": spent}
    if baseline_id == "SkillGraphOnly":
        plan = fallback_plan("assess")
        outcome = solve_assignment(data, scenario)
        return {"served_demand": outcome.served_demand, "weighted_walk_time_min": outcome.weighted_walk_time_min, "plan_nodes": len(plan.nodes), "solver_status": outcome.solver_status, "feasible": outcome.solver_status == "OPTIMAL", "published": False, "publication_state": "SHADOW_ONLY_QUARANTINED", "verifier": "NOT_RUN_BY_ABLATION"}
    if baseline_id == "SkillGraphVerifier":
        from backend.graph.executor import execute_task
        outcome = execute_task(
            TaskRequest(task_type="assess", fixture_id=fixture_id if data.data_status == "SYNTHETIC_FIXTURE" and fixture_id else None),
            source_override=(data, scenario, options),
        )
        return {"served_demand": outcome["result_summary"]["served_demand"], "weighted_walk_time_min": outcome["result_summary"]["weighted_walk_time_min"], "solver_status": outcome["solver_status"], "feasible": outcome["verification_status"] == "PASS_DEMO", "published": outcome["verification_status"] == "PASS_DEMO", "verification_status": outcome["verification_status"], "trace_id": outcome["execution_trace_id"]}
    raise ValueError(f"Unknown runnable baseline: {baseline_id}")
