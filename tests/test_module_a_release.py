from __future__ import annotations

import unittest

from benchmark.shelterplan_eval.difficulty_discovery import _scenario_metrics
from benchmark.shelterplan_eval.evaluator import aggregate_results, evaluate_task
from benchmark.shelterplan_eval.oracle import enumerate_portfolios, independent_max_flow
from benchmark.shelterplan_eval.release import build_task


class ModuleAOracleTests(unittest.TestCase):
    def test_independent_flow_and_portfolio_enumeration(self):
        demands = {"d1": 3.0, "d2": 2.0}
        shelters = {"s1": 3.0, "s2": 3.0}
        edges = [("d1", "s1"), ("d1", "s2"), ("d2", "s2")]
        result = independent_max_flow(demands, shelters, edges, scale=1)
        self.assertEqual(result.served, 5.0)
        portfolio = enumerate_portfolios(
            [{"option_id": "o1", "shelter_id": "s1", "delta_capacity_persons": 2, "cost_units": 1}],
            1,
            demands,
            {"s1": 1.0, "s2": 1.0},
            edges,
            scale=1,
        )
        self.assertEqual(portfolio["status"], "INDEPENDENT_ORACLE_PROVEN")
        self.assertEqual(portfolio["best"]["option_ids"], ["o1"])


class ModuleADifficultyTests(unittest.TestCase):
    def test_structural_evidence_marks_unverified_recovery(self):
        scenario = {
            "scenario_id": "SYN-RECOVERY",
            "region_id": "demo",
            "scenario_family": "multi_stage_recovery",
            "expected_behavior": "EXECUTE",
            "interaction_type": "parameter_edit",
            "max_walk_time_min": 15,
            "population_demand_ratio": 1.0,
            "budget_units": 5,
            "improvement_options": [
                {"option_id": "o1", "shelter_id": "s1", "cost_units": 3, "eligible": True, "exclusive_group": None},
                {"option_id": "o2", "shelter_id": "s1", "cost_units": 3, "eligible": True, "exclusive_group": "g1"},
            ],
            "damaged_shelter_ids": [],
            "disabled_shelter_ids": [],
            "recovery_stages": {"stage_1": ["o1"], "stage_2": ["o2"]},
            "design_difficulty": {"decision_complexity": "high"},
        }
        observation = {
            "data_status": "DEMO_ONLY",
            "data_version": "demo-v1",
            "total_population": 100,
            "capacities": {"s1": 20, "s2": 30},
            "thresholds": {"15": {"reachable_demand_nodes": 2, "reachable_population": 80, "by_shelter": {"s1": ["d1"], "s2": ["d1", "d2"]}}},
            "source_files": {},
        }
        evidence = _scenario_metrics(scenario, observation)
        self.assertEqual(evidence["oracle"]["status"], "NOT_PROVEN")
        self.assertIn("MULTI_STAGE_STRUCTURE_ONLY_NO_DAMAGE_OR_DISABLED_INPUT", evidence["evidence"]["warnings"])
        self.assertEqual(evidence["evidence"]["exclusive_group_count"], 1)


class ModuleAEvaluatorTests(unittest.TestCase):
    def task(self, expected: str = "EXECUTE", parent: bool = False):
        return {
            "task_id": "A-SYN",
            "expected_behavior": expected,
            "gt_assertions": {
                "expected_behavior": expected,
                "region_id": "shiqi",
                "data_version": "demo-v1",
                "allowed_option_ids": ["o1"],
                "disabled_shelter_ids": [],
                "parent_state_required": parent,
                "reject_contract": {"expected_error_codes": ["CANDIDATE_INVALID"], "solver_must_not_run": True},
            },
        }

    def test_execute_hard_gate(self):
        output = {
            "status": "SUCCEEDED", "behavior": "EXECUTE", "solver_started": True,
            "trace_present": True, "verified": True, "region_id": "shiqi",
            "data_version": "demo-v1", "selected_option_ids": ["o1"],
            "disabled_shelter_ids": [], "oracle_status": "NOT_PROVEN",
        }
        result = evaluate_task(self.task(), output)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["dimensions"]["U"], 1.0)

    def test_reject_requires_no_solver(self):
        result = evaluate_task(self.task("REJECT"), {
            "status": "FAILED", "behavior": "REJECT", "solver_started": False,
            "error_code": "CANDIDATE_INVALID",
        })
        self.assertEqual(result["status"], "PASS")

    def test_safe_stop_without_evidence_is_unscorable(self):
        result = evaluate_task(self.task("SAFE_STOP"), {
            "status": "SUCCEEDED", "behavior": "SAFE_STOP", "solver_started": False,
            "safe_stop_supported": False,
        })
        self.assertEqual(result["status"], "UNSCORABLE")

    def test_parent_child_lineage_is_hard_gate(self):
        output = {
            "status": "SUCCEEDED", "behavior": "EXECUTE", "solver_started": True,
            "trace_present": True, "verified": True, "region_id": "shiqi",
            "data_version": "demo-v1", "selected_option_ids": ["o1"],
            "disabled_shelter_ids": [], "state_lineage": {
                "parent_trace_present": True, "child_trace_present": True,
                "parent_scenario_id": "p", "child_parent_scenario_id": "p-child",
                "scenario_hash_match": True, "region_match": True, "data_version_match": True,
            },
        }
        result = evaluate_task(self.task(parent=True), output)
        self.assertEqual(result["status"], "PASS")

    def test_aggregate_does_not_count_unscorable_as_success(self):
        rows = [evaluate_task(self.task(), None), evaluate_task(self.task("REJECT"), {
            "status": "FAILED", "behavior": "REJECT", "solver_started": False,
            "error_code": "CANDIDATE_INVALID",
        })]
        summary = aggregate_results(rows)
        self.assertEqual(summary["status_counts"]["NOT_RUN"], 1)
        self.assertEqual(summary["status_counts"]["PASS"], 1)
        self.assertEqual(summary["verified_end_to_end_success_rate"], 0.5)


class ModuleAReleaseTaskTests(unittest.TestCase):
    def test_task_contains_a_only_provenance_and_frozen_split(self):
        scenario = {
            "scenario_id": "SYN-000", "region_id": "shiqi", "data_version": "demo-v1",
            "dataset_content_hash": "dataset", "source_manifest_sha256": "audit",
            "a_v2_manifest_sha256": "region", "scenario_family": "coverage_baseline",
            "expected_behavior": "EXECUTE", "interaction_type": "complete_request",
            "max_walk_time_min": 15, "improvement_options": [], "disabled_shelter_ids": [],
            "budget_units": 0,
        }
        difficulty = {"scenario_id": "SYN-000", "oracle": {"status": "NOT_PROVEN"}}
        task = build_task(scenario, difficulty, source_manifest_sha256="release", region_manifest={"data_status": "DEMO_ONLY"})
        self.assertEqual(task["split"], "frozen_eval")
        self.assertFalse(task["provenance"]["verified_for_release"])
        self.assertEqual(task["oracle"]["status"], "NOT_PROVEN")


if __name__ == "__main__":
    unittest.main()
