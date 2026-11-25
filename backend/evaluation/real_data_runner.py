"""Reproducible Module B experiments using the configured Module A outputs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import gc
from pathlib import Path
import threading
import time
import uuid

import yaml

from backend.adapters.a_reader import load_a_bundle
from backend.engine import diagnose, eligible_edges, optimize_budgeted, solve_assignment
from backend.models import ImprovementOption, PlanningInput, PlanningScenario
from backend.verifier.checks import verify_assignment, verify_budgeted

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PeakRssSampler:
    def __init__(self, interval_seconds: float = 0.01):
        import psutil
        self._process = psutil.Process()
        self._interval = interval_seconds
        self.peak_bytes = self._process.memory_info().rss
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                self.peak_bytes = max(self.peak_bytes, self._process.memory_info().rss)
            except Exception:
                pass
            self._stop.wait(self._interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join()
        self.peak_bytes = max(self.peak_bytes, self._process.memory_info().rss)


def _timed(call):
    started = time.perf_counter()
    value = call()
    return value, time.perf_counter() - started


def _assignment_metrics(data, scenario, result) -> dict:
    total = result.total_demand
    return {
        "solver_status": result.solver_status,
        "optimality_proven": result.optimality_proven,
        "served_demand_persons": result.served_demand,
        "total_demand_persons": total,
        "service_rate": result.served_demand / total if total else None,
        "unserved_demand_persons": max(0.0, total - result.served_demand),
        "weighted_walk_time_min": result.weighted_walk_time_min,
        "shelter_loads": result.shelter_loads,
        "shelter_utilization": result.shelter_utilization,
        "verification_status": verify_assignment(data, scenario, result).verification_status,
    }


def _build_options(data, experiment_config, candidate_shelter_ids: list[str]) -> list[ImprovementOption]:
    cfg = experiment_config["experiment"]["expansion"]
    fraction = float(cfg["capacity_delta_fraction"])
    cost = float(cfg["cost_units_per_site"])
    selected_shelters = [row for row in data.shelters if row.shelter_id in set(candidate_shelter_ids)]
    return [ImprovementOption(
        option_id=f"HYPOTHETICAL_CAP_{index:02d}",
        shelter_id=shelter.shelter_id,
        action=str(cfg["action"]),
        delta_capacity_persons=float(shelter.capacity_persons * fraction),
        cost_units=cost,
        eligible=True,
        source_status=str(cfg["source_status"]),
        hypothetical=True,
        human_confirmed=bool(cfg["human_confirmed"]),
        combination_policy=str(cfg["combination_policy"]),
    ) for index, shelter in enumerate(selected_shelters, start=1)]


def _make_plan_result(data, scenario, selected, cost, budget, assignment, proven=True) -> dict:
    return {
        "solver_status": assignment.solver_status if proven else "FEASIBLE",
        "optimality_proven": bool(proven and assignment.optimality_proven),
        "objective_value": assignment.served_demand,
        "selected_option_ids": [item.option_id for item in selected],
        "selected_options": [item.model_dump() for item in selected],
        "total_cost_units": cost,
        "budget_units": budget,
        "assignment": assignment.model_dump(),
    }


def _greedy_budget_curve(data, scenario, options, budget_levels, initial_assignment=None) -> dict:
    max_budget = max(budget_levels, default=0.0)
    # Reuse the baseline solve. On the full A dataset another LP allocation can
    # fail after the sensitivity and stress runs have already exercised memory.
    current = initial_assignment or solve_assignment(data, scenario)
    selected: list[ImprovementOption] = []
    remaining = [item for item in options if item.eligible and item.shelter_id not in scenario.disabled_shelter_ids]
    spent = 0.0
    solver_calls = 0 if initial_assignment is not None else 1
    started = time.perf_counter()
    snapshots: dict[float, dict] = {}

    def snapshot(budget):
        plan = _make_plan_result(data, scenario, selected, spent, budget, current)
        certificate = verify_budgeted(data, scenario, options, plan, budget)
        return {
            "solver_status": plan["solver_status"], "optimality_proven": plan["optimality_proven"],
            "served_demand_persons": current.served_demand,
            "service_rate": current.served_demand / current.total_demand if current.total_demand else None,
            "weighted_walk_time_min": current.weighted_walk_time_min,
            "cost_units": spent, "budget_units": budget,
            "selected_option_ids": plan["selected_option_ids"],
            "verification_status": certificate["verification_status"],
        }

    for budget in sorted(set(float(value) for value in budget_levels)):
        while remaining:
            choices = []
            for option in remaining:
                if spent + option.cost_units > budget + 1e-9 or spent + option.cost_units > max_budget + 1e-9:
                    continue
                trial = [*selected, option]
                if any(row.shelter_id == option.shelter_id for row in selected):
                    continue
                groups = [row.exclusive_group for row in trial if row.combination_policy == "exclusive_group"]
                if len(groups) != len(set(groups)):
                    continue
                deltas = {}
                for row in trial:
                    deltas[row.shelter_id] = deltas.get(row.shelter_id, 0.0) + row.delta_capacity_persons
                candidate = solve_assignment(data, scenario, deltas)
                solver_calls += 1
                if candidate.solver_status not in {"OPTIMAL", "FEASIBLE"}:
                    continue
                gain = candidate.served_demand - current.served_demand
                choices.append((gain / max(option.cost_units, 1e-12), gain, option.option_id, option, candidate))
            if not choices:
                break
            _ratio, gain, _option_id, option, candidate = max(choices, key=lambda row: (row[0], row[1], row[2]))
            if gain <= 1e-8:
                break
            selected.append(option)
            remaining.remove(option)
            spent += option.cost_units
            current = candidate
        snapshots[budget] = snapshot(budget)
    return {"plans": {str(budget): row for budget, row in snapshots.items()}, "solver_calls": solver_calls, "runtime_seconds": time.perf_counter() - started}


def run_experiment(config_path: str | Path = "configs/module_b_hypothetical.yaml", output_dir: str | Path = "outputs/module_b/real_data") -> dict:
    root = PROJECT_ROOT
    config_file = (root / config_path).resolve() if not Path(config_path).is_absolute() else Path(config_path)
    experiment_config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    scenario_cfg = experiment_config["experiment"]["base_scenario"]
    ratio_values = experiment_config["experiment"]["sensitivity"]["population_demand_ratio"]
    threshold_values = experiment_config["experiment"]["sensitivity"]["max_walk_time_min"]
    budget_levels = [float(value) for value in experiment_config["experiment"]["expansion"]["budget_units"]]
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    with PeakRssSampler() as memory:
        bundle, load_seconds = _timed(lambda: load_a_bundle(root / "configs/pilot.yaml", root))
        data = PlanningInput(
            data_version=bundle.manifest.data_version,
            data_status=bundle.availability.data_status,
            demands=bundle.demands,
            shelters=bundle.shelters,
            od_edges=bundle.od_edges,
            source_manifest_hash=bundle.manifest.source_manifest_hash,
            source_label=f"module_a:{bundle.manifest.data_version}",
        )
        base_scenario = PlanningScenario(
            scenario_id=f"real-data-demo-{run_id[:8]}", data_version=data.data_version,
            analysis_mode="demo", population_demand_ratio=float(scenario_cfg["population_demand_ratio"]),
            max_walk_time_min=float(scenario_cfg["max_walk_time_min"]),
        )
        base_result, base_seconds = _timed(lambda: solve_assignment(data, base_scenario))
        base_certificate = verify_assignment(data, base_scenario, base_result)
        base_diagnosis = diagnose(data, base_scenario, base_result)
        diagnosis_by_type: dict[str, dict] = {}
        for row in base_diagnosis:
            group = diagnosis_by_type.setdefault(row["diagnosis"], {"demand_nodes": 0, "unserved_persons": 0.0})
            group["demand_nodes"] += 1
            group["unserved_persons"] += float(row["unserved_persons"])

        legal_edges = eligible_edges(data, base_scenario)
        reachable_ids = {did for did, _sid, _time in legal_edges}
        pop_by_id = {row.demand_id: row.pop_est for row in data.demands}
        total_population = float(sum(pop_by_id.values()))
        total_demand = total_population * base_scenario.population_demand_ratio
        reachable_population = float(sum(pop_by_id[did] for did in reachable_ids))
        reachable_demand = reachable_population * base_scenario.population_demand_ratio
        nominal_capacity = float(sum(row.capacity_persons for row in data.shelters))

        grid = []
        for ratio in ratio_values:
            for threshold in threshold_values:
                scenario = base_scenario.model_copy(update={
                    "scenario_id": f"{base_scenario.scenario_id}-r{ratio}-t{threshold}",
                    "population_demand_ratio": float(ratio), "max_walk_time_min": float(threshold),
                })
                if float(ratio) == base_scenario.population_demand_ratio and float(threshold) == base_scenario.max_walk_time_min:
                    assigned, elapsed = base_result, base_seconds
                else:
                    assigned, elapsed = _timed(lambda: solve_assignment(data, scenario))
                cert = verify_assignment(data, scenario, assigned)
                grid.append({
                    "population_demand_ratio": float(ratio), "max_walk_time_min": float(threshold),
                    "scenario_id": scenario.scenario_id, "served_demand_persons": assigned.served_demand,
                    "service_rate": assigned.served_demand / assigned.total_demand if assigned.total_demand else None,
                    "weighted_walk_time_min": assigned.weighted_walk_time_min,
                    "solver_status": assigned.solver_status, "optimality_proven": assigned.optimality_proven,
                    "verification_status": cert.verification_status, "runtime_seconds": elapsed,
                })

        most_loaded = max(base_result.shelter_loads, key=base_result.shelter_loads.get)
        closed_scenario = base_scenario.model_copy(update={
            "scenario_id": f"{base_scenario.scenario_id}-closed-{most_loaded}",
            "disabled_shelter_ids": [most_loaded],
        })
        closed_result, close_seconds = _timed(lambda: solve_assignment(data, closed_scenario))
        closed_certificate = verify_assignment(data, closed_scenario, closed_result)

        # A capacity-only upper bound preserves exactly the same OD relation and time threshold.
        full_demand_capacity = {row.shelter_id: total_demand for row in data.shelters}
        upper_result, upper_seconds = _timed(lambda: solve_assignment(data, base_scenario, full_demand_capacity))
        upper_certificate = verify_assignment(data, base_scenario, upper_result, full_demand_capacity)

        candidate_count = int(experiment_config["experiment"]["expansion"]["candidate_site_count"])
        candidate_shelter_ids = sorted(
            base_result.shelter_loads,
            key=lambda sid: (-float(base_result.shelter_utilization[sid] or 0.0), sid),
        )[:candidate_count]
        options = _build_options(data, experiment_config, candidate_shelter_ids)
        # Greedy evaluates candidate LPs. Run it before the MIP batch and avoid
        # retaining full per-demand MIP assignment dumps in the result object.
        greedy_curve, greedy_seconds = _timed(lambda: _greedy_budget_curve(
            data, base_scenario, options, budget_levels, initial_assignment=base_result,
        ))
        greedy_curve["runtime_seconds_total"] = greedy_seconds
        gc.collect()

        mip_plans = {}
        for budget in budget_levels:
            scenario = base_scenario.model_copy(update={"scenario_id": f"{base_scenario.scenario_id}-mip-budget-{budget}", "budget_units": budget})
            try:
                plan, elapsed = _timed(lambda: optimize_budgeted(data, scenario, options, budget))
                certificate = verify_budgeted(data, scenario, options, plan, budget)
                gaps = [stage.get("mip_gap") for stage in plan.get("mip_stages", []) if stage.get("mip_gap") is not None]
                mip_plans[str(budget)] = {
                    "solver_status": plan["solver_status"], "optimality_proven": plan["optimality_proven"],
                    "optimality_gap": max(gaps) if gaps and not plan["optimality_proven"] else (0.0 if plan["optimality_proven"] else None),
                    "served_demand_persons": plan["objective_value"],
                    "service_rate": plan["objective_value"] / total_demand if total_demand else None,
                    "weighted_walk_time_min": plan["assignment"]["weighted_walk_time_min"],
                    "cost_units": plan["total_cost_units"], "budget_units": budget,
                    "selected_option_ids": plan["selected_option_ids"],
                    "verification_status": certificate["verification_status"],
                    "runtime_seconds": elapsed, "mip_stages": plan.get("mip_stages", []),
                }
                del plan, certificate
                gc.collect()
            except Exception as exc:
                mip_plans[str(budget)] = {"solver_status": getattr(exc, "error_code", "ERROR"), "error": str(exc), "budget_units": budget}

        records = {
            "run_id": run_id,
            "started_at_utc": started_at,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "label": "DEMO_ONLY real-source planning experiment; no verified real-planning claim",
            "data": {
                "data_version": data.data_version, "data_status": data.data_status,
                "source_manifest_sha256": data.source_manifest_hash,
                "demand_nodes": len(data.demands), "shelters": len(data.shelters), "od_rows": len(data.od_edges),
                "population_estimate_persons": total_population,
                "nominal_capacity_persons_unverified": nominal_capacity,
                "release_status": bundle.manifest.release_status,
                "validated_manifest_artifacts": bundle.manifest.validated_artifacts,
            },
            "run_parameters": {
                "base_scenario": base_scenario.model_dump(), "sensitivity_ratios": ratio_values,
                "sensitivity_walk_thresholds_min": threshold_values, "budget_units": budget_levels,
                "hypothetical_options": [item.model_dump() for item in options],
                "hypothetical_candidate_selection": "top sites by base-scenario utilization; one standalone 25 percent capacity option per selected site",
                "hypothetical_options_config": str(config_file.relative_to(root).as_posix()),
                "max_lp_solve_seconds": 10.0, "max_mip_solve_seconds": 10.0,
            },
            "performance": {
                "data_load_seconds": load_seconds,
                "base_solve_seconds": base_seconds,
                "sampled_peak_rss_bytes": memory.peak_bytes,
                "rss_sampling_interval_seconds": 0.01,
            },
            "baseline_assessment": {
                **_assignment_metrics(data, base_scenario, base_result),
                "assignment": base_result.model_dump(),
                "solver_runtime_seconds": base_seconds,
                "scenario_demand_persons": total_demand,
                "reachable_population_estimate": reachable_population,
                "reachable_population_rate": reachable_population / total_population if total_population else None,
                "reachable_scenario_demand_persons": reachable_demand,
                "reachability_coverage_rate": reachable_demand / total_demand if total_demand else None,
                "capacity_constrained_service_coverage_rate": base_result.served_demand / total_demand if total_demand else None,
                "service_rate_within_reachable_demand": base_result.served_demand / reachable_demand if reachable_demand else None,
                "unserved_by_diagnosis": diagnosis_by_type,
                "diagnosis": base_diagnosis,
                "verification_status": base_certificate.verification_status,
            },
            "scenario_grid": grid,
            "closure_stress": {
                "disabled_shelter_id": most_loaded,
                "baseline_load_persons": base_result.shelter_loads[most_loaded],
                "baseline_served_persons": base_result.served_demand,
                "stressed_served_persons": closed_result.served_demand,
                "stressed_service_rate": closed_result.served_demand / closed_result.total_demand if closed_result.total_demand else None,
                "weighted_walk_time_min": closed_result.weighted_walk_time_min,
                "solver_status": closed_result.solver_status,
                "verification_status": closed_certificate.verification_status,
                "runtime_seconds": close_seconds,
                "assignment": closed_result.model_dump(),
            },
            "reachability_upper_bound": {
                "method": "same legal OD edges and time threshold, each active site capacity raised by scenario demand as an explicit mathematical upper-bound counterfactual",
                "served_demand_persons": upper_result.served_demand,
                "service_rate": upper_result.served_demand / total_demand if total_demand else None,
                "unreachable_demand_persons": max(0.0, total_demand - reachable_demand),
                "remaining_unserved_even_with_unbounded_site_capacity": max(0.0, total_demand - upper_result.served_demand),
                "solver_status": upper_result.solver_status,
                "verification_status": upper_certificate.verification_status,
                "runtime_seconds": upper_seconds,
                "assignment": upper_result.model_dump(),
            },
            "budget_comparison": {
                "label": "HYPOTHETICAL; abstract cost units and 25 percent capacity additions are not engineering estimates",
                "estimated_workload_before_run": {
                    "scenario_grid_assessments_including_baseline": len(ratio_values) * len(threshold_values),
                    "additional_closure_and_reachability_upper_bound_lp_solves": 2,
                    "greedy_lp_solves_upper_bound": len(options) * (len(budget_levels) - 1),
                    "mip_budget_runs": len(budget_levels),
                    "mip_total_time_limit_upper_bound_seconds": len(budget_levels) * 10.0,
                },
                "candidates": len(options), "budgeted_mip": mip_plans,
                "greedy_expansion": greedy_curve,
            },
            "limitations": [
                "A7 is DEMO_ONLY; all 24 shelters are unverified and have approximate coordinates/current capacity unverified.",
                "The analysis polygon is not an official administrative boundary.",
                "The source manifest declares a 922,251,437-byte population raster; local file is a 134-byte Git LFS pointer, so full A source reproducibility is incomplete.",
                "HYPOTHETICAL expansion cost units are comparison assumptions, not real prices.",
            ],
        }

    output = (root / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"real_data_experiment_{run_id}.json"
    target.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"run_id": run_id, "path": str(target.relative_to(root).as_posix()), "records": records}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/module_b_hypothetical.yaml")
    parser.add_argument("--output-dir", default="outputs/module_b/real_data")
    args = parser.parse_args()
    result = run_experiment(args.config, args.output_dir)
    summary = result["records"]["baseline_assessment"]
    print(json.dumps({
        "run_id": result["run_id"], "path": result["path"],
        "data_status": result["records"]["data"]["data_status"],
        "solver_status": summary["solver_status"], "verification_status": summary["verification_status"],
        "served_demand_persons": summary["served_demand_persons"],
        "total_demand_persons": summary["total_demand_persons"],
        "performance": result["records"]["performance"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
