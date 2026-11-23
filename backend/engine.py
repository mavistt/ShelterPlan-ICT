"""Capacity assignment, budgeted improvements and diagnostic calculations."""
from __future__ import annotations

from math import isclose
import time

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import coo_matrix, csr_matrix, vstack

from backend.errors import CandidateError, ScenarioError
from backend.models import AssignmentResult, ImprovementOption, PlanningInput, PlanningScenario

NUMERIC_TOLERANCE = 1e-9
# HiGHS can report the continuous primary objective a few ulps below its
# incumbent when the secondary MIP is rebuilt.  A 1e-9 lower bound therefore
# turns an otherwise feasible lexicographic stage into an apparent
# infeasible/unbounded model (the B-1 regression).  Keep the published
# numerical checks strict, but use this bounded solver-side slack.
MIP_OBJECTIVE_TOLERANCE = 1e-7
MAX_BUDGET_OPTIONS = 64
MAX_BUDGET_SOLVE_SECONDS = 10.0
MAX_LP_SOLVE_SECONDS = 10.0


def validate_scenario(data: PlanningInput, scenario: PlanningScenario) -> None:
    if scenario.data_version != data.data_version:
        raise ScenarioError("Scenario data_version does not match the loaded data", details={"expected": data.data_version, "received": scenario.data_version})
    if data.region_id is not None and scenario.region_id != data.region_id:
        raise ScenarioError(
            "Scenario region_id does not match the loaded data",
            details={"error_code": "REGION_MISMATCH", "expected": data.region_id, "received": scenario.region_id},
        )
    if scenario.analysis_mode == "verified":
        if data.data_status != "VERIFIED" or any(s.verified_status not in {"verified", "verified_current"} for s in data.shelters):
            raise ScenarioError("Real planning is blocked because Module A data are not formally verified", details={"data_status": data.data_status, "error_code": "DATA_NOT_VERIFIED"})
    if len(scenario.disabled_shelter_ids) != len(set(scenario.disabled_shelter_ids)):
        raise ScenarioError("disabled_shelter_ids contains duplicates")
    known = {s.shelter_id for s in data.shelters}
    unknown = set(scenario.disabled_shelter_ids) - known
    if unknown:
        raise ScenarioError("Scenario references an unknown shelter", details={"unknown_shelter_ids": sorted(unknown)})


def eligible_edges(data: PlanningInput, scenario: PlanningScenario) -> list[tuple[str, str, float]]:
    disabled = set(scenario.disabled_shelter_ids)
    inactive = {row.shelter_id for row in data.shelters if not row.active}
    return [
        (edge.demand_id, edge.shelter_id, float(edge.time_min))
        for edge in data.od_edges
        if edge.reachable and edge.time_min is not None
        and edge.time_min <= scenario.max_walk_time_min
        and edge.shelter_id not in disabled
        and edge.shelter_id not in inactive
    ]


