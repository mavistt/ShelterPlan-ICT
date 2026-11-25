from __future__ import annotations

import unittest

from benchmark.shelterplan_eval.scenario_factory import (
    generate_candidate,
    load_snapshots,
    validate_scenario,
)


class ScenarioFactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = load_snapshots()
        cls.shiqi = cls.snapshots["shiqi"]

    def candidate(self, index: int = 0):
        return generate_candidate(self.snapshots["shiqi"], index=index, seed=9000 + index)

    def test_same_seed_is_reproducible(self):
        left = generate_candidate(self.shiqi, index=1, seed=12345)
        right = generate_candidate(self.shiqi, index=1, seed=12345)
        self.assertEqual(left, right)

    def test_cross_region_data_mix_is_rejected(self):
        scenario = self.candidate()
        result = validate_scenario(
            scenario,
            self.snapshots["east_district"],
            allow_manifest_blocked_demo=True,
        )
        self.assertIn("region_id_mismatch", result["errors"])
        self.assertIn("data_version_mismatch", result["errors"])

    def test_disabled_region_is_rejected(self):
        scenario = self.candidate()
        xiaolan = self.snapshots["xiaolan"]
        scenario["region_id"] = "xiaolan"
        scenario["data_version"] = xiaolan.data_version
        scenario["source_manifest_sha256"] = xiaolan.source_manifest_sha256
        scenario["a_v2_manifest_sha256"] = xiaolan.a_v2_manifest_sha256
        result = validate_scenario(
            scenario,
            xiaolan,
            allow_manifest_blocked_demo=True,
        )
        self.assertFalse(result["pilot_accepted"])
        self.assertIn("disabled_region", result["errors"])

    def test_api_candidate_limit_is_rejected(self):
        scenario = self.candidate()
        scenario["shelter_ids"] = list(self.shiqi.normal_candidate_ids[:13])
        result = validate_scenario(
            scenario,
            self.shiqi,
            allow_manifest_blocked_demo=True,
        )
        self.assertIn("api_candidate_limit_exceeded", result["errors"])

    def test_invalid_shelter_id_is_rejected(self):
        scenario = self.candidate(index=7)
        result = validate_scenario(
            scenario,
            self.shiqi,
            allow_manifest_blocked_demo=True,
        )
        self.assertIn("invalid_shelter_id_in_request", result["errors"])
        self.assertFalse(result["pilot_accepted"])

    def test_illegal_constraint_is_rejected(self):
        scenario = self.candidate(index=8)
        result = validate_scenario(
            scenario,
            self.shiqi,
            allow_manifest_blocked_demo=True,
        )
        self.assertIn("illegal_constraint_in_request", result["errors"])
        self.assertFalse(result["pilot_accepted"])

    def test_every_assumption_has_a_source_label(self):
        scenario = self.candidate()
        scenario["assumptions"][0]["source_label"] = ""
        result = validate_scenario(
            scenario,
            self.shiqi,
            allow_manifest_blocked_demo=True,
        )
        self.assertTrue(any("missing_assumption_source" in error for error in result["errors"]))

    def test_duplicate_fingerprint_is_rejected(self):
        scenario = self.candidate()
        result = validate_scenario(
            scenario,
            self.shiqi,
            seen_fingerprints={scenario["fingerprint"]},
            allow_manifest_blocked_demo=True,
        )
        self.assertIn("duplicate_or_high_similarity_scenario", result["errors"])

    def test_manifest_error_is_not_verified_or_strictly_accepted(self):
        scenario = self.candidate()
        strict = validate_scenario(scenario, self.shiqi)
        self.assertIn("a_v2_manifest_mismatch", strict["errors"])
        self.assertFalse(strict["eligible_for_verified"])
        demo = validate_scenario(
            scenario,
            self.shiqi,
            allow_manifest_blocked_demo=True,
        )
        self.assertTrue(demo["pilot_accepted"])
        self.assertFalse(demo["verified_for_release"])
        self.assertEqual(demo["acceptance_class"], "accepted_demo_only_manifest_blocked")

    def test_historical_version_is_rejected(self):
        scenario = self.candidate()
        scenario["data_version"] = "historical-v2"
        result = validate_scenario(
            scenario,
            self.shiqi,
            allow_manifest_blocked_demo=True,
        )
        self.assertIn("data_version_mismatch", result["errors"])
        self.assertIn("non_a_v2_or_historical_data_version", result["errors"])

    def test_six_task_families_are_represented(self):
        families = {
            generate_candidate(self.shiqi, index=index, seed=100 + index)["scenario_family"]
            for index in range(6)
        }
        self.assertEqual(
            families,
            {
                "coverage_baseline",
                "budget_tradeoff",
                "capacity_competition",
                "facility_disruption",
                "multi_stage_recovery",
                "clarification_and_safe_stop",
            },
        )


if __name__ == "__main__":
    unittest.main()
