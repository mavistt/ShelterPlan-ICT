"""Integrity checks for the V2.1 generalisation set.

These run on every test invocation. They do not measure quality; they make it
impossible to quietly edit the frozen cases, drop a case, or reuse a V2 prompt.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CASES = Path(__file__).resolve().parent / "cases" / "v21_cases.json"
FREEZE = Path(__file__).resolve().parent / "cases" / "v21_freeze.json"
V2_CASES = Path(__file__).resolve().parent / "cases" / "v2_cases.json"
V1_CASES = Path(__file__).resolve().parent / "cases" / "nl_cases.json"

GRADEABLE = {"intent", "ops", "task_types", "params", "no_result", "no_tasks",
             "clarify_fields", "numeric_consistent", "demo_disclosed",
             "must_contain_any", "must_not_contain", "max_planning_tasks"}
REQUIRED_GROUPS = {"compound", "vague", "units", "undo", "mixed", "compare",
                   "unknown", "illegal", "fabricate", "bypass"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def user_texts(document: dict) -> set[str]:
    return {turn["user"].strip()
            for case in document["cases"] for turn in case["turns"]}


def test_case_count_and_groups():
    document = load(CASES)
    cases = document["cases"]
    assert len(cases) >= 40
    assert {case["group"] for case in cases} == REQUIRED_GROUPS
    for group in REQUIRED_GROUPS:
        assert sum(1 for case in cases if case["group"] == group) >= 4, group


def test_ids_are_unique_and_well_formed():
    cases = load(CASES)["cases"]
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert all(case_id.startswith("V21-") for case_id in ids)
    assert all(case.get("note") for case in cases), "every case states what it is for"


def test_every_case_is_gradeable():
    for case in load(CASES)["cases"]:
        assert case["turns"] and case["turns"][0]["user"].strip()
        expect = case.get("expect") or {}
        assert GRADEABLE & set(expect), f"{case['case_id']} has no gradeable expectation"


def test_no_prompt_is_reused_from_v1_or_v2():
    ours = user_texts(load(CASES))
    for path in (V2_CASES, V1_CASES):
        if not path.exists():
            continue
        overlap = ours & user_texts(load(path))
        assert not overlap, f"reused prompts from {path.name}: {sorted(overlap)[:3]}"


def test_cases_are_not_numeric_variants_of_each_other():
    """Two cases may not differ only by a number."""
    import re
    seen: dict[str, str] = {}
    for case in load(CASES)["cases"]:
        skeleton = " | ".join(turn["user"] for turn in case["turns"])
        skeleton = re.sub(r"\d+(?:\.\d+)?", "<n>", skeleton)
        skeleton = re.sub(r"zs_[0-9a-z]+", "<id>", skeleton)
        assert skeleton not in seen, (case["case_id"], seen.get(skeleton))
        seen[skeleton] = case["case_id"]


def test_frozen_hash_matches():
    freeze = load(FREEZE)
    actual = hashlib.sha256(CASES.read_bytes()).hexdigest()
    assert actual == freeze["cases_sha256"], (
        "v21_cases.json changed after freeze; create a v21b set instead of rescoring this one")
    grading = FREEZE.parents[3] / freeze["grading_path"]
    assert hashlib.sha256(grading.read_bytes()).hexdigest() == freeze["grading_sha256"]


def test_freeze_records_the_authoring_process():
    freeze = load(FREEZE)
    assert freeze["case_count"] == len(load(CASES)["cases"])
    assert "before the first scored run" in freeze["authoring"]
    assert freeze["dev_set"]["purpose"]
