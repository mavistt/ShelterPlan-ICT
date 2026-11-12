"""Classify each post-fix-regression case by the *evidence* in its trace.

    python -m tests.module_c.classify_v22_trace \
        --report outputs/module_c/v22-postfix-final/report.json \
        --trace outputs/module_c/v22-postfix-final/trace \
        --output outputs/module_c/v22-postfix-final/trace_taxonomy.md

V2.2 asks for per-case traces that distinguish four failure sources. This tool
does not re-grade and does not change any score: it reads the frozen report plus
the already-written trace and labels every case with the evidence it used, so a
reader can audit the label instead of trusting a prose summary.

Buckets
-------
* passed              - no failure recorded
* model_extraction    - the extractor fell back to rules / retried / repaired JSON,
                        or the intent/slot expectation was missed by the model path
* rule_misread        - a deterministic rule changed or dropped an operation
                        (params/ops mismatch with a normal LLM extraction)
* business_rejection  - a guard or Module B refusal appears in the answer/trace
* measurement         - only grader/recorder-level differences remain
* other               - no bucket matched; listed verbatim for manual review

The classification is deliberately conservative: an unmatched case stays in
`other` rather than being forced into a convenient bucket.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

BUCKETS = ("passed", "model_extraction", "rule_misread",
           "business_rejection", "measurement", "other")

BUSINESS_CODES = ("GUARD_", "SCENARIO_INVALID", "CANDIDATE_INVALID", "DATA_INVALID",
                  "JOB_FAILED", "JOB_TIMEOUT", "JOB_STATE_LOST", "RESULT_MISMATCH",
                  "CONTRACT_VIOLATION", "B_", "SOLVER_TIMEOUT")
MODEL_MODES = {"rules_fallback", "llm_retry", "llm_repaired"}
MEASUREMENT_MARKERS = ("clarify_fields:", "text:", "demo:", "numeric:", "no_tasks:",
                       "max_planning_tasks", "must_not_contain", "must_contain_any")


def trace_events(trace_dir: Path, rep: int, arm: str, case_id: str) -> list[dict[str, Any]]:
    path = trace_dir / f"r{rep}-{arm}-{case_id}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def classify(row: dict[str, Any], events: list[dict[str, Any]]) -> tuple[str, str]:
    failures = list(row.get("failures") or [])
    if row.get("passed"):
        return "passed", "no failure recorded by the frozen grader"

    text = " ".join(str(row.get(k) or "") for k in ("final_text", "runner_error"))
    joined = " ".join(failures)
    modes = {str(event.get("extract_mode")) for event in events
             if event.get("event") == "intent"}
    error_codes = [str(event.get("error_code")) for event in events
                   if event.get("event") in {"tool_call", "plan", "turn", "task"}
                   and event.get("error_code")]

    if any(code in text for code in BUSINESS_CODES) or any(
            code not in {"None", ""} for code in error_codes):
        return "business_rejection", f"guard/B error in answer or trace ({error_codes[:2]})"
    if row.get("runner_error") or row.get("max_turns_hit"):
        return "business_rejection", "runner error or loop budget exhausted"
    if modes & MODEL_MODES:
        return "model_extraction", f"extract_mode={sorted(modes & MODEL_MODES)}"
    if any(marker in joined for marker in ("intent: expected", "ops: missing", "loop_limit:")):
        return "model_extraction", "intent/ops expectation missed on the model path"
    if any(marker in joined for marker in ("params:", "task_types:", "ops:")):
        return "rule_misread", "deterministic layer changed/dropped an operation or task"
    if any(marker in joined for marker in MEASUREMENT_MARKERS):
        return "measurement", "only grader/recorder-level differences remain"
    return "other", "no bucket matched; review the failures verbatim"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.module_c.classify_v22_trace")
    parser.add_argument("--report", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    trace_dir = Path(args.trace)
    rows: list[dict[str, Any]] = []
    counts: collections.Counter[str] = collections.Counter()
    by_arm: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)

    for row in report.get("cases") or []:
        events = trace_events(trace_dir, row.get("rep", 0), row["arm"], row["case_id"])
        bucket, reason = classify(row, events)
        counts[bucket] += 1
        by_arm[row["arm"]][bucket] += 1
        rows.append({"case_id": row["case_id"], "arm": row["arm"], "rep": row.get("rep"),
                     "passed": bool(row.get("passed")), "bucket": bucket, "reason": reason,
                     "failures": list(row.get("failures") or []),
                     "trace_events": len(events)})

    lines = ["# V2.2 post-fix regression — per-case trace taxonomy", "",
             f"source report: `{args.report}` (label="
             f"`{(report.get('design') or {}).get('label', 'n/a')}`)", "",
             "Classification is evidence-based and does not change any score. "
             "Every case keeps its own trace file under the trace directory.", "",
             "| bucket | cases |", "|---|---:|"]
    for bucket in BUCKETS:
        lines.append(f"| {bucket} | {counts[bucket]} |")
    lines += ["", "## by arm", "", "| arm | " + " | ".join(BUCKETS) + " |",
              "|---|" + "---:|" * len(BUCKETS)]
    for arm, counter in sorted(by_arm.items()):
        lines.append(f"| {arm} | " + " | ".join(str(counter[b]) for b in BUCKETS) + " |")
    lines += ["", "## per-case detail", "",
              "| case | arm | rep | bucket | evidence | failures |", "|---|---|---|---|---|---|"]
    for row in rows:
        failures = "; ".join(row["failures"])[:220].replace("|", "\\|") or "-"
        lines.append(f"| {row['case_id']} | {row['arm']} | {row['rep']} | {row['bucket']} | "
                     f"{row['reason']} | {failures} |")
    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    sidecar = Path(str(args.output) + ".json")
    sidecar.write_text(json.dumps({"counts": dict(counts),
                                   "by_arm": {k: dict(v) for k, v in by_arm.items()},
                                   "cases": rows}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(json.dumps(dict(counts), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
