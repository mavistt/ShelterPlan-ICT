"""Independent hard-constraint, provenance and numerical checks."""
from __future__ import annotations

from math import isclose, isfinite

from backend.models import AssignmentResult, CheckRecord, PlanningInput, PlanningScenario, VerificationCertificate

TOL = 1e-6


def verify_assignment(data: PlanningInput, scenario: PlanningScenario, result: AssignmentResult, capacity_deltas: dict[str, float] | None = None) -> VerificationCertificate:
    checks: list[CheckRecord] = []
    version_ok = scenario.data_version == data.data_version
    region_ok = data.region_id is None or scenario.region_id == data.region_id
    quality_ok = scenario.analysis_mode != "verified" or data.data_status == "VERIFIED"
    def add(check_id: str, object_id: str, passed: bool, observed, limit, evidence: str, message: str):
        checks.append(CheckRecord(check_id=check_id, object_id=object_id, passed=passed, observed=observed, limit=limit, tolerance=TOL, evidence_ref=evidence, message=message))

    if scenario.data_version != data.data_version:
        add("DATA_VERSION", scenario.scenario_id, False, scenario.data_version, data.data_version, "scenario:data_version", "Scenario and source versions differ.")
    else:
        add("DATA_VERSION", scenario.scenario_id, True, scenario.data_version, data.data_version, "scenario:data_version", "Scenario uses the loaded data version.")
    if data.region_id is not None and scenario.region_id != data.region_id:
        add("REGION_ID", scenario.scenario_id, False, scenario.region_id, data.region_id, "scenario:region_id", "Scenario uses a different region than the loaded source.")
    elif data.region_id is not None:
        add("REGION_ID", scenario.scenario_id, True, scenario.region_id, data.region_id, "scenario:region_id", "Scenario uses the loaded source region.")
    if scenario.analysis_mode == "verified" and data.data_status != "VERIFIED":
        add("DATA_QUALITY", scenario.scenario_id, False, data.data_status, "VERIFIED", "source:data_status", "Real-planning output is not permitted for this source status.")
    else:
        add("DATA_QUALITY", scenario.scenario_id, True, data.data_status, scenario.analysis_mode, "source:data_status", "Data mode is permitted for this result scope.")

    demand = {node.demand_id: node.pop_est * scenario.population_demand_ratio for node in data.demands}
    shelter = {node.shelter_id: node.capacity_persons for node in data.shelters}
    capacity_deltas = capacity_deltas or {}
    disabled = set(scenario.disabled_shelter_ids)
    inactive = {row.shelter_id for row in data.shelters if not row.active}
    legal = {(e.demand_id, e.shelter_id) for e in data.od_edges if e.reachable and e.time_min is not None and e.time_min <= scenario.max_walk_time_min and e.shelter_id not in disabled and e.shelter_id not in inactive}
    deficit_rows = {row.get("demand_id"): row for row in result.deficits if isinstance(row, dict)}
    deficit_rows_unique = len(deficit_rows) == len(result.deficits)
    deficit_amounts: dict[str, float] = {}
    for did, row in deficit_rows.items():
        try:
            value = float(row.get("unserved_persons"))
            if isfinite(value):
                deficit_amounts[did] = value
        except (TypeError, ValueError):
            pass
    served = {key: 0.0 for key in demand}
    loads = {key: 0.0 for key in shelter}
    total_time = 0.0
    total_served = 0.0
    valid = version_ok and region_ok and quality_ok
    for row in result.assignments:
        did, sid = row.get("demand_id"), row.get("shelter_id")
        amount, time = row.get("persons"), row.get("time_min")
        row_ok = did in demand and sid in shelter and (did, sid) in legal
        try:
            amount_f, time_f = float(amount), float(time)
            row_ok = row_ok and isfinite(amount_f) and amount_f >= -TOL and isfinite(time_f) and time_f >= 0
        except (TypeError, ValueError):
            row_ok = False
            amount_f, time_f = 0.0, 0.0
        add("LEGAL_NONNEGATIVE_ASSIGNMENT", str(did), row_ok, {"shelter_id": sid, "persons": amount, "time_min": time}, "eligible OD edge; persons>=0", f"assignment:{did}:{sid}", "Assignment must use a legal edge and finite nonnegative values.")
        valid = valid and row_ok
        if row_ok:
            served[did] += amount_f
            loads[sid] += amount_f
            total_time += amount_f * time_f
            total_served += amount_f
    for did, amount in demand.items():
        deficit = deficit_amounts.get(did)
        conservation = deficit is not None and isclose(served[did] + deficit, amount, rel_tol=TOL, abs_tol=TOL)
        add("DEMAND_CONSERVATION", did, conservation, {"served": served[did], "deficit": deficit, "demand": amount}, amount, f"demand:{did}", "Served plus unserved demand must equal scenario demand.")
        valid = valid and conservation
    for sid, amount in loads.items():
        cap = 0.0 if sid in disabled or sid in inactive else shelter[sid] + capacity_deltas.get(sid, 0.0)
        within = amount <= cap + TOL
        add("SHELTER_CAPACITY", sid, within, amount, cap, f"shelter:{sid}/capacity", "Assigned load must fit the active shelter capacity.")
        valid = valid and within
    loads_match = all(isclose(loads[sid], float(result.shelter_loads.get(sid, float("nan"))), rel_tol=TOL, abs_tol=TOL) for sid in loads)
    deficits_match = deficit_rows_unique and set(deficit_rows) == set(demand) and set(deficit_amounts) == set(demand) and all(
        isclose(max(0.0, demand[did] - served[did]), deficit_amounts[did], rel_tol=TOL, abs_tol=TOL)
        for did in demand
    )
    numerical = isclose(total_served, result.served_demand, rel_tol=TOL, abs_tol=TOL) and isclose(total_time / total_served if total_served else 0.0, result.weighted_walk_time_min or 0.0, rel_tol=TOL, abs_tol=TOL) and loads_match and deficits_match
    add("NUMERICAL_RECOMPUTATION", "result", numerical, {"served": total_served, "weighted_walk_time": (total_time / total_served if total_served else None), "loads_match": loads_match, "deficits_match": deficits_match}, {"served": result.served_demand, "weighted_walk_time": result.weighted_walk_time_min}, "result:assignments", "Summary values are recomputed from assignments.")
    valid = valid and numerical
    if data.data_status == "VERIFIED":
        status = ("PASS_VERIFIED" if scenario.analysis_mode == "verified" else "PASS_DEMO") if valid else "FAIL"
    elif data.data_status in {"DEMO_ONLY", "SYNTHETIC_FIXTURE"} and scenario.analysis_mode == "demo":
        status = "PASS_DEMO" if valid else "FAIL"
    else:
        status = "BLOCKED"
    return VerificationCertificate(
        verification_status=status,
        verified_for_demo=(status == "PASS_DEMO"),
        verified_for_real_planning=(status == "PASS_VERIFIED"),
        checks=checks,
    )


