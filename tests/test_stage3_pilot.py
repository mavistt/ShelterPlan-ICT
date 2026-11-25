from __future__ import annotations

import unittest

from backend.graph.executor import load_fixture
from benchmark.shelterplan_eval.stage3_pilot import (
    DEFAULT_CASE_IDS,
    _evaluate_output,
    build_tasks,
    build_task,
    configured_http_route,
    enumerate_budget_oracle,
    load_pilot,
)


class Stage3PilotTests(unittest.TestCase):
    def test_task_factory_repairs_negative_and_parent_contracts(self):
        tasks = {task["scenario_id"]: task for task in build_tasks()}
        self.assertEqual(set(tasks), set(DEFAULT_CASE_IDS))
        multi = tasks["ST21-shiqi-004"]
        self.assertEqual(multi["task_type"], "multi_round")
        self.assertNotIn("parent_scenario_id", multi["rounds"][1]["payload_template"])
        invalid = tasks["ST21-shiqi-016"]["b_request_payload"]
        self.assertTrue(invalid["improvement_options"][0]["shelter_id"].startswith("invalid-"))
        illegal_constraint = build_task(next(row for row in load_pilot() if row["scenario_id"] == "ST21-shiqi-006"))
        self.assertEqual(illegal_constraint["b_request_payload"]["scenario"]["max_walk_time_min"], -5)
        safe = tasks["ST21-east_district-011"]
        self.assertEqual(safe["b_request_payload"]["task_type"], "assess")
        clarify = tasks["ST21-east_district-029"]
        self.assertEqual(clarify["b_request_payload"]["task_type"], "optimize")
        self.assertNotIn("parameters", clarify["b_request_payload"])

    def test_route_is_explicit_and_no_east_fallback(self):
        route = configured_http_route()
        self.assertEqual(route["configured_region_id"], "shiqi")
        tasks = {task["scenario_id"]: task for task in build_tasks()}
        self.assertEqual(tasks["ST21-shiqi-000"]["http_route_status"], "SUPPORTED")
        self.assertEqual(tasks["ST21-east_district-001"]["http_route_status"], "UNSUPPORTED")
        self.assertEqual(tasks["ST21-east_district-001"]["execution_route"], "explicit_adapter_direct_solver")

    def test_independent_oracle_enumerates_small_budget_fixture(self):
        data, scenario, options = load_fixture("tests/module_b/fixtures/toy_case.json")
        result = enumerate_budget_oracle(data, scenario, options)
        self.assertEqual(result["status"], "ORACLE_PROVEN")
        self.assertEqual(result["method"], "EXHAUSTIVE_OPTION_SUBSET_ENUMERATION")
        self.assertGreater(result["feasible_subset_count"], 0)

    def test_safe_stop_label_alone_is_not_a_score(self):
        task = {"expected_behavior": "SAFE_STOP", "task_type": "assess"}
        execution = {"status": "SUCCEEDED", "execution_trace_id": "trace-1", "result_summary": {}}
        evaluated = _evaluate_output(task, execution, safe_evidence={"safe_stop_supported_by_capacity_and_reachability": True})
        self.assertEqual(evaluated["status"], "UNSCORABLE")


if __name__ == "__main__":
    unittest.main()
