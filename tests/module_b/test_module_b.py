"""Standard-library tests; pytest can collect these unittest.TestCase classes too."""
from __future__ import annotations

import json
from pathlib import Path
import time
import unittest
import uuid
from unittest.mock import patch

import numpy as np
from pydantic import ValidationError
from scipy.optimize import OptimizeResult, linprog

from backend.adapters.a_reader import inspect_data, load_a_bundle
from backend.engine import diagnose, eligible_edges, optimize_budgeted, solve_assignment
from backend.evaluation.baselines import integer_expansion_oracle, integer_max_flow
from backend.evaluation.benchmark_cases import build_cases
from backend.evaluation.fault_injection import run_fault_campaign
from backend.evaluation.refinement import run_offline_refinement
from backend.errors import DataUnavailableError, PlanRejectedError, ScenarioError
from backend.graph.executor import CACHE, execute_task, load_fixture
from backend.graph.planner import validate_plan
from backend.models import AssignmentResult, ImprovementOption, PlanningScenario, ProposedSkillPlan, TaskRequest
from backend.storage import JsonResultStore
from backend.verifier.checks import verify_assignment, verify_budgeted
from backend.verifier.counterfactual import verify_monotonicity

ROOT = Path(__file__).resolve().parents[2]


class AdapterTests(unittest.TestCase):
    def test_missing_a_outputs_are_explicit_and_read_only(self):
        with patch("backend.adapters.a_reader.Path.is_file", return_value=False):
            status = inspect_data(ROOT / "configs/pilot.yaml", ROOT)
            self.assertEqual(status.data_status, "MISSING")
            self.assertIn("data/processed/travel_matrix.parquet", status.missing_paths)
            self.assertEqual(len(status.existing_paths), 0)
            with self.assertRaises(DataUnavailableError):
                load_a_bundle(ROOT / "configs/pilot.yaml", ROOT)

    def test_synced_a_bundle_schema_hashes_and_demo_status(self):
        bundle = load_a_bundle(ROOT / "configs/pilot.yaml", ROOT)
        self.assertEqual(bundle.availability.data_status, "DEMO_ONLY")
        self.assertEqual(bundle.manifest.data_version, "zhongshan_pilot_v0.1")
        self.assertEqual(bundle.manifest.release_status, "DEMO_ONLY")
        self.assertEqual((len(bundle.shelters), len(bundle.demands), len(bundle.od_edges)), (24, 7965, 191160))
        self.assertEqual(len(bundle.manifest.validated_artifacts), 4)
        self.assertTrue(all(item["matched"] is True for item in bundle.manifest.validated_artifacts))


