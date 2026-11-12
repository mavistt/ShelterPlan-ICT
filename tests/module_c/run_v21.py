"""V2.1 generalisation runner.

    python -m tests.module_c.run_v21 --arms harness,harness_opt --reps 2

Differences from ``run_v2`` (deliberate, and enforced):

* the case file is *frozen*: its sha256 must match ``v21_freeze.json`` or the
  runner refuses to start, so a "blind" run cannot be silently rescored after
  the author has seen the failures;
* every record carries ``b_submissions`` next to ``llm_calls``/``tool_calls``
  so a business-task submission is never conflated with a model call;
* repetitions are written to separate report files (``--rep-start``), never
  overwriting an earlier run.

The grading code and case file are the same objects the report cites.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient  # noqa: E402

from agent.b_client import BClient  # noqa: E402
from agent.config import HarnessConfig  # noqa: E402
from agent.llm_adapter import LLMAdapter, LLMConfig  # noqa: E402
from agent.service import HarnessService  # noqa: E402
from backend.app import app as b_app  # noqa: E402
from tests.module_c.eval_lib import (  # noqa: E402
    ARMS,
    load_cases,
    metrics,
    paired,
)
from tests.module_c.run_v2 import _capability_matrix, run_one  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "cases" / "v21_cases.json"
FREEZE_PATH = Path(__file__).resolve().parent / "cases" / "v21_freeze.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_freeze(cases_path: Path = CASES_PATH, freeze_path: Path = FREEZE_PATH) -> dict[str, Any]:
    """Fail closed when either the cases or the grading rules have changed."""
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    checks = [
        ("cases", cases_path, freeze["cases_sha256"]),
        ("grading", ROOT / freeze["grading_path"], freeze["grading_sha256"]),
    ]
    for label, path, expected in checks:
        actual = file_sha256(Path(path))
        if actual != expected:
            raise SystemExit(
                f"FROZEN {label.upper()} CHANGED\n  path     {path}\n"
                f"  expected {expected}\n  actual   {actual}\n"
                "A blind run cannot be rescored after the inputs or the grader were edited. "
                "Create a new case set (v21b) instead of editing this one."
            )
    return freeze


def run(*, cases_path: Path = CASES_PATH, output_dir: Path, arms: list[str],
        reps: int = 1, rep_start: int = 0, only: str | None = None,
        llm_config: LLMConfig | None = None, max_llm_calls: int = 10,
        frozen: bool = True) -> dict[str, Any]:
    freeze = verify_freeze(cases_path) if frozen else {"cases_sha256": file_sha256(cases_path)}
    needles = [part.strip() for part in (only or "").split(",") if part.strip()]
    cases = [case for case in load_cases(cases_path)
             if not needles or any(needle in case["case_id"] for needle in needles)]
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = output_dir / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)

    config = HarnessConfig()
    shared_client = TestClient(b_app)
    llm_config = llm_config or LLMConfig.from_env()
    llm = LLMAdapter(llm_config)
    service = HarnessService(config, client=BClient(config, client=shared_client),
                             trace_dir=str(trace_dir / "service"))
    records: list[dict[str, Any]] = []
    started = time.time()

    try:
        for rep in range(rep_start, rep_start + reps):
            for index, case in enumerate(cases):
                shift = (index + rep) % len(arms)
                order = arms[shift:] + arms[:shift]
                for arm in order:
                    record = run_one(case, arm, rep, service=service, llm=llm,
                                     config=config, shared_client=shared_client,
                                     trace_dir=trace_dir, max_llm_calls=max_llm_calls)
                    records.append(record)
                    print(f"[r{rep}] {case['case_id']:<10} {arm:<15} "
                          f"{'PASS' if record['passed'] else 'FAIL'} "
                          f"{record['llm_calls']} calls {record['duration_ms']:.0f}ms",
                          flush=True)
    finally:
        llm.close()

    by_arm = {arm: metrics([row for row in records if row["arm"] == arm]) for arm in arms}
    pairs = {f"{left}->{right}": paired(records, left, right)
             for left, right in (("harness", "harness_opt"),
                                 ("baseline", "harness_opt"))}
    report = {
        "run_id": f"v21-{int(started)}",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "code": _code_identity(),
        "freeze": freeze,
        "cases_path": str(cases_path),
        "cases_sha256_at_run": file_sha256(cases_path),
        "model": {"base_url": llm_config.base_url, "model": llm_config.model,
                  "temperature": llm_config.temperature},
        "design": {
            "arms": arms,
            "reps": reps,
            "rep_start": rep_start,
            "case_count": len(cases),
            "grading": "tests/module_c/eval_lib.py::grade",
            "arm_order": "rotated per case and repetition",
            "note": ("Expectations were written before the first run. The case file is "
                     "hash-locked; failures are reported as measured and are not rescored "
                     "after fixes."),
        },
        "cases": records,
        "by_arm": by_arm,
        "paired": pairs,
        "capability_matrix": _capability_matrix(records),
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    combined = output_dir / "trace.jsonl"
    with combined.open("w", encoding="utf-8") as sink:
        for path in sorted(trace_dir.glob("*.jsonl")):
            sink.write(path.read_text(encoding="utf-8"))
    report["trace_path"] = str(combined)
    _write_summary(report, output_dir / "summary.md")
    return report


def _code_identity() -> dict[str, Any]:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.strip()
        return {"commit": head, "working_tree_dirty": bool(dirty)}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "working_tree_dirty": None}


def _write_summary(report: dict[str, Any], path: Path) -> None:
    lines = [f"# V2.1 run {report['run_id']}", "",
             f"cases `{report['freeze'].get('cases_sha256', '')[:16]}` · "
             f"model `{report['model']['model']}` · {report['design']['case_count']} cases × "
             f"{len(report['design']['arms'])} arms × {report['design']['reps']} rep(s)", "",
             "| metric | " + " | ".join(report["by_arm"]) + " |",
             "|---|" + "---:|" * len(report["by_arm"])]
    keys = ["case_pass_count", "case_pass_rate", "task_routing_rate", "param_rate",
            "inheritance_rate", "numeric_consistency_rate", "demo_compliance_rate",
            "clarify_rate", "intent_accuracy", "slot_accuracy", "recovery_rate",
            "llm_calls_per_case", "tool_calls_per_case", "b_submissions_per_case",
            "planning_tasks_per_case", "tokens_per_case", "case_latency_p50_ms",
            "case_latency_p95_ms", "llm_latency_p50_ms", "b_api_success_p50_ms", "retries_total"]
    for key in keys:
        cells = []
        for arm in report["by_arm"]:
            value = report["by_arm"][arm].get(key)
            cells.append("n/a" if value is None else str(value))
        lines.append(f"| `{key}` | " + " | ".join(cells) + " |")
    lines += ["", "## per-group pass rate", "",
              "| group | " + " | ".join(report["by_arm"]) + " |",
              "|---|" + "---:|" * len(report["by_arm"])]
    for group, bucket in report["capability_matrix"].items():
        cells = [str((bucket.get(arm) or {}).get("rate")) for arm in report["by_arm"]]
        lines.append(f"| {group} | " + " | ".join(cells) + " |")
    lines += ["", "## paired", ""]
    for name, pair in report["paired"].items():
        lines.append(f"- **{name}**: {pair['right_wins']} wins / {pair['right_losses']} losses / "
                     f"{pair['ties']} ties over {pair['pairs']} pairs")
    lines += ["", "## failed cases", ""]
    for row in report["cases"]:
        if not row["passed"]:
            lines.append(f"- **{row['arm']}/{row['case_id']}** (r{row['rep']}): "
                         f"{'; '.join(row['failures'])[:260]}")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m tests.module_c.run_v21")
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--output", default=str(ROOT / "outputs" / "module_c" / "v21"))
    parser.add_argument("--arms", default="harness,harness_opt")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--rep-start", type=int, default=0)
    parser.add_argument("--only", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--max-llm-calls", type=int, default=12)
    parser.add_argument("--allow-unfrozen", action="store_true",
                        help="dev set only; never use for a reported generalisation run")
    args = parser.parse_args(argv)

    arms = list(ARMS) if args.arms == "all" else args.arms.split(",")
    llm_config = LLMConfig.from_env()
    if args.model:
        from dataclasses import replace
        llm_config = replace(llm_config, model=args.model)
    if args.llm_base_url:
        from dataclasses import replace
        llm_config = replace(llm_config, base_url=args.llm_base_url)

    report = run(cases_path=Path(args.cases), output_dir=Path(args.output), arms=arms,
                 reps=args.reps, rep_start=args.rep_start, only=args.only,
                 llm_config=llm_config, max_llm_calls=args.max_llm_calls,
                 frozen=not args.allow_unfrozen)
    print(json.dumps({"by_arm": report["by_arm"], "paired": {
        key: {k: v for k, v in value.items() if k != "cases"}
        for key, value in report["paired"].items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
