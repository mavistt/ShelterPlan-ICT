from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from backend.storage import JsonResultStore, canonical_hash


class StreamingStorageTests(unittest.TestCase):
    def test_canonical_hash_is_byte_compatible_with_legacy_json_dumps(self):
        value = {
            "region": "东区街道",
            "values": [0, 1.25, None, True, {"z": "雪", "a": [3, 2, 1]}],
        }
        legacy_bytes = json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        self.assertEqual(canonical_hash(value), hashlib.sha256(legacy_bytes).hexdigest())

    def test_result_store_streams_the_same_pretty_json_document(self):
        document = {"region": "东区街道", "trace": [{"node": "S01", "ok": True}]}
        result_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as directory:
            store = JsonResultStore(Path(directory))
            path = store.save(result_id, document)
            expected = json.dumps(document, ensure_ascii=False, indent=2, default=str)
            self.assertEqual(path.read_text(encoding="utf-8"), expected)
            self.assertEqual(store.get(result_id), document)


if __name__ == "__main__":
    unittest.main()
