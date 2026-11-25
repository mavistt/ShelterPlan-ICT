import unittest

from sources.admission_gate import evaluate_row


def base_row(**overrides):
    row = {
        "region_id": "shiqi",
        "shelter_id": "demo-1",
        "source_row_excel": "1",
        "source_file": "source.xls",
        "source_sheet": "sheet",
        "lon_wgs84": "113.38",
        "lat_wgs84": "22.52",
        "active": "True",
        "review_decision": "needs_review",
        "identity_status": "needs_review",
        "field_mapping_status": "needs_review",
        "coordinate_status": "approximate",
        "geocode_status": "candidate",
        "capacity_source_status": "original",
        "capacity_verification_status": "needs_review",
        "current_open_status": "assumed_open",
        "data_status": "DEMO_ONLY",
        "verified_status": "demo_unverified",
        "duplicate_source_name_candidate": "False",
        "duplicate_source_specific_address_candidate": "False",
        "reviewer_notes": "",
    }
    row.update(overrides)
    return row


class AdmissionGateTests(unittest.TestCase):
    def test_demo_is_allowed_with_explicit_warnings(self):
        result = evaluate_row(
            base_row(),
            region_meta={
                "region_id": "shiqi",
                "enabled": True,
                "data_version": "v2-shiqi",
                "boundary_status": "demo_analysis_boundary",
            },
            snap={"snap_distance_m": "50", "snap_ok": "True"},
            geocode={"status": "candidate"},
            source_disposition={"review_decision": "needs_review"},
            duplicate_rows=[],
        )
        self.assertTrue(result["eligible_for_demo"])
        self.assertFalse(result["eligible_for_verified"])
        self.assertEqual(result["admission_class"], "demo_eligible_with_warnings")
        self.assertIn("coordinate_not_verified", result["demo_blockers"])

    def test_snap_failure_is_excluded(self):
        result = evaluate_row(
            base_row(),
            region_meta={
                "region_id": "xiaolan",
                "enabled": True,
                "data_version": "v2-xiaolan",
                "boundary_status": "demo_analysis_boundary",
            },
            snap={"snap_distance_m": "424.726", "snap_ok": "False"},
            geocode={"status": "candidate"},
            source_disposition={"review_decision": "needs_review"},
            duplicate_rows=[],
        )
        self.assertFalse(result["eligible_for_demo"])
        self.assertEqual(result["admission_class"], "excluded")
        self.assertIn("shelter_snap_too_far", result["fatal_blockers"])

    def test_disabled_region_is_not_normal_demo_admission(self):
        result = evaluate_row(
            base_row(region_id="xiaolan"),
            region_meta={
                "region_id": "xiaolan",
                "enabled": False,
                "data_version": "v2-xiaolan",
                "boundary_status": "demo_analysis_boundary",
            },
            snap={"snap_distance_m": "50", "snap_ok": "True"},
            geocode={"status": "candidate"},
            source_disposition={"review_decision": "needs_review"},
            duplicate_rows=[],
        )
        self.assertFalse(result["eligible_for_demo"])
        self.assertEqual(result["admission_class"], "needs_review")
        self.assertIn("region_disabled", result["demo_blockers"])

    def test_verified_requires_independent_evidence(self):
        row = base_row(
            identity_status="verified",
            field_mapping_status="verified",
            coordinate_status="verified",
            geocode_status="verified",
            capacity_source_status="original",
            capacity_verification_status="verified",
            current_open_status="confirmed",
            data_status="VERIFIED",
            verified_status="verified",
            review_decision="approved",
        )
        result = evaluate_row(
            row,
            region_meta={
                "region_id": "shiqi",
                "enabled": True,
                "data_version": "v2-shiqi",
                "boundary_status": "official",
            },
            snap={"snap_distance_m": "50", "snap_ok": "True"},
            geocode={"status": "verified"},
            source_disposition={"review_decision": "approved"},
            duplicate_rows=[],
        )
        self.assertTrue(result["eligible_for_demo"])
        self.assertTrue(result["eligible_for_verified"])

    def test_missing_provenance_is_structural_failure(self):
        result = evaluate_row(
            base_row(source_file="", source_sheet=""),
            region_meta={
                "region_id": "shiqi",
                "enabled": True,
                "data_version": "v2-shiqi",
                "boundary_status": "demo_analysis_boundary",
            },
            snap={"snap_distance_m": "50", "snap_ok": "True"},
            geocode={"status": "candidate"},
            source_disposition={"review_decision": "needs_review"},
            duplicate_rows=[],
        )
        self.assertFalse(result["eligible_for_demo"])
        self.assertEqual(result["admission_class"], "excluded")


if __name__ == "__main__":
    unittest.main()