class SolverTests(unittest.TestCase):
    def setUp(self):
        self.data, self.scenario, self.options = load_fixture("tests/module_b/fixtures/toy_case.json")

    def test_assignment_and_expansion(self):
        current = solve_assignment(self.data, self.scenario)
        self.assertAlmostEqual(current.served_demand, 150, places=6)
        self.assertAlmostEqual(current.weighted_walk_time_min, 6.3333333333, places=6)
        self.assertTrue(current.optimality_proven)
        improved = optimize_budgeted(self.data, self.scenario, self.options, 10)
        self.assertAlmostEqual(improved["objective_value"], 200, places=6)
        self.assertEqual(improved["selected_option_ids"], ["A_PLUS_50"])
        self.assertTrue(improved["optimality_proven"])

    def test_zero_budget_is_feasible_and_keeps_residual(self):
        result = optimize_budgeted(self.data, self.scenario, self.options, 0)
        self.assertEqual(result["selected_option_ids"], [])
        self.assertAlmostEqual(result["objective_value"], 150, places=6)
        self.assertAlmostEqual(sum(row["unserved_persons"] for row in result["assignment"]["deficits"]), 50, places=6)

    def test_unconfirmed_budget_is_requested_and_standalone_options_do_not_stack(self):
        scenario = PlanningScenario(**{**self.scenario.model_dump(), "budget_units": None})
        with self.assertRaises(ScenarioError):
            execute_task(TaskRequest(task_type="optimize", fixture_id="toy_case", scenario=scenario))
        extra = ImprovementOption(**{**self.options[0].model_dump(), "option_id": "A_PLUS_20", "delta_capacity_persons": 20, "cost_units": 2})
        result = optimize_budgeted(self.data, self.scenario, [self.options[0], extra], 20)
        self.assertEqual(len(result["selected_option_ids"]), 1)

    def test_budget_mip_accepts_more_than_the_old_enumeration_bound(self):
        base = self.options[0].model_dump()
        options = [ImprovementOption(**{**base, "option_id": f"ZERO_{index}", "delta_capacity_persons": 0, "cost_units": 1, "combination_policy": "stackable"}) for index in range(13)]
        result = optimize_budgeted(self.data, self.scenario, options, 10)
        self.assertTrue(result["optimality_proven"])
        self.assertEqual(result["selected_option_ids"], [])
        self.assertEqual(result["solver"], "scipy.optimize.milp/HiGHS")

    def test_sparse_lp_matches_dense_reference_on_toy_case(self):
        edges = eligible_edges(self.data, self.scenario)
        demand_ids = [row.demand_id for row in self.data.demands]
        shelter_ids = [row.shelter_id for row in self.data.shelters]
        n_edges, n_demands = len(edges), len(demand_ids)
        variable_count = n_edges + n_demands
        demand_index = {did: index for index, did in enumerate(demand_ids)}
        shelter_index = {sid: index for index, sid in enumerate(shelter_ids)}
        a_eq = np.zeros((n_demands, variable_count))
        a_ub = np.zeros((len(shelter_ids), variable_count))
        for index, (did, sid, _time) in enumerate(edges):
            a_eq[demand_index[did], index] = 1.0
            a_ub[shelter_index[sid], index] = 1.0
        for index, row in enumerate(self.data.demands):
            a_eq[index, n_edges + index] = 1.0
        demand = np.asarray([row.pop_est * self.scenario.population_demand_ratio for row in self.data.demands])
        capacity = np.asarray([row.capacity_persons for row in self.data.shelters])
        bounds = [(0, None)] * variable_count
        primary_objective = np.zeros(variable_count); primary_objective[:n_edges] = -1.0
        primary = linprog(primary_objective, A_ub=a_ub, b_ub=capacity, A_eq=a_eq, b_eq=demand, bounds=bounds, method="highs")
        time_objective = np.zeros(variable_count); time_objective[:n_edges] = [row[2] for row in edges]
        primary_row = np.r_[-np.ones(n_edges), np.zeros(n_demands)]
        secondary = linprog(time_objective, A_ub=np.vstack([a_ub, primary_row]), b_ub=np.r_[capacity, -(-primary.fun - 1e-9)], A_eq=a_eq, b_eq=demand, bounds=bounds, method="highs")
        sparse = solve_assignment(self.data, self.scenario)
        self.assertTrue(primary.success and secondary.success)
        self.assertAlmostEqual(sparse.served_demand, -primary.fun, places=6)
        self.assertAlmostEqual(sparse.weighted_walk_time_min, secondary.fun / sparse.served_demand, places=6)

    def test_lp_timeout_incumbent_and_no_incumbent_are_not_called_optimal(self):
        incumbent = OptimizeResult(status=1, success=False, x=np.asarray([100.0, 0.0, 50.0, 0.0, 50.0]), message="time limit")
        with patch("backend.engine.linprog", return_value=incumbent):
            feasible = solve_assignment(self.data, self.scenario)
        self.assertEqual(feasible.solver_status, "FEASIBLE")
        self.assertFalse(feasible.optimality_proven)
        self.assertAlmostEqual(feasible.served_demand, 150, places=6)

        no_incumbent = OptimizeResult(status=1, success=False, x=None, message="time limit")
        with patch("backend.engine.linprog", return_value=no_incumbent):
            timed_out = solve_assignment(self.data, self.scenario)
        self.assertEqual(timed_out.solver_status, "SOLVER_TIMEOUT")
        self.assertFalse(timed_out.optimality_proven)
        self.assertAlmostEqual(sum(row["unserved_persons"] for row in timed_out.deficits), 200, places=6)

    def test_budget_mip_timeout_incumbent_is_not_called_optimal(self):
        incumbent = OptimizeResult(status=1, success=False, x=np.asarray([100.0, 0.0, 50.0, 0.0, 50.0, 0.0]), message="time limit")
        with patch("backend.engine.milp", return_value=incumbent):
            result = optimize_budgeted(self.data, self.scenario, self.options, 10)
        self.assertEqual(result["solver_status"], "FEASIBLE")
        self.assertFalse(result["optimality_proven"])
        self.assertAlmostEqual(result["objective_value"], 150, places=6)

    def test_exclusive_group_is_global_across_shelters(self):
        base = self.options[0].model_dump()
        constrained_data = self.data.model_copy(update={
            "shelters": [row.model_copy(update={"capacity_persons": 50}) for row in self.data.shelters],
        })
        options = [
            ImprovementOption(**{**base, "option_id": "A_PLUS", "shelter_id": "A", "cost_units": 1, "combination_policy": "exclusive_group", "exclusive_group": "one-project"}),
            ImprovementOption(**{**base, "option_id": "B_PLUS", "shelter_id": "B", "cost_units": 1, "combination_policy": "exclusive_group", "exclusive_group": "one-project"}),
        ]
        result = optimize_budgeted(constrained_data, self.scenario, options, 2)
        self.assertAlmostEqual(result["objective_value"], 150, places=6)
        self.assertEqual(len(result["selected_option_ids"]), 1)
        self.assertEqual(verify_budgeted(constrained_data, self.scenario, options, result, 2)["verification_status"], "PASS_DEMO")

    def test_budget_verifier_never_promotes_demo_data_to_verified(self):
        demo_data = self.data.model_copy(update={"data_status": "DEMO_ONLY"})
        assignment = solve_assignment(demo_data, self.scenario)
        plan = {
            "selected_option_ids": [], "total_cost_units": 0.0,
            "assignment": assignment.model_dump(),
        }
        certificate = verify_budgeted(demo_data, self.scenario, [], plan, 0.0)
        self.assertEqual(certificate["verification_status"], "PASS_DEMO")
        self.assertTrue(certificate["verified_for_demo"])
        self.assertFalse(certificate["verified_for_real_planning"])

    def test_monotonicity_for_budget_and_walk_threshold(self):
        low_budget = optimize_budgeted(self.data, self.scenario, self.options, 0)["objective_value"]
        high_budget = optimize_budgeted(self.data, self.scenario, self.options, 10)["objective_value"]
        short_walk = solve_assignment(self.data, self.scenario.model_copy(update={"max_walk_time_min": 6})).served_demand
        full_walk = solve_assignment(self.data, self.scenario).served_demand
        self.assertLessEqual(low_budget, high_budget + 1e-6)
        self.assertLessEqual(short_walk, full_walk + 1e-6)
        check = verify_monotonicity({"served_demand": low_budget}, {"served_demand": high_budget}, relation="nondecreasing", same_data_version=True, both_optimal=True, parameter_change_valid=True)
        self.assertTrue(check["applicable"])
        self.assertTrue(check["passed"])
        not_applicable = verify_monotonicity({"served_demand": low_budget}, {"served_demand": high_budget}, relation="nondecreasing", same_data_version=True, both_optimal=False, parameter_change_valid=True)
        self.assertIsNone(not_applicable["passed"])

    def test_result_store_rejects_path_like_ids(self):
        store = JsonResultStore(ROOT / "outputs/module_b/runs")
        self.assertIsNone(store.get("..\\..\\README"))

    def test_no_legal_edge_and_capacity_competition_are_separate(self):
        data, scenario, _ = load_fixture("tests/module_b/fixtures/mixed_bottlenecks.json")
        result = solve_assignment(data, scenario)
        diagnoses = {row["demand_id"]: row["diagnosis"] for row in diagnose(data, scenario, result)}
        self.assertEqual(diagnoses["D3"], "NO_LEGAL_CONNECTION")
        self.assertEqual(diagnoses["D2"], "CAPACITY_COMPETITION_OR_INSUFFICIENCY")

    def test_threshold_rebuilds_eligible_edges(self):
        strict = self.scenario.model_copy(update={"max_walk_time_min": 6})
        result = solve_assignment(self.data, strict)
        self.assertAlmostEqual(result.served_demand, 100, places=6)

    def test_mip_input_rejects_negative_cost(self):
        raw = self.options[0].model_dump()
        raw["cost_units"] = -1
        with self.assertRaises(ValidationError):
            ImprovementOption(**raw)

    def test_integer_oracle_independently_matches_primary_objective(self):
        self.assertEqual(integer_max_flow(self.data, self.scenario), 150)
        solved = solve_assignment(self.data, self.scenario)
        self.assertAlmostEqual(solved.served_demand, integer_max_flow(self.data, self.scenario), places=6)

    def test_benchmark_catalog_has_40_cases_with_family_separated_splits(self):
        cases = build_cases()
        self.assertEqual(len(cases), 40)
        self.assertEqual({split: sum(case.split == split for case in cases) for split in ("dev", "validation", "test")}, {"dev": 16, "validation": 12, "test": 12})
        family_splits = {}
        for case in cases:
            family_splits.setdefault(case.template_family, set()).add(case.split)
        self.assertEqual(len(family_splits), 10)
        self.assertTrue(all(len(splits) == 1 for splits in family_splits.values()))

    def test_budget_oracle_enforces_exclusive_groups_across_shelters(self):
        data, scenario, _ = load_fixture("tests/module_b/fixtures/mixed_bottlenecks.json")
        scenario = scenario.model_copy(update={"budget_units": 2})
        options = [
            ImprovementOption(option_id="S1_PLUS", shelter_id="S1", action="capacity", delta_capacity_persons=40, cost_units=1, eligible=True, source_status="test", hypothetical=True, human_confirmed=False, combination_policy="exclusive_group", exclusive_group="one"),
            ImprovementOption(option_id="S2_PLUS", shelter_id="S2", action="capacity", delta_capacity_persons=40, cost_units=1, eligible=True, source_status="test", hypothetical=True, human_confirmed=False, combination_policy="exclusive_group", exclusive_group="one"),
        ]
        oracle = integer_expansion_oracle(data, scenario, options)
        self.assertEqual(oracle, 135)


