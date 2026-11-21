"""Reproducible structural audit of the immutable Module A OD data."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import platform
import time

import numpy
import scipy

from backend.adapters.a_reader import load_a_bundle
from backend.engine import diagnose, eligible_edges, solve_assignment
from backend.models import PlanningInput, PlanningScenario
from backend.verifier.checks import verify_assignment

ROOT = Path(__file__).resolve().parents[2]
DATA_VERSION = "zhongshan_pilot_v0.1"
RATIOS = (0.20,)
THRESHOLDS = (10.0, 15.0, 20.0)


def load_data() -> tuple[PlanningInput, object]:
    bundle = load_a_bundle(ROOT / "configs/pilot.yaml", ROOT)
    data = PlanningInput(
        data_version=bundle.manifest.data_version,
        data_status=bundle.availability.data_status,
        demands=bundle.demands,
        shelters=bundle.shelters,
        od_edges=bundle.od_edges,
        source_manifest_hash=bundle.manifest.source_manifest_hash,
        source_label=f"module_a:{bundle.manifest.data_version}",
    )
    if data.data_version != DATA_VERSION or data.data_status != "DEMO_ONLY":
        raise RuntimeError("Unexpected A data version/status; refusing to run.")
    return data, bundle


def make_scenario(data: PlanningInput, *, scenario_id: str, threshold: float = 15.0,
                  ratio: float = 0.20, disabled: list[str] | None = None) -> PlanningScenario:
    return PlanningScenario(
        scenario_id=scenario_id,
        data_version=data.data_version,
        analysis_mode="demo",
        population_demand_ratio=ratio,
        max_walk_time_min=threshold,
        disabled_shelter_ids=disabled or [],
    )


def audit_structure() -> dict:
    data, bundle = load_data()
    demand_pop = {row.demand_id: float(row.pop_est) for row in data.demands}
    total_population = sum(demand_pop.values())
    ratio = 0.20
    total_demand = total_population * ratio
    reasons = Counter((row.reason, row.reachable) for row in data.od_edges)
    reachable_rows = [row for row in data.od_edges if row.reachable and row.time_min is not None]
    thresholds: dict[str, dict] = {}
    base_result = None
    base_scenario = None
    base_seconds = None
    base_certificate = None
    base_diagnosis = None
    base_sites: dict[str, set[str]] = {}
    base_degree: Counter = Counter()

    for threshold in THRESHOLDS:
        scenario = make_scenario(data, scenario_id=f"structure-t{threshold:g}", threshold=threshold)
        edges = eligible_edges(data, scenario)
        degree = Counter(did for did, _sid, _time in edges)
        result_start = time.perf_counter()
        result = solve_assignment(data, scenario)
        elapsed = time.perf_counter() - result_start
        certificate = verify_assignment(data, scenario, result)
        thresholds[str(int(threshold))] = {
            "eligible_edge_count": len(edges),
            "demand_nodes_with_edge": len(degree),
            "demand_nodes_without_edge": len(data.demands) - len(degree),
            "demand_nodes_degree_1": sum(1 for count in degree.values() if count == 1),
            "demand_nodes_degree_2_plus": sum(1 for count in degree.values() if count >= 2),
            "demand_population_with_edge": sum(demand_pop[did] for did in degree),
            "scenario_demand": total_demand,
            "served_demand": result.served_demand,
            "service_rate": result.served_demand / total_demand if total_demand else None,
            "weighted_walk_time_min": result.weighted_walk_time_min,
            "solver_status": result.solver_status,
            "optimality_proven": result.optimality_proven,
            "verification_status": certificate.verification_status,
            "solve_seconds": elapsed,
            "edge_counts_within_half_minute_of_cutoff": sum(
                1 for row in reachable_rows if abs(float(row.time_min) - threshold) <= 0.5
            ),
        }
        if threshold == 15.0:
            base_result, base_scenario, base_seconds, base_certificate = result, scenario, elapsed, certificate
            base_degree = degree
            base_diagnosis = diagnose(data, scenario, result)
            base_sites = {row.shelter_id: set() for row in data.shelters}
            for did, sid, _walk in edges:
                base_sites[sid].add(did)

    assert base_result is not None and base_scenario is not None
    degree_hist = Counter(base_degree.get(row.demand_id, 0) for row in data.demands)
    degree_population = Counter()
    for row in data.demands:
        degree_population[str(base_degree.get(row.demand_id, 0))] += float(row.pop_est)

    diagnoses: dict[str, dict] = {}
    for row in base_diagnosis or []:
        item = diagnoses.setdefault(row["diagnosis"], {"demand_nodes": 0, "unserved_persons": 0.0})
        item["demand_nodes"] += 1
        item["unserved_persons"] += float(row["unserved_persons"])

    shelter_by_id = {row.shelter_id: row for row in data.shelters}
    site_rows = []
    for sid, node_ids in base_sites.items():
        capacity = float(shelter_by_id[sid].capacity_persons)
        load = float(base_result.shelter_loads[sid])
        eligible_population = sum(demand_pop[did] for did in node_ids)
        exclusive_population = sum(
            demand_pop[row.demand_id]
            for row in data.demands
            if row.demand_id in node_ids and base_degree.get(row.demand_id, 0) == 1
        )
        bump = 0.25 * capacity
        bump_start = time.perf_counter()
        bumped = solve_assignment(data, base_scenario, {sid: bump})
        bump_seconds = time.perf_counter() - bump_start
        bump_cert = verify_assignment(data, base_scenario, bumped, {sid: bump})
        close_scenario = make_scenario(
            data, scenario_id=f"close-{sid}", disabled=[sid],
        )
        close_start = time.perf_counter()
        closed = solve_assignment(data, close_scenario)
        close_seconds = time.perf_counter() - close_start
        close_cert = verify_assignment(data, close_scenario, closed)
        site_rows.append({
            "shelter_id": sid,
            "capacity_persons_unverified": capacity,
            "baseline_load_persons": load,
            "baseline_utilization": base_result.shelter_utilization[sid],
            "eligible_demand_node_count": len(node_ids),
            "eligible_population_est": eligible_population,
            "exclusive_reachable_population_est": exclusive_population,
            "bump_25pct_assumption": bump,
            "bump_25pct_served_gain": bumped.served_demand - base_result.served_demand,
            "bump_25pct_solver_status": bumped.solver_status,
            "bump_25pct_verification_status": bump_cert.verification_status,
            "bump_25pct_solve_seconds": bump_seconds,
            "closure_served_loss": base_result.served_demand - closed.served_demand,
            "closure_solver_status": closed.solver_status,
            "closure_verification_status": close_cert.verification_status,
            "closure_solve_seconds": close_seconds,
        })
    site_rows.sort(key=lambda row: (
        -float(row["bump_25pct_served_gain"]),
        -float(row["closure_served_loss"]),
        row["shelter_id"],
    ))

    overlaps = []
    ids = sorted(base_sites)
    for i, sid_a in enumerate(ids):
        for sid_b in ids[i + 1:]:
            shared = base_sites[sid_a] & base_sites[sid_b]
            if not shared:
                continue
            union = base_sites[sid_a] | base_sites[sid_b]
            overlaps.append({
                "shelter_a": sid_a,
                "shelter_b": sid_b,
                "shared_demand_nodes": len(shared),
                "jaccard": len(shared) / len(union),
                "shared_population_est": sum(demand_pop[did] for did in shared),
            })
    overlaps.sort(key=lambda row: (-row["jaccard"], -row["shared_population_est"], row["shelter_a"], row["shelter_b"]))

    diagnosis_by_demand = {row["demand_id"]: row["diagnosis"] for row in base_diagnosis or []}
    return {
        "audit_version": "grounded-structure-audit-0.1.0",
        "generator_version": "grounded-exp-0.1.0",
        "seed": 20261113,
        "environment": {
            "python": platform.python_version(),
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
        },
        "source": {
            "data_version": data.data_version,
            "data_status": data.data_status,
            "a7_status": bundle.manifest.release_status,
            "source_manifest_sha256": data.source_manifest_hash,
            "demand_nodes": len(data.demands),
            "shelters": len(data.shelters),
            "od_rows": len(data.od_edges),
            "reachable_od_rows_all_times": len(reachable_rows),
            "od_reason_counts": {f"{reason}|reachable={reachable}": count for (reason, reachable), count in reasons.items()},
            "population_est": total_population,
            "scenario_demand_at_ratio_0_2": total_demand,
            "nominal_capacity_persons_unverified": sum(row.capacity_persons for row in data.shelters),
        },
        "thresholds": thresholds,
        "baseline_15_min": {
            "population_demand_ratio": ratio,
            "max_walk_time_min": 15.0,
            "eligible_edges": len(eligible_edges(data, base_scenario)),
            "served_demand": base_result.served_demand,
            "service_rate": base_result.served_demand / base_result.total_demand,
            "weighted_walk_time_min": base_result.weighted_walk_time_min,
            "solver_status": base_result.solver_status,
            "optimality_proven": base_result.optimality_proven,
            "verification_status": base_certificate.verification_status,
            "solve_seconds": base_seconds,
            "diagnoses": diagnoses,
            "demand_degree_node_counts": {str(key): degree_hist.get(key, 0) for key in range(5)} | {
                "5+": sum(count for key, count in degree_hist.items() if key >= 5)
            },
            "demand_degree_population_est": dict(degree_population),
            "demand_diagnosis_labels": diagnosis_by_demand,
        },
        "site_metrics": site_rows,
        "site_overlap_top20": overlaps[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="merge_data/runs/01_structure_audit.json")
    args = parser.parse_args()
    result = audit_structure()
    target = (ROOT / args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": target.relative_to(ROOT).as_posix(),
        "data_version": result["source"]["data_version"],
        "counts": {key: result["source"][key] for key in ("demand_nodes", "shelters", "od_rows", "reachable_od_rows_all_times")},
        "baseline_15": {key: result["baseline_15_min"][key] for key in ("eligible_edges", "served_demand", "service_rate", "solver_status", "verification_status")},
        "thresholds": {key: {k: v for k, v in value.items() if k in {"eligible_edge_count", "demand_nodes_with_edge", "served_demand", "service_rate", "solve_seconds", "verification_status"}} for key, value in result["thresholds"].items()},
        "top_bump_sites": [{k: row[k] for k in ("shelter_id", "bump_25pct_served_gain", "closure_served_loss")} for row in result["site_metrics"][:8]],
        "top_overlap": result["site_overlap_top20"][:3],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
