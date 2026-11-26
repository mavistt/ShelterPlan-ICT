from __future__ import annotations

import unittest
import hashlib
import json
import tempfile
from pathlib import Path

from backend.adapters.a_reader import (
    _artifact_bytes,
    _normalized_file_signature,
    _reconstructed_crlf_bytes,
    _validate_manifest_artifact,
)
from sources.preflight import ROOT


class AReaderHashPolicyTests(unittest.TestCase):
    def test_current_text_manifest_reports_actual_match_mode(self):
        manifest = json.loads((ROOT / "data/processed/regions/shiqi/manifest.json").read_text(encoding="utf-8"))
        result = _validate_manifest_artifact(
            ROOT,
            "data/processed/regions/shiqi/shelters.geojson",
            manifest["files"],
        )
        self.assertIn(result["match_mode"], {"MATCH_RAW", "MATCH_RECONSTRUCTED_CRLF"})
        self.assertTrue(result["matched"])

    def test_text_manifest_can_match_reconstructed_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"{\"safe\":true}\n"
            (root / "sample.json").write_bytes(content)
            reconstructed = content.replace(b"\n", b"\r\n")
            record = {"sample.json": {
                "bytes": len(reconstructed),
                "sha256": hashlib.sha256(reconstructed).hexdigest(),
            }}
            result = _validate_manifest_artifact(root, "sample.json", record)
            self.assertEqual(result["match_mode"], "MATCH_RECONSTRUCTED_CRLF")

    def test_binary_manifest_remains_raw_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"\x00binary\xff"
            (root / "sample.parquet").write_bytes(payload)
            expected = {"sample.parquet": {
                "bytes": len(payload),
                "sha256": "0" * 64,
            }}
            with self.assertRaises(Exception) as ctx:
                _validate_manifest_artifact(root, "sample.parquet", expected)
            self.assertEqual(ctx.exception.details["status"], "CONTENT_MISMATCH")

    def test_streamed_text_signatures_match_legacy_newline_conversion_across_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "line_endings.json"
            raw = b"\xef\xbb\xbf" + "row\r\n中\rvalue\nlast\r".encode("utf-8")
            path.write_bytes(raw)
            canonical = _artifact_bytes(path)
            reconstructed = _reconstructed_crlf_bytes(path)
            self.assertIsNotNone(reconstructed)
            self.assertEqual(
                _normalized_file_signature(path, b"\n", chunk_size=4),
                (len(canonical), hashlib.sha256(canonical).hexdigest()),
            )
            self.assertEqual(
                _normalized_file_signature(path, b"\r\n", chunk_size=4),
                (len(reconstructed), hashlib.sha256(reconstructed).hexdigest()),
            )


if __name__ == "__main__":
    unittest.main()
