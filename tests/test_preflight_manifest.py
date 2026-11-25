from __future__ import annotations

import json
import unittest
from pathlib import Path

from sources.preflight import (
    ROOT,
    normalized_text_sha256,
    verify_declared_file,
    verify_region_manifest,
)


class ManifestVerificationTests(unittest.TestCase):
    def test_real_parquet_manifest_entry_matches_raw_bytes(self):
        region_manifest = json.loads(
            (ROOT / "data/processed/regions/shiqi/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        path = "data/processed/regions/shiqi/demand_nodes.parquet"
        result = verify_declared_file(path, region_manifest["files"][path])
        self.assertEqual(result["status"], "MATCH_RAW")
        self.assertTrue(result["raw_match"])
        self.assertIsNone(result["normalized_text_sha256"])

    def test_binary_hash_mismatch_is_not_normalized_away(self):
        region_manifest = json.loads(
            (ROOT / "data/processed/regions/shiqi/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        path = "data/processed/regions/shiqi/demand_nodes.parquet"
        expected = dict(region_manifest["files"][path])
        expected["sha256"] = "0" * 64
        result = verify_declared_file(path, expected)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["allowed_text_normalization"])
        self.assertIsNone(result["normalized_text_sha256"])

    def test_text_normalized_hash_is_explicitly_distinguished(self):
        path = "configs/regions.yaml"
        actual_path = ROOT / path
        normalized_hash = normalized_text_sha256(actual_path)
        self.assertIsNotNone(normalized_hash)
        result = verify_declared_file(
            path,
            {"bytes": actual_path.stat().st_size + 1, "sha256": normalized_hash},
        )
        self.assertEqual(result["status"], "MATCH_NORMALIZED_TEXT")
        self.assertFalse(result["raw_match"])
        self.assertTrue(result["normalized_text_match"])

    def test_missing_file_is_reported(self):
        result = verify_declared_file(
            "data/processed/regions/shiqi/not-present.parquet",
            {"bytes": 10, "sha256": "0" * 64},
        )
        self.assertEqual(result["status"], "MISSING")
        self.assertIsNone(result["actual_bytes"])
        self.assertIsNone(result["actual_sha256"])

    def test_real_manifest_failure_has_file_level_evidence(self):
        result = verify_region_manifest("shiqi")
        self.assertFalse(result["all_declared_files_match"])
        self.assertGreater(result["failure_count"], 0)
        failed = [
            item for item in result["file_checks"] if item["status"] == "FAIL"
        ]
        self.assertTrue(failed)
        self.assertTrue(
            all(
                item["path"]
                and item["expected_bytes"] is not None
                and item["actual_bytes"] is not None
                and item["expected_sha256"]
                and item["actual_sha256"]
                for item in failed
            )
        )


if __name__ == "__main__":
    unittest.main()
