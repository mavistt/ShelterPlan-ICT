"""Freeze a non-author test set before it is ever run.

Why this exists
---------------
V2.1's generalisation set was still written by the person who wrote the code.
Once you know that, "79.8%" has an obvious ceiling: the author also wrote the
answers. The only way past it is somebody else's sentences.

This script is the machinery for that, deliberately separate from the case file:

    python -m tests.module_c.freeze_holdout path/to/incoming.json --author "同事 A"

It validates the submission, refuses to touch an already-frozen set, and writes a
sidecar `*_freeze.json` with the sha256 of the exact bytes that will be scored.
`run_v21.py` can then be pointed at it with `--cases ... ` (the freeze check is
skipped for non-frozen files only when `--allow-unfrozen` is passed explicitly).

The expected format is the same as `v21_cases.json`:

    {"schema_version": 2,
     "cases": [{"case_id": "X-01", "group": "plan", "capability": "assess",
                "turns": [{"user": "..."}],
                "expect": {"task_types": ["assess"], "numeric_consistent": true}}]}

`expect` may be left empty (`{}`) if the author of the sentences does not want to
write grading rules; the runner will then only record what happened. That is
weaker evidence, but it is honest evidence, and it is still useful: "20 sentences
a colleague typed, none of them crashed or invented a number" is a real result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
MIN_CASES = 20


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(payload: dict) -> list[str]:
    problems: list[str] = []
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return ["`cases` must be a list"]
    if len(cases) < MIN_CASES:
        problems.append(f"needs at least {MIN_CASES} cases, got {len(cases)}")
    seen: set[str] = set()
    for index, case in enumerate(cases, 1):
        where = f"case #{index}"
        if not isinstance(case, dict):
            problems.append(f"{where}: not an object")
            continue
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            problems.append(f"{where}: missing case_id")
        elif case_id in seen:
            problems.append(f"{where}: duplicate case_id {case_id}")
        else:
            seen.add(case_id)
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns:
            problems.append(f"{where}: needs at least one turn")
            continue
        for turn_index, turn in enumerate(turns, 1):
            text = (turn or {}).get("user") if isinstance(turn, dict) else None
            if not isinstance(text, str) or not text.strip():
                problems.append(f"{where} turn {turn_index}: empty `user`")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.module_c.freeze_holdout")
    parser.add_argument("cases", help="path to the incoming, never-run case file")
    parser.add_argument("--author", default="", help="who wrote the sentences")
    parser.add_argument("--note", default="", help="anything the report should say")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing freeze (never do this for a scored set)")
    args = parser.parse_args(argv)

    path = Path(args.cases)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists():
        print(f"missing: {path}")
        return 2

    sidecar = path.with_name(path.stem + "_freeze.json")
    if sidecar.exists() and not args.force:
        print(f"already frozen: {sidecar}\n"
              "A scored set is never re-frozen. Create a new file for new inputs.")
        return 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    problems = validate(payload)
    if problems:
        print("cannot freeze:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    record = {
        "cases_path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "cases_sha256": sha256(path),
        "case_count": len(payload["cases"]),
        "author": args.author or "unattributed",
        "note": args.note,
        "declaration": (
            "Written by someone who did not implement Module C. Frozen before the "
            "first run; scores from this file are reported as a non-author result "
            "and the file is never edited after scoring."),
    }
    sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(f"frozen {record['case_count']} cases -> {sidecar.name}")
    print(f"  sha256 {record['cases_sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
