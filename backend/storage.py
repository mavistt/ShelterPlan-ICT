"""Versioned scenario/result storage and dependency-aware in-process cache."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any
import uuid


def canonical_hash(value: Any) -> str:
    # Avoid materializing a second full-size string/bytes copy for large GIS
    # result payloads. JSONEncoder.iterencode emits the same canonical stream
    # as json.dumps with these options, so existing hashes remain stable.
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), default=str)
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


class DependencyCache:
    def __init__(self):
        self.values: dict[str, Any] = {}
        self.lock = threading.RLock()

    def get(self, key: str):
        with self.lock:
            return self.values.get(key)

    def put(self, key: str, value: Any) -> None:
        with self.lock:
            self.values[key] = value

    def clear(self) -> None:
        with self.lock:
            self.values.clear()


class JsonResultStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def save(self, result_id: str, document: dict) -> Path:
        result_id = str(uuid.UUID(result_id))
        path = self.root / f"{result_id}.json"
        with self.lock:
            encoder = json.JSONEncoder(ensure_ascii=False, indent=2, default=str)
            with path.open("w", encoding="utf-8", newline="") as handle:
                for chunk in encoder.iterencode(document):
                    handle.write(chunk)
        return path

    def get(self, result_id: str) -> dict | None:
        try:
            result_id = str(uuid.UUID(result_id))
        except (ValueError, AttributeError, TypeError):
            return None
        path = self.root / f"{result_id}.json"
        if not path.is_file():
            return None
        with self.lock:
            return json.loads(path.read_text(encoding="utf-8"))


def save_scenario_version(scenario: dict, data_version: str, output_root: str | Path = "outputs/module_b/scenarios") -> dict:
    """Persist an immutable scenario node keyed by its full content hash."""
    scenario_hash = canonical_hash({"scenario": scenario, "data_version": data_version})
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    document = {"scenario_hash": scenario_hash, "data_version": data_version, **scenario}
    path = root / f"{scenario_hash}.json"
    if not path.exists():
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"scenario_hash": scenario_hash, "path": str(path)}


def find_scenario_version(scenario_id: str, output_root: str | Path = "outputs/module_b/scenarios") -> dict | None:
    root = Path(output_root)
    if not root.is_dir():
        return None
    for path in root.glob("*.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if document.get("scenario_id") == scenario_id:
            return document
    return None