def verify_budgeted(data: PlanningInput, scenario: PlanningScenario, options: list, result: dict, budget: float) -> dict:
    """Independently check selected-option eligibility, budget and expanded capacities."""
    by_id = {option.option_id: option for option in options}
    selected_ids = result.get("selected_option_ids", [])
    selected = [by_id[key] for key in selected_ids if key in by_id]
    checks = []
    checks.append({"check_id": "OPTION_IDS", "passed": len(selected) == len(selected_ids), "observed": selected_ids, "limit": "known unique options"})
    checks.append({"check_id": "OPTION_ELIGIBILITY", "passed": all(option.eligible for option in selected), "observed": [option.eligible for option in selected], "limit": True})
    cost = sum(option.cost_units for option in selected)
    checks.append({"check_id": "BUDGET", "passed": cost <= budget + TOL and isclose(cost, float(result.get("total_cost_units", float("nan"))), rel_tol=TOL, abs_tol=TOL), "observed": cost, "limit": budget})
    group_counts = {}
    policy_ok = True
    grouped = {}
    for option in selected:
        grouped.setdefault(option.shelter_id, []).append(option)
        if option.combination_policy == "exclusive_group":
            group_counts[option.exclusive_group] = group_counts.get(option.exclusive_group, 0) + 1
    for rows in grouped.values():
        if len(rows) > 1 and any(row.combination_policy != "stackable" for row in rows):
            policy_ok = False
    policy_ok = policy_ok and all(count <= 1 for count in group_counts.values())
    checks.append({"check_id": "COMBINATION_POLICY", "passed": policy_ok, "observed": selected_ids, "limit": "explicit stacking/standalone/exclusive rules"})
    deltas = {}
    for option in selected:
        deltas[option.shelter_id] = deltas.get(option.shelter_id, 0.0) + option.delta_capacity_persons
    from backend.models import AssignmentResult
    assignment = AssignmentResult(**result["assignment"])
    cert = verify_assignment(data, scenario, assignment, deltas)
    checks.extend([row.model_dump() for row in cert.checks])
    passed = all(row.get("passed", False) for row in checks)
    permitted_demo = data.data_status in {"DEMO_ONLY", "SYNTHETIC_FIXTURE"} and scenario.analysis_mode == "demo"
    permitted_verified = data.data_status == "VERIFIED" and scenario.analysis_mode == "verified"
    verification_status = "PASS_VERIFIED" if passed and permitted_verified else ("PASS_DEMO" if passed and (permitted_demo or data.data_status == "VERIFIED") else "FAIL")
    return {
        "verification_status": verification_status,
        "checks": checks,
        "verified_for_real_planning": passed and permitted_verified,
        "verified_for_demo": passed and verification_status == "PASS_DEMO",
    }
