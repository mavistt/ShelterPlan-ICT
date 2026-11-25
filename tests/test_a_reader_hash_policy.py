from __future__ import annotations

import unittest

from backend.adapters.a_reader import _validate_manifest_artifact
from sources.preflight import ROOT


class AReaderHashPolicyTests(unittest.TestCase):
    def test_text_manifest_can_match_reconstructed_crlf(self):
        import json
        manifest = json.loads((ROOT / "data/processed/regions/shiqi/manifest.json").read_text(encoding="utf-8"))
        result = _validate_manifest_artifact(
            ROOT,
            "data/processed/regions/shiqi/shelters.geojson",
            manifest["files"],
        )
        self.assertEqual(result["match_mode"], "MATCH_RECONSTRUCTED_CRLF")

    def test_binary_manifest_remains_raw_strict(self):
        import json
        manifest = json.loads((ROOT / "data/processed/regions/shiqi/manifest.json").read_text(encoding="utf-8"))
        expected = dict(manifest["files"]["data/processed/regions/shiqi/demand_nodes.parquet"])
        expected["sha256"] = "0" * 64
        with self.assertRaises(Exception) as ctx:
            _validate_manifest_artifact(
                ROOT,
                "data/processed/regions/shiqi/demand_nodes.parquet",
                {"data/processed/regions/shiqi/demand_nodes.parquet": expected},
            )
        self.assertEqual(ctx.exception.details["status"], "CONTENT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