def solve_assignment(
    data: PlanningInput,
    scenario: PlanningScenario,
    capacity_deltas: dict[str, float] | None = None,
    time_limit_seconds: float = MAX_LP_SOLVE_SECONDS,
) -> AssignmentResult:
    validate_scenario(data, scenario)
    capacity_deltas = capacity_deltas or {}
    demands = data.demands
    shelters = data.shelters
    demand_ids = [d.demand_id for d in demands]
    shelter_ids = [s.shelter_id for s in shelters]
    demand_by_id = {d.demand_id: d.pop_est * scenario.population_demand_ratio for d in demands}
    shelter_by_id = {s.shelter_id: s.capacity_persons for s in shelters}
    eligible = eligible_edges(data, scenario)
    n_edges, n_demands = len(eligible), len(demands)
    if not demands:
        raise ScenarioError("At least one demand node is required")

    inactive = {row.shelter_id for row in shelters if not row.active}
    capacities = {
        sid: (
            0.0 if sid in set(scenario.disabled_shelter_ids) or sid in inactive
            else shelter_by_id[sid] + capacity_deltas.get(sid, 0.0)
        )
        for sid in shelter_ids
    }
    total_demand = float(sum(demand_by_id.values()))
    if n_edges == 0 or total_demand <= NUMERIC_TOLERANCE:
        served = 0.0
        loads = {sid: 0.0 for sid in shelter_ids}
        deficits = [{"demand_id": did, "demand_persons": amount, "unserved_persons": amount} for did, amount in demand_by_id.items()]
        return AssignmentResult(
            solver_status="OPTIMAL", optimality_proven=True, served_demand=served,
            total_demand=total_demand, weighted_walk_time_min=None, assignments=[],
            deficits=deficits, shelter_loads=loads,
            shelter_utilization={sid: (None if capacities[sid] == 0 else 0.0) for sid in shelter_ids},
            objective_value=0.0,
        )

    # x variables occupy [0, n_edges), followed by one unserved variable per demand.
    variable_count = n_edges + n_demands
    demand_index = {did: index for index, did in enumerate(demand_ids)}
    c_primary = np.zeros(variable_count)
    c_primary[:n_edges] = -1.0
    eq_rows = [demand_index[did] for did, _sid, _time in eligible]
    eq_cols = list(range(n_edges))
    eq_values = [1.0] * n_edges
    eq_rows.extend(range(n_demands))
    eq_cols.extend(n_edges + index for index in range(n_demands))
    eq_values.extend([1.0] * n_demands)
    a_eq = coo_matrix((eq_values, (eq_rows, eq_cols)), shape=(n_demands, variable_count)).tocsr()
    b_eq = np.array([demand_by_id[did] for did in demand_ids], dtype=float)
    shelter_index = {sid: index for index, sid in enumerate(shelter_ids)}
    shelter_rows = [shelter_index[sid] for _did, sid, _time in eligible]
    a_ub = coo_matrix(
        ([1.0] * n_edges, (shelter_rows, list(range(n_edges)))),
        shape=(len(shelter_ids), variable_count),
    ).tocsr()
    b_ub = np.array([capacities[sid] for sid in shelter_ids], dtype=float)
    bounds = [(0.0, None)] * variable_count
    lp_options = {"time_limit": max(0.01, min(float(time_limit_seconds), MAX_LP_SOLVE_SECONDS))}
    primary = linprog(c_primary, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs", options=lp_options)
    if primary.x is None:
        status = "NO_FEASIBLE_SOLUTION" if primary.status == 2 else ("SOLVER_TIMEOUT" if primary.status == 1 else "SOLVER_ERROR")
        return AssignmentResult(
            solver_status=status,
            optimality_proven=False, served_demand=0.0, total_demand=total_demand,
            weighted_walk_time_min=None, assignments=[],
            deficits=[{"demand_id": did, "demand_persons": amount, "unserved_persons": amount} for did, amount in demand_by_id.items()],
            shelter_loads={sid: 0.0 for sid in shelter_ids},
            shelter_utilization={sid: (None if capacities[sid] <= NUMERIC_TOLERANCE else 0.0) for sid in shelter_ids}, objective_value=0.0,
        )
    if not primary.success:
        # A feasible time-limited incumbent is usable, but it cannot support a second-stage claim.
        return _assignment_from_allocation(
            data, scenario, eligible, primary.x[:n_edges], capacity_deltas,
            "FEASIBLE", False,
        )
    best_served = float(-primary.fun)
    c_time = np.zeros(variable_count)
    c_time[:n_edges] = np.array([edge[2] for edge in eligible])
    # Preserve the primary objective within a declared absolute tolerance.
    primary_floor = csr_matrix(
        ([-1.0] * n_edges, ([0] * n_edges, list(range(n_edges)))),
        shape=(1, variable_count),
    )
    a_ub_secondary = vstack([a_ub, primary_floor], format="csr")
    b_ub_secondary = np.r_[b_ub, -(best_served - NUMERIC_TOLERANCE)]
    secondary = linprog(c_time, A_ub=a_ub_secondary, b_ub=b_ub_secondary, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs", options=lp_options)
    if secondary.x is not None:
        final = secondary
        solver_status, optimality_proven = ("OPTIMAL", True) if secondary.success else ("FEASIBLE", False)
    else:
        final = primary
        solver_status, optimality_proven = "FEASIBLE", False
    x = np.maximum(final.x[:n_edges], 0.0)
    assignments = [
        {"demand_id": did, "shelter_id": sid, "persons": float(amount), "time_min": t}
        for (did, sid, t), amount in zip(eligible, x)
        if amount > NUMERIC_TOLERANCE
    ]
    served_by_demand = {did: 0.0 for did in demand_ids}
    loads = {sid: 0.0 for sid in shelter_ids}
    for row in assignments:
        served_by_demand[row["demand_id"]] += row["persons"]
        loads[row["shelter_id"]] += row["persons"]
    deficits = [
        {"demand_id": did, "demand_persons": demand_by_id[did], "unserved_persons": max(0.0, demand_by_id[did] - served_by_demand[did])}
        for did in demand_ids
    ]
    served_actual = float(sum(loads.values()))
    weighted_time = None if served_actual <= NUMERIC_TOLERANCE else float(sum(row["persons"] * row["time_min"] for row in assignments) / served_actual)
    utilization = {sid: (None if capacities[sid] <= NUMERIC_TOLERANCE else float(loads[sid] / capacities[sid])) for sid in shelter_ids}
    return AssignmentResult(
        solver_status=solver_status,
        optimality_proven=optimality_proven,
        served_demand=served_actual, total_demand=total_demand,
        weighted_walk_time_min=weighted_time, assignments=assignments,
        deficits=deficits, shelter_loads=loads, shelter_utilization=utilization,
        objective_value=served_actual,
    )


def audit_options(data: PlanningInput, options: list[ImprovementOption]) -> None:
    ids = [option.option_id for option in options]
    if len(ids) != len(set(ids)):
        raise CandidateError("Improvement option IDs must be unique")
    shelters = {s.shelter_id for s in data.shelters}
    for option in options:
        if option.shelter_id not in shelters:
            raise CandidateError("Improvement option references unknown shelter", details={"option_id": option.option_id})
        if not option.eligible:
            continue
        if not option.source_status.strip():
            raise CandidateError("Improvement option must identify its source status", details={"option_id": option.option_id})
        if option.combination_policy == "exclusive_group" and not option.exclusive_group:
            raise CandidateError("exclusive_group policy requires a group ID", details={"option_id": option.option_id})
        if option.hypothetical is False and option.human_confirmed is False:
            raise CandidateError("Unconfirmed non-hypothetical option is not admissible", details={"option_id": option.option_id})
    if len(options) > MAX_BUDGET_OPTIONS:
        raise CandidateError("Candidate set exceeds the configured MIP model limit", details={"option_count": len(options), "max_options": MAX_BUDGET_OPTIONS, "solver_status": "MODEL_SIZE_LIMIT"})


def _combination_valid(selected: tuple[ImprovementOption, ...]) -> bool:
    by_shelter: dict[str, list[ImprovementOption]] = {}
    for option in selected:
        by_shelter.setdefault(option.shelter_id, []).append(option)
    for rows in by_shelter.values():
        if len(rows) <= 1:
            continue
        if any(row.combination_policy != "stackable" for row in rows):
            return False
    group_counts: dict[str, int] = {}
    for row in selected:
        if row.combination_policy == "exclusive_group":
            group_counts[row.exclusive_group or ""] = group_counts.get(row.exclusive_group or "", 0) + 1
    if any(count > 1 for count in group_counts.values()):
        return False
    return True


def _assignment_from_allocation(
    data: PlanningInput,
    scenario: PlanningScenario,
    eligible: list[tuple[str, str, float]],
    allocation: np.ndarray,
    capacity_deltas: dict[str, float],
    solver_status: str,
    optimality_proven: bool,
) -> AssignmentResult:
    demands = data.demands
    shelters = data.shelters
    demand_by_id = {row.demand_id: row.pop_est * scenario.population_demand_ratio for row in demands}
    disabled = set(scenario.disabled_shelter_ids)
    capacities = {
        row.shelter_id: (0.0 if row.shelter_id in disabled else row.capacity_persons + capacity_deltas.get(row.shelter_id, 0.0))
        for row in shelters
    }
    assignments = [
        {"demand_id": did, "shelter_id": sid, "persons": float(amount), "time_min": time_min}
        for (did, sid, time_min), amount in zip(eligible, np.maximum(allocation, 0.0))
        if amount > NUMERIC_TOLERANCE
    ]
    served_by_demand = {did: 0.0 for did in demand_by_id}
    loads = {row.shelter_id: 0.0 for row in shelters}
    for row in assignments:
        served_by_demand[row["demand_id"]] += row["persons"]
        loads[row["shelter_id"]] += row["persons"]
    deficits = [
        {"demand_id": did, "demand_persons": amount, "unserved_persons": max(0.0, amount - served_by_demand[did])}
        for did, amount in demand_by_id.items()
    ]
    served = float(sum(loads.values()))
    weighted_time = None if served <= NUMERIC_TOLERANCE else float(sum(row["persons"] * row["time_min"] for row in assignments) / served)
    return AssignmentResult(
        solver_status=solver_status, optimality_proven=optimality_proven,
        served_demand=served, total_demand=float(sum(demand_by_id.values())),
        weighted_walk_time_min=weighted_time, assignments=assignments, deficits=deficits,
        shelter_loads=loads,
        shelter_utilization={sid: (None if capacities[sid] <= NUMERIC_TOLERANCE else float(loads[sid] / capacities[sid])) for sid in loads},
        objective_value=served,
    )


def _solve_budget_mip(
    data: PlanningInput,
    scenario: PlanningScenario,
    options: list[ImprovementOption],
    budget: float,
    time_limit_seconds: float,
) -> tuple[AssignmentResult, list[ImprovementOption], float, list[dict], bool]:
    """Solve service, travel time, cost and option-count objectives in order."""
    eligible = eligible_edges(data, scenario)
    demands = data.demands
    shelters = data.shelters
    demand_ids = [row.demand_id for row in demands]
    shelter_ids = [row.shelter_id for row in shelters]
    if not demands:
        raise ScenarioError("At least one demand node is required")

    # A candidate with no capacity delta cannot change any objective or
    # constraint.  Excluding it from the MIP avoids creating unconstrained
    # binary variables (HiGHS may report the secondary lexicographic stage as
    # infeasible/unbounded for that degenerate model) and such an option is
    # never a meaningful recommendation.
    active = [
        row for row in options
        if row.eligible
        and row.shelter_id not in scenario.disabled_shelter_ids
        and row.delta_capacity_persons > NUMERIC_TOLERANCE
    ]
    edge_count, demand_count, option_count = len(eligible), len(demands), len(active)
    var_count = edge_count + demand_count + option_count
    option_offset = edge_count + demand_count
    edge_by_demand: dict[str, list[int]] = {did: [] for did in demand_ids}
    edge_by_shelter: dict[str, list[int]] = {sid: [] for sid in shelter_ids}
    for index, (did, sid, _walk_time) in enumerate(eligible):
        edge_by_demand[did].append(index)
        edge_by_shelter[sid].append(index)

    row_indices: list[int] = []
    col_indices: list[int] = []
    values: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    row = 0
    demand_amounts = {item.demand_id: item.pop_est * scenario.population_demand_ratio for item in demands}
    for demand_index, did in enumerate(demand_ids):
        for edge_index in edge_by_demand[did]:
            row_indices.append(row); col_indices.append(edge_index); values.append(1.0)
        row_indices.append(row); col_indices.append(edge_count + demand_index); values.append(1.0)
        amount = float(demand_amounts[did])
        lower.append(amount); upper.append(amount); row += 1

    capacities = {item.shelter_id: item.capacity_persons for item in shelters}
    for sid in shelter_ids:
        for edge_index in edge_by_shelter[sid]:
            row_indices.append(row); col_indices.append(edge_index); values.append(1.0)
        for option_index, option in enumerate(active):
            if option.shelter_id == sid and option.delta_capacity_persons:
                row_indices.append(row); col_indices.append(option_offset + option_index); values.append(-option.delta_capacity_persons)
        capacity = 0.0 if sid in scenario.disabled_shelter_ids else float(capacities[sid])
        lower.append(-np.inf); upper.append(capacity); row += 1

    for option_index, option in enumerate(active):
        if option.cost_units:
            row_indices.append(row); col_indices.append(option_offset + option_index); values.append(option.cost_units)
    lower.append(-np.inf); upper.append(budget); row += 1

    by_shelter: dict[str, list[int]] = {}
    by_group: dict[str, list[int]] = {}
    for index, option in enumerate(active):
        by_shelter.setdefault(option.shelter_id, []).append(index)
        if option.combination_policy == "exclusive_group":
            by_group.setdefault(option.exclusive_group or "", []).append(index)
    # Standalone and exclusive-group actions cannot stack with any action at the same shelter.
    for indexes in by_shelter.values():
        for pos, first in enumerate(indexes):
            for second in indexes[pos + 1:]:
                if active[first].combination_policy != "stackable" or active[second].combination_policy != "stackable":
                    row_indices.extend([row, row]); col_indices.extend([option_offset + first, option_offset + second]); values.extend([1.0, 1.0])
                    lower.append(-np.inf); upper.append(1.0); row += 1
    # A named exclusive group is exclusive across the candidate set, including different sites.
    for indexes in by_group.values():
        if len(indexes) > 1:
            for index in indexes:
                row_indices.append(row); col_indices.append(option_offset + index); values.append(1.0)
            lower.append(-np.inf); upper.append(1.0); row += 1

    base_matrix = coo_matrix((values, (row_indices, col_indices)), shape=(row, var_count)).tocsr()
    base_lower, base_upper = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    variable_lower = np.zeros(var_count, dtype=float)
    variable_upper = np.full(var_count, np.inf, dtype=float)
    if option_count:
        variable_upper[option_offset:] = 1.0
    integrality = np.zeros(var_count, dtype=np.uint8)
    if option_count:
        integrality[option_offset:] = 1
    bounds = Bounds(variable_lower, variable_upper)
    walk_times = np.asarray([edge[2] for edge in eligible], dtype=float)
    option_costs = np.asarray([option.cost_units for option in active], dtype=float)
    stages: list[dict] = []
    deadline = time.monotonic() + min(max(float(time_limit_seconds), 0.01), MAX_BUDGET_SOLVE_SECONDS)

    def run_stage(name: str, objective: np.ndarray, extra_rows: list[tuple[np.ndarray, float, float]]):
        remaining = deadline - time.monotonic()
        if remaining <= 0.01:
            stages.append({"stage": name, "status": "TIME_LIMIT_NO_SOLVE", "optimality_proven": False})
            return None
        matrices = [base_matrix]
        lbs = [base_lower]
        ubs = [base_upper]
        if extra_rows:
            extra_matrix = csr_matrix(np.vstack([item[0] for item in extra_rows]))
            matrices.append(extra_matrix)
            lbs.append(np.asarray([item[1] for item in extra_rows], dtype=float))
            ubs.append(np.asarray([item[2] for item in extra_rows], dtype=float))
        matrix = matrices[0] if len(matrices) == 1 else vstack(matrices, format="csr")
        constraint = LinearConstraint(matrix, np.concatenate(lbs), np.concatenate(ubs))
        result = milp(
            c=objective, integrality=integrality, bounds=bounds, constraints=constraint,
            options={"time_limit": max(0.01, remaining), "mip_rel_gap": 0.0, "presolve": True},
        )
        stages.append({
            "stage": name, "status": int(result.status), "optimality_proven": bool(result.status == 0),
            "message": str(result.message), "mip_gap": getattr(result, "mip_gap", None),
            "mip_node_count": getattr(result, "mip_node_count", None),
        })
        return result

    primary_objective = np.zeros(var_count, dtype=float)
    primary_objective[:edge_count] = -1.0
    zero_extra: list[tuple[np.ndarray, float, float]] = []
    primary = run_stage("maximize_served_demand", primary_objective, zero_extra)
    if primary is None or primary.x is None:
        raise CandidateError("Budget MIP did not return a feasible incumbent", details={"solver_status": "SOLVER_TIMEOUT", "mip_stages": stages})
    final = primary
    all_optimal = primary.status == 0

    if all_optimal:
        best_served = float(np.sum(primary.x[:edge_count]))
        served_row = np.zeros(var_count); served_row[:edge_count] = 1.0
        secondary_objective = np.zeros(var_count); secondary_objective[:edge_count] = walk_times
        secondary = run_stage("minimize_weighted_walk_time", secondary_objective, [(served_row, best_served - MIP_OBJECTIVE_TOLERANCE, np.inf)])
        if secondary is not None and secondary.x is not None:
            final = secondary
            all_optimal = all_optimal and secondary.status == 0
        else:
            all_optimal = False

        if secondary is not None and secondary.x is not None and secondary.status == 0:
            best_walk_cost = float(np.dot(walk_times, secondary.x[:edge_count]))
            walk_row = np.zeros(var_count); walk_row[:edge_count] = walk_times
            cost_objective = np.zeros(var_count); cost_objective[option_offset:] = option_costs
            tertiary = run_stage(
                "minimize_option_cost", cost_objective,
                [(served_row, best_served - MIP_OBJECTIVE_TOLERANCE, np.inf), (walk_row, -np.inf, best_walk_cost + MIP_OBJECTIVE_TOLERANCE)],
            )
            if tertiary is not None and tertiary.x is not None:
                final = tertiary
                all_optimal = all_optimal and tertiary.status == 0
            else:
                all_optimal = False

            if tertiary is not None and tertiary.x is not None and tertiary.status == 0:
                best_cost = float(np.dot(option_costs, tertiary.x[option_offset:]))
                cost_row = np.zeros(var_count); cost_row[option_offset:] = option_costs
                count_objective = np.zeros(var_count); count_objective[option_offset:] = 1.0
                quaternary = run_stage(
                    "minimize_option_count", count_objective,
                    [
                        (served_row, best_served - MIP_OBJECTIVE_TOLERANCE, np.inf),
                        (walk_row, -np.inf, best_walk_cost + MIP_OBJECTIVE_TOLERANCE),
                        (cost_row, -np.inf, best_cost + MIP_OBJECTIVE_TOLERANCE),
                    ],
                )
                if quaternary is not None and quaternary.x is not None:
                    final = quaternary
                    all_optimal = all_optimal and quaternary.status == 0
                else:
                    all_optimal = False

    chosen = [option for index, option in enumerate(active) if final.x[option_offset + index] >= 0.5]
    deltas: dict[str, float] = {}
    for option in chosen:
        deltas[option.shelter_id] = deltas.get(option.shelter_id, 0.0) + option.delta_capacity_persons
    assignment = _assignment_from_allocation(
        data, scenario, eligible, final.x[:edge_count], deltas,
        "OPTIMAL" if all_optimal else "FEASIBLE", all_optimal,
    )
    spent = float(sum(option.cost_units for option in chosen))
    return assignment, chosen, spent, stages, all_optimal


def optimize_budgeted(
    data: PlanningInput, scenario: PlanningScenario, options: list[ImprovementOption], budget: float | None = None,
) -> dict:
    validate_scenario(data, scenario)
    audit_options(data, options)
    actual_budget = scenario.budget_units if budget is None else budget
    if actual_budget is None:
        raise ScenarioError("Budget is required for optimize task", details={"required_user_action": "Provide a confirmed budget_units value."})
    try:
        actual_budget = float(actual_budget)
    except (TypeError, ValueError) as exc:
        raise ScenarioError("Budget must be a finite number") from exc
    if not np.isfinite(actual_budget) or actual_budget < 0 or actual_budget > 1_000_000_000:
        raise ScenarioError("Budget must be finite and between 0 and 1,000,000,000")
    assignment, selected, spent, stages, proven = _solve_budget_mip(data, scenario, options, actual_budget, MAX_BUDGET_SOLVE_SECONDS)
    return {
        "solver_status": "OPTIMAL" if proven else "FEASIBLE", "optimality_proven": proven,
        "objective_value": assignment.served_demand, "selected_option_ids": [row.option_id for row in selected],
        "selected_options": [row.model_dump() for row in selected],
        "total_cost_units": spent, "budget_units": actual_budget,
        "assignment": assignment.model_dump(),
        "primary_objective": "maximum_served_demand",
        "secondary_objective": "minimum_weighted_walk_time_then_cost_then_option_count",
        "solver": "scipy.optimize.milp/HiGHS",
        "mip_stages": stages,
    }


def diagnose(data: PlanningInput, scenario: PlanningScenario, result: AssignmentResult) -> list[dict]:
    legal_by_demand: dict[str, list[tuple[str, float]]] = {d.demand_id: [] for d in data.demands}
    raw_reasons: dict[str, set[str]] = {d.demand_id: set() for d in data.demands}
    for edge in data.od_edges:
        if edge.shelter_id in scenario.disabled_shelter_ids:
            raw_reasons[edge.demand_id].add("shelter_disabled")
        elif not edge.reachable:
            raw_reasons[edge.demand_id].add(edge.reason)
        elif edge.time_min is not None and edge.time_min > scenario.max_walk_time_min:
            raw_reasons[edge.demand_id].add("time_threshold")
        elif edge.reachable and edge.time_min is not None:
            legal_by_demand[edge.demand_id].append((edge.shelter_id, edge.time_min))
    deficits = {row["demand_id"]: row["unserved_persons"] for row in result.deficits}
    evidence = []
    for demand in data.demands:
        missing = deficits.get(demand.demand_id, 0.0)
        if missing <= NUMERIC_TOLERANCE:
            continue
        if not legal_by_demand[demand.demand_id]:
            cause = "NO_LEGAL_CONNECTION"
        else:
            cause = "CAPACITY_COMPETITION_OR_INSUFFICIENCY"
        evidence.append({
            "demand_id": demand.demand_id,
            "unserved_persons": missing,
            "diagnosis": cause,
            "observed_edge_reasons": sorted(raw_reasons[demand.demand_id]),
            "evidence_ref": f"demand:{demand.demand_id}/eligible_edges",
            "unique_cause_claimed": False,
        })
    return evidence