class PlannerAndVerifierTests(unittest.TestCase):
    def test_module_b_handoff_examples_match_request_schema_and_reject_bad_inputs(self):
        examples = ROOT / "examples/module_b"
        requests = {
            path.stem: TaskRequest(**json.loads(path.read_text(encoding="utf-8")))
            for path in examples.glob("*.json")
        }
        self.assertEqual(len(requests), 7)
        self.assertEqual(requests["assess_current_a"].data_version, "zhongshan_pilot_v0.1")
        self.assertEqual(requests["budget_hypothetical_current_a"].improvement_options[0].hypothetical, True)
        valid_plan = requests["valid_skill_plan_toy"]
        self.assertEqual(validate_plan(valid_plan.task_type, valid_plan.proposed_skill_plan).status, "accepted")
        invalid_plan = requests["invalid_skill_plan_toy"]
        with self.assertRaises(PlanRejectedError):
            validate_plan(invalid_plan.task_type, invalid_plan.proposed_skill_plan)
        with self.assertRaises(ScenarioError):
            execute_task(requests["invalid_scenario_toy"])

    def test_missing_required_skill_is_rewritten_and_unknown_or_cycle_rejected(self):
        incomplete = ProposedSkillPlan(nodes=[{"node_id": "a", "skill_id": "S01", "skill_version": "1.0.0"}, {"node_id": "b", "skill_id": "S12", "depends_on": ["a"], "skill_version": "1.0.0"}])
        self.assertEqual(validate_plan("assess", incomplete).status, "rewritten")
        unknown = ProposedSkillPlan(nodes=[{"node_id": "x", "skill_id": "S99", "skill_version": "1.0.0"}])
        with self.assertRaises(PlanRejectedError):
            validate_plan("assess", unknown)
        cycle = ProposedSkillPlan(nodes=[{"node_id": "a", "skill_id": "S01", "depends_on": ["b"], "skill_version": "1.0.0"}, {"node_id": "b", "skill_id": "S02", "depends_on": ["a"], "skill_version": "1.0.0"}])
        with self.assertRaises(PlanRejectedError):
            validate_plan("assess", cycle)

    def test_verifier_catches_capacity_corruption_and_clean_case(self):
        data, scenario, _ = load_fixture("tests/module_b/fixtures/toy_case.json")
        clean = solve_assignment(data, scenario)
        self.assertEqual(verify_assignment(data, scenario, clean).verification_status, "PASS_DEMO")
        corrupted = clean.model_copy(update={"shelter_loads": {"A": 1000, "B": 0}})
        self.assertEqual(verify_assignment(data, scenario, corrupted).verification_status, "FAIL")
        campaign = run_fault_campaign(data, scenario, clean, _)
        self.assertFalse(campaign["false_positive_clean_case"])
        self.assertEqual(campaign["fault_detection_recall"], 1.0)

    def test_verified_mode_is_blocked_for_synthetic_fixture(self):
        with self.assertRaises(ScenarioError):
            execute_task(TaskRequest(task_type="assess", fixture_id="toy_case", analysis_mode="verified"))

    def test_budget_change_reuses_od_graph_but_recomputes_optimizer(self):
        CACHE.clear()
        first = execute_task(TaskRequest(task_type="optimize", fixture_id="toy_case"))
        second = execute_task(TaskRequest(task_type="optimize", fixture_id="toy_case", changes={"budget_units": 0}, parent_scenario_id=first["scenario_id"]))
        first_s03 = next(row for row in first["trace"] if row["skill_id"] == "S03")
        second_s03 = next(row for row in second["trace"] if row["skill_id"] == "S03")
        second_s07 = next(row for row in second["trace"] if row["skill_id"] == "S07")
        self.assertFalse(first_s03["cache_hit"])
        self.assertTrue(second_s03["cache_hit"])
        self.assertFalse(second_s07["cache_hit"])
        self.assertNotEqual(first["scenario_hash"], second["scenario_hash"])

    def test_stress_and_comparison_are_labeled(self):
        stress = execute_task(TaskRequest(task_type="stress_test", fixture_id="toy_case", parameters={"disabled_shelter_ids": ["A"]}))
        self.assertIn("fixed_plan_stress", stress["result_detail"]["stress"])
        self.assertIn("adaptive_replan", stress["result_detail"]["stress"])
        compare = execute_task(TaskRequest(task_type="compare", fixture_id="toy_case", parameters={"scenarios": [{"max_walk_time_min": 5}, {"max_walk_time_min": 15}]}))
        self.assertIsNone(compare["result_detail"]["comparison"]["unique_recommendation"])

    def test_diagnose_intervene_takes_no_candidate_branch(self):
        result = execute_task(TaskRequest(task_type="diagnose_intervene", fixture_id="mixed_bottlenecks"))
        skills = [row["skill_id"] for row in result["plan_decision"]["nodes"]]
        self.assertNotIn("S06", skills)
        self.assertNotIn("S07", skills)
        self.assertEqual(result["result_detail"]["intervention"]["reason_code"], "NO_ELIGIBLE_EXPANSION_OPTIONS")

    def test_sensitivity_points_are_versioned_and_verified(self):
        result = execute_task(TaskRequest(task_type="sensitivity", fixture_id="toy_case", parameters={"parameter": "budget_units", "values": [0, 10]}))
        points = result["result_detail"]["sensitivity"]["points"]
        self.assertEqual([point["verification_status"] for point in points], ["PASS_DEMO", "PASS_DEMO"])
        self.assertNotEqual(points[0]["scenario_hash"], points[1]["scenario_hash"])

    def test_offline_refinement_records_rejected_candidate_without_claims(self):
        temp_root = ROOT / "outputs/module_b/test_runs" / str(uuid.uuid4())
        record = run_offline_refinement(temp_root)
        self.assertEqual(record["validation"]["status"], "PASS_INTERNAL_SYNTHETIC")
        self.assertEqual(record["validation"]["case_count"], 12)
        self.assertEqual(record["decision"], "KEEP_REVIEW_REQUIRED")
        self.assertIsNone(record["metrics"]["accuracy_delta"])
        self.assertTrue(Path(record["record_path"]).is_file())

    def test_agentarts_json_fixtures_are_strict_and_bad_version_is_rejected(self):
        requests = ROOT / "tests/module_b/fixtures/requests"
        for name in ("assess_ok", "optimize_ok", "missing_budget", "invalid_skill_dag", "demo_only_real_planning_refused", "unreachable_no_expansion_value", "stress_fixed_vs_adaptive", "compare_wrong_data_version"):
            raw = json.loads((requests / f"{name}.json").read_text(encoding="utf-8"))
            TaskRequest(**raw)
        missing = TaskRequest(**json.loads((requests / "missing_budget.json").read_text(encoding="utf-8")))
        with self.assertRaises(ScenarioError): execute_task(missing)
        invalid = TaskRequest(**json.loads((requests / "invalid_skill_dag.json").read_text(encoding="utf-8")))
        with self.assertRaises(PlanRejectedError): execute_task(invalid)
        refused = TaskRequest(**json.loads((requests / "demo_only_real_planning_refused.json").read_text(encoding="utf-8")))
        with self.assertRaises(ScenarioError): execute_task(refused)
        unreachable = TaskRequest(**json.loads((requests / "unreachable_no_expansion_value.json").read_text(encoding="utf-8")))
        unreachable_result = execute_task(unreachable)
        self.assertEqual(unreachable_result["result_detail"]["optimization"]["selected_option_ids"], [])
        wrong = TaskRequest(**json.loads((requests / "compare_wrong_data_version.json").read_text(encoding="utf-8")))
        with self.assertRaises(ScenarioError):
            execute_task(wrong)

    def test_fastapi_capabilities_and_async_job(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        client = TestClient(app)
        caps = client.get("/planning/capabilities")
        self.assertEqual(caps.status_code, 200)
        self.assertEqual(len(caps.json()["skills"]), 12)
        queued = client.post("/planning/tasks", json={"task_type": "assess", "fixture_id": "toy_case", "analysis_mode": "demo"})
        self.assertEqual(queued.status_code, 202)
        job_id = queued.json()["job_id"]
        deadline = time.time() + 5
        job = client.get(f"/planning/jobs/{job_id}").json()
        while job["status"] in {"QUEUED", "RUNNING"} and time.time() < deadline:
            time.sleep(0.01)
            job = client.get(f"/planning/jobs/{job_id}").json()
        self.assertEqual(job["status"], "SUCCEEDED", job)
        result = client.get(f"/planning/results/{job['result_id']}")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["verification_status"], "PASS_DEMO")


if __name__ == "__main__":
    unittest.main()
