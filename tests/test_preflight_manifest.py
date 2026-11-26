from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sources.preflight import (
    ROOT,
    normalized_text_sha256,
    reconstructed_crlf_bytes,
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
        self.assertEqual(result["status"], "CONTENT_MISMATCH")
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
        self.assertEqual(result["status"], "CONTENT_MISMATCH")
        self.assertFalse(result["raw_match"])
        self.assertTrue(result["normalized_text_match"])

    def test_text_crlf_reconstruction_matches_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = "sample.json"
            contents = b"{\"safe\":true}\n"
            (root / path).write_bytes(contents)
            rebuilt = contents.replace(b"\n", b"\r\n")
            expected = {
                "bytes": len(rebuilt),
                "sha256": hashlib.sha256(rebuilt).hexdigest(),
            }
            with patch("sources.preflight.ROOT", root):
                result = verify_declared_file(path, expected)
            self.assertEqual(result["status"], "MATCH_RECONSTRUCTED_CRLF")
            self.assertTrue(result["reconstructed_crlf_match"])
            self.assertEqual(len(rebuilt), expected["bytes"])

    def test_missing_file_is_reported(self):
        result = verify_declared_file(
            "data/processed/regions/shiqi/not-present.parquet",
            {"bytes": 10, "sha256": "0" * 64},
        )
        self.assertEqual(result["status"], "MISSING")
        self.assertIsNone(result["actual_bytes"])
        self.assertIsNone(result["actual_sha256"])

    def test_real_manifest_matches_current_ui_bridge_checkout(self):
        result = verify_region_manifest("shiqi")
        self.assertTrue(result["all_declared_files_match"])
        self.assertEqual(result["failure_count"], 0)
        self.assertEqual(result["content_failure_count"], 0)
        self.assertGreater(result["raw_match_count"], 0)

    def test_manifest_failure_has_file_level_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = "sample.json"
            (root / path).write_bytes(b"{\"safe\":true}\n")
            expected = {"bytes": 99, "sha256": "0" * 64}
            with patch("sources.preflight.ROOT", root):
                result = verify_declared_file(path, expected)
        self.assertEqual(result["status"], "CONTENT_MISMATCH")
        self.assertFalse(result["raw_match"])
        self.assertEqual(result["path"], "sample.json")
        self.assertEqual(result["expected_bytes"], 99)
        self.assertIsNotNone(result["actual_bytes"])
        self.assertEqual(result["expected_sha256"], "0" * 64)
        self.assertIsNotNone(result["actual_sha256"])


if __name__ == "__main__":
    unittest.main()
