from __future__ import annotations

import unittest

from benchmark.shelterplan_eval.scenario_factory import (
    ALLOWED_REGIONS,
    generate_candidate,
    load_snapshots,
    validate_scenario,
)
from sources.preflight import build_manifest


class Stage21QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = load_snapshots()

    def test_dataset_content_hash_is_stable_across_audit_runs(self):
        self.assertEqual(build_manifest()["dataset_content_hash"], build_manifest()["dataset_content_hash"])

    def test_candidates_have_real_behaviors_and_unmeasured_execution_state(self):
        behaviors = set()
        for index in range(48):
            snapshot = self.snapshots[ALLOWED_REGIONS[index % 2]]
            scenario = generate_candidate(snapshot, index=index, seed=20261125 + index)
            behaviors.add(scenario["expected_behavior"])
            self.assertEqual(scenario["execution_status"], "NOT_EXECUTED")
            self.assertEqual(scenario["oracle_status"], "NOT_AVAILABLE")
            self.assertEqual(scenario["measured_difficulty"]["decision_complexity"], "NOT_MEASURED")
            self.assertLessEqual(len(scenario["improvement_options"]), 12)
            result = validate_scenario(scenario, snapshot, allow_manifest_blocked_demo=True)
            self.assertTrue(result["pilot_accepted"], result)
        self.assertTrue({"EXECUTE", "CLARIFY", "REJECT", "SAFE_STOP"}.issubset(behaviors))

    def test_budget_and_option_semantics_are_not_trivial(self):
        scenarios = [
            generate_candidate(self.snapshots["shiqi"], index=index, seed=20261125 + index)
            for index in range(6)
        ]
        self.assertTrue(any(s["difficulty_evidence"]["budget_competition"] for s in scenarios))
        self.assertTrue(any(s["difficulty_evidence"]["exclusive_group_count"] for s in scenarios))
        self.assertTrue(all(option["action"] == "capacity_expansion" for s in scenarios for option in s["improvement_options"]))


if __name__ == "__main__":
    unittest.main()
