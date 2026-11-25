"""Recompute every V2 statistic from the stored raw records.

This is the audit tool for the V2 report: it does not re-run the model and it
does not trust any number written in prose. It:

1. rebuilds the merged V2 record set from the two source reports exactly the way
   the V2 merge did (`overrides`, one cell at a time);
2. recomputes each arm's metrics from the merged per-case records with the same
   grading/aggregation code the runner used;
3. compares them with the numbers stored in `by_arm` and prints a table.

Exit code 1 means a stored number cannot be reproduced.

    python -m tests.module_c.recompute_v2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.module_c.eval_lib import metrics  # noqa: E402

DEFAULT_FINAL = ROOT / "outputs" / "module_c" / "v2-final" / "report.json"
DEFAULT_SOURCES = [ROOT / "outputs" / "module_c" / "v2-run1" / "report.json",
                   ROOT / "outputs" / "module_c" / "v2-fix1" / "report.json"]
TOLERANCE = 1e-9


def _key(row: dict[str, Any]) -> tuple[str, str, int]:
    return row["case_id"], row["arm"], int(row.get("rep") or 0)


def rebuild(sources: list[Path], overrides: list[str]) -> dict[tuple[str, str, int], dict]:
    """Start from source #1, then replace only the cells named in `overrides`."""
    merged: dict[tuple[str, str, int], dict] = {}
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in sources]
    for row in documents[0]["cases"]:
        merged[_key(row)] = row
    for label in overrides:
        cell = label.split(" <- ")[0]
        source_index = int(label.split("#")[-1]) if "#" in label else 1
        case_id, arm, rep = cell.split("/")
        donor = documents[source_index]
        found = [row for row in donor["cases"]
                 if row["case_id"] == case_id and row["arm"] == arm
                 and int(row.get("rep") or 0) == int(rep.lstrip("r"))]
        if len(found) != 1:
            raise SystemExit(f"override {label!r} matched {len(found)} rows in source {source_index}")
        merged[(case_id, arm, int(rep.lstrip("r")))] = found[0]
    return merged


def compare(final_path: Path, sources: list[Path], *, allow_missing: bool = False) -> list[str]:
    final = json.loads(final_path.read_text(encoding="utf-8"))
    problems: list[str] = []

    # --- 1. the merged record set is reproducible -------------------------- #
    try:
        rebuilt = rebuild(sources, final.get("overrides") or [])
    except (OSError, KeyError, IndexError, ValueError) as exc:
        problems.append(f"cannot rebuild record set: {exc}")
        rebuilt = {}
    stored = {_key(row): row for row in final["cases"]}
    if rebuilt:
        missing = set(stored) - set(rebuilt)
        extra = set(rebuilt) - set(stored)
        if missing or extra:
            problems.append(f"record set differs: missing={sorted(missing)[:3]} extra={sorted(extra)[:3]}")
        for key in sorted(set(stored) & set(rebuilt)):
            left, right = stored[key], rebuilt[key]
            if bool(left["passed"]) != bool(right["passed"]):
                problems.append(f"{key}: passed {left['passed']} != {right['passed']}")
            if (left.get("final_text") or "") != (right.get("final_text") or ""):
                problems.append(f"{key}: final_text differs")
            if stored and left.get("failures") != right.get("failures"):
                problems.append(f"{key}: failures differ")

    # --- 2. every stored metric is recomputable ---------------------------- #
    by_arm = final.get("by_arm") or {}
    for arm, stored_metrics in by_arm.items():
        rows = [row for row in final["cases"] if row["arm"] == arm]
        recomputed = metrics(rows)
        for name, value in stored_metrics.items():
            if name not in recomputed:
                if not allow_missing:
                    problems.append(f"{arm}.{name}: metric no longer produced by eval_lib")
                continue
            fresh = recomputed[name]
            if isinstance(value, (int, float)) and isinstance(fresh, (int, float)):
                if abs(float(value) - float(fresh)) > TOLERANCE:
                    problems.append(f"{arm}.{name}: stored {value} != recomputed {fresh}")
            elif value != fresh:
                problems.append(f"{arm}.{name}: stored {value!r} != recomputed {fresh!r}")
    return problems


def report(final_path: Path, sources: list[Path]) -> str:
    final = json.loads(final_path.read_text(encoding="utf-8"))
    lines = [f"# V2 recompute — {final.get('run_id')}", "",
             f"records: {len(final['cases'])} · arms: {', '.join(final.get('by_arm') or {})}", "",
             "| arm | pass | pass_rate | llm/case | tasks/case | tokens/case | p50 ms |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for arm, stored in (final.get("by_arm") or {}).items():
        rows = [row for row in final["cases"] if row["arm"] == arm]
        fresh = metrics(rows)
        lines.append(
            f"| {arm} | {fresh['case_pass_count']}/{fresh['case_count']} | "
            f"{fresh['case_pass_rate']:.3f} | {fresh['llm_calls_per_case']} | "
            f"{fresh['planning_tasks_per_case']} | {fresh['tokens_per_case']} | "
            f"{fresh['case_latency_p50_ms']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.module_c.recompute_v2")
    parser.add_argument("--final", default=str(DEFAULT_FINAL))
    parser.add_argument("--sources", nargs="*", default=[str(path) for path in DEFAULT_SOURCES])
    parser.add_argument("--allow-missing", action="store_true",
                        help="ignore metrics that were added after the report was written")
    args = parser.parse_args(argv)

    final_path = Path(args.final)
    sources = [Path(path) for path in args.sources]
    problems = compare(final_path, sources, allow_missing=args.allow_missing)
    print(report(final_path, sources))
    if problems:
        print("\nMISMATCHES:")
        for problem in problems[:40]:
            print(" -", problem)
        print(f"\n{len(problems)} mismatch(es)")
        return 1
    print("\nOK: every stored number is reproducible from the raw records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
