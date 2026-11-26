from __future__ import annotations

import unittest
import copy

from backend.graph.executor import load_fixture
from benchmark.shelterplan_eval.stage3_pilot import (
    DEFAULT_CASE_IDS,
    _evaluate_output,
    build_tasks,
    build_task,
    configured_http_route,
    enumerate_budget_oracle,
    _multiround_state_checks,
    _vets,
    load_pilot,
)
from benchmark.shelterplan_eval.bc_adapter import build_disaster_recovery_payload, normalize_composite_result


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
        self.assertEqual(result["status"], "INDEPENDENT_ORACLE_PROVEN")
        self.assertIn("INDEPENDENT_DINIC", result["method"])
        self.assertEqual(result["proof_level"], "INDEPENDENT_FULL_INTEGER_MAX_FLOW")
        self.assertGreater(result["feasible_subset_count"], 0)

    def test_real_data_oracle_is_not_mislabeled_as_independent(self):
        tasks = {task["scenario_id"]: task for task in build_tasks()}
        self.assertEqual(tasks["ST21-east_district-001"]["gt_assertions"]["independent_oracle_required"], True)
        # Real A v2 has fractional demand/capacity and must never receive a
        # full independent integer-flow proof from the fixture-only method.
        self.assertNotEqual(tasks["ST21-east_district-001"]["data_version"], "synthetic_toy_v1")

    def test_multiround_checks_validate_hash_and_inherited_parameters(self):
        task = next(task for task in build_tasks() if task["scenario_id"] == "ST21-shiqi-004")
        parent_payload = task["rounds"][0]["payload"]
        child_payload = copy.deepcopy(task["rounds"][1]["payload_template"])
        child_payload["parent_scenario_id"] = parent_payload["scenario_id"]
        child_payload["scenario"]["parent_scenario_id"] = parent_payload["scenario_id"]
        from backend.models import PlanningScenario
        from backend.storage import canonical_hash
        parent = {"status": "SUCCEEDED", "scenario_id": parent_payload["scenario_id"], "region_id": "shiqi", "data_version": task["data_version"], "data_status": "DEMO_ONLY", "execution_trace_id": "p", "scenario_hash": canonical_hash({"scenario": PlanningScenario(**parent_payload["scenario"]).model_dump(), "data_version": parent_payload["data_version"]})}
        child = {"status": "SUCCEEDED", "scenario_id": child_payload["scenario_id"], "parent_scenario_id": parent_payload["scenario_id"], "region_id": "shiqi", "data_version": task["data_version"], "data_status": "DEMO_ONLY", "execution_trace_id": "c", "scenario_hash": canonical_hash({"scenario": PlanningScenario(**child_payload["scenario"]).model_dump(), "data_version": child_payload["data_version"]})}
        self.assertTrue(_multiround_state_checks(parent_payload, child_payload, parent, child, "shiqi", task["data_version"])["passed"])
        child["data_version"] = "wrong-version"
        self.assertFalse(_multiround_state_checks(parent_payload, child_payload, parent, child, "shiqi", task["data_version"])["passed"])

    def test_reject_requires_expected_code_and_phase(self):
        task = next(task for task in build_tasks() if task["scenario_id"] == "ST21-shiqi-016")
        passed = _evaluate_output(task, {"status": "FAILED", "error": {"error_code": "CANDIDATE_INVALID"}})
        self.assertEqual(passed["status"], "PASS")
        wrong = _evaluate_output(task, {"status": "FAILED", "error": {"error_code": "SCENARIO_INVALID"}})
        self.assertEqual(wrong["status"], "FAIL")

    def test_vets_is_binary_and_diagnostics_are_separate(self):
        task = {"expected_behavior": "EXECUTE"}
        evaluation = {"status": "PASS"}
        score = _vets(task, {"status": "SUCCEEDED", "execution_trace_id": "trace", "verification_status": "PASS_DEMO", "result_summary": {"served_demand": 10}, "elapsed_harness_ms": 2}, evaluation, {"status": "CONDITIONAL_SUBSET_OPTIMUM", "objective": {"served_demand": 10}})
        self.assertIs(score["vets_success"], True)
        self.assertIn("planning_quality", score)
        self.assertIn("evidence", score)
        self.assertNotIn("overall", score)

    def test_bc_adapter_preserves_explicit_region_version_and_no_fallback(self):
        task = next(task for task in build_tasks() if task["scenario_id"] == "ST21-shiqi-000")
        payload = build_disaster_recovery_payload(task, session_id="s-1", turn_id="t-1", idempotency_key="i-1")
        self.assertEqual(payload["region_id"], task["region_id"])
        self.assertEqual(payload["data_version"], task["data_version"])
        self.assertEqual(payload["client_session_id"], "s-1")
        normalized = normalize_composite_result({"result_detail": {"composite": {"contract": {"stage_results": [], "branch_decisions": [{"decision": "verify"}], "constraint_checks": []}}}, "execution_trace_id": "trace"})
        self.assertEqual(normalized["execution_trace_id"], "trace")
        self.assertEqual(normalized["branch_decisions"], [{"decision": "verify"}])

    def test_safe_stop_label_alone_is_not_a_score(self):
        task = {"expected_behavior": "SAFE_STOP", "task_type": "assess"}
        execution = {"status": "SUCCEEDED", "execution_trace_id": "trace-1", "result_summary": {}}
        evaluated = _evaluate_output(task, execution, safe_evidence={"safe_stop_supported_by_capacity_and_reachability": True})
        self.assertEqual(evaluated["status"], "UNSCORABLE")


if __name__ == "__main__":
    unittest.main()
