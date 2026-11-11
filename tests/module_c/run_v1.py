"""V1 experiment runner: real LLM agent, baseline vs harness.

    python -m tests.module_c.run_v1 --split all --output outputs/module_c/v1

Model defaults come from SHELTERPLAN_LLM_* environment variables (see
agent/llm_adapter.py). Module B runs in-process (ASGI) unless --base-url.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient  # noqa: E402

from agent.agent_loop import AgentRunner, FORBIDDEN_DEMO_PHRASES  # noqa: E402
from agent.agent_tools import (
    SHARED_SYSTEM_PROMPT,  # noqa: E402
    BASELINE_SYSTEM_PROMPT, HARNESS_SYSTEM_PROMPT, baseline_registry, harness_registry,
)
from agent.b_client import BClient  # noqa: E402
from agent.config import HarnessConfig  # noqa: E402
from agent.errors import HarnessError  # noqa: E402
from agent.numeric_audit import collect_numbers  # noqa: E402
from agent.harness.trace import TraceRecorder  # noqa: E402
from agent.llm_adapter import LLMAdapter, LLMConfig  # noqa: E402
from agent.numeric_audit import check_citations  # noqa: E402
from agent.service import HarnessService  # noqa: E402
from backend.app import app as b_app  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "cases" / "nl_cases.json"
MODES = ("baseline", "harness")


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 1)


def _norm_params(params: dict[str, Any] | None) -> dict[str, Any]:
    params = params or {}
    out = dict(params)
    if "disabled_shelter_ids" in out:
        out["disabled_shelter_ids"] = sorted(out.get("disabled_shelter_ids") or [])
    return out


def _params_match(expected: dict[str, Any], actual: dict[str, Any] | None) -> list[str]:
    actual = _norm_params(actual)
    failures = []
    for key, value in expected.items():
        got = actual.get(key)
        if isinstance(value, list):
            if sorted(map(str, got or [])) != sorted(map(str, value)):
                failures.append(f"{key}: expected {value} got {got}")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if got is None or abs(float(got) - float(value)) > 1e-6:
                failures.append(f"{key}: expected {value} got {got}")
        elif got != value:
            failures.append(f"{key}: expected {value} got {got}")
    return failures


def _ground_truth_numbers(baseline_state: dict[str, Any]) -> set[float]:
    """Authoritative numbers for the baseline arm: everything B actually returned
    for this case, plus the parameters its requests implied."""
    numbers: set[float] = set()
    for document in (baseline_state.get("results") or {}).values():
        # Ground truth = everything Module B actually computed for this case.
        numbers |= collect_numbers(document)
    for row in baseline_state.get("requests") or []:
        numbers |= collect_numbers(row.get("effective_params") or {})
    return numbers


def _ask_or_refuse(text: str | None) -> bool:
    if not text:
        return False
    if "？" in text or "?" in text:
        return True
    return any(token in text for token in
               ("请提供", "请确认", "请告诉我", "需要确认", "缺少", "无法", "不能", "无效", "拒绝"))


def _classify(case: dict[str, Any], run: AgentRun, failures: list[str]) -> list[str]:
    taxonomy: list[str] = []
    for failure in failures:
        if failure.startswith("task_types:"):
            taxonomy.append("wrong_tool_or_task")
        elif failure.startswith("params:"):
            taxonomy.append("param_error")
        elif failure.startswith("numeric"):
            taxonomy.append("numeric_violation")
        elif failure.startswith("demo"):
            taxonomy.append("demo_violation")
        elif failure.startswith("no_result"):
            taxonomy.append("no_result")
        elif failure.startswith("ask_or_refuse"):
            taxonomy.append("guessed_instead_of_ask")
        elif failure.startswith("ambiguous"):
            taxonomy.append("ambiguous_unresolved")
        else:
            taxonomy.append("other")
    if run.max_turns_hit:
        taxonomy.append("loop_limit")
    if run.error:
        taxonomy.append("runner_error")
    return sorted(set(taxonomy)) or ["none"]


def _evaluate(case: dict[str, Any], run: AgentRun, expect: dict[str, Any], *,
              baseline_state: dict[str, Any] | None = None) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    info: dict[str, Any] = {}

    successful_types: list[str] = []
    if run.mode == "harness":
        successful_types = [row["task_type"] for row in run.successful_tasks if row.get("task_type")]
    else:
        for row in (baseline_state or {}).get("requests") or []:
            if row.get("completed") and row["payload"].get("task_type"):
                successful_types.append(row["payload"]["task_type"])
    info["successful_task_types"] = sorted(set(successful_types))

    if "task_types" in expect:
        missing = [t for t in expect["task_types"] if t not in successful_types]
        if missing:
            failures.append(f"task_types: missing {missing}; got {info['successful_task_types']}")

    if "no_result" in expect:
        blocked = expect["no_result"]
        if blocked in successful_types:
            failures.append(f"no_result: produced a successful {blocked} result")
        if not _ask_or_refuse(run.final_text):
            failures.append("ask_or_refuse: final answer neither asks nor explains the refusal")

    if "params" in expect:
        if run.mode == "harness":
            final_params = None
            for row in reversed(run.submitted):
                if row.get("params"):
                    final_params = row["params"]
                    break
        else:
            completed = [row for row in (baseline_state or {}).get("requests") or [] if row.get("completed")]
            final_params = (completed[-1] if completed else
                            ((baseline_state or {}).get("requests") or [{}])[-1]).get("effective_params")
        info["final_params"] = _norm_params(final_params)
        failures.extend(_params_match(expect["params"], final_params))

    if "numeric_consistent" in expect:
        if run.mode == "harness":
            consistent = bool((run.citations or {}).get("consistent", True))
        else:
            audit = check_citations(run.final_text or "", _ground_truth_numbers(baseline_state or {}))
            consistent = bool(audit["consistent"])
            info["baseline_citations"] = audit
        info["numeric_consistent"] = consistent
        if expect["numeric_consistent"] and not consistent:
            failures.append("numeric: answer cites numbers not present in tool output")

    if "demo_disclosed" in expect and expect["demo_disclosed"]:
        text = run.final_text or ""
        disclosed = any(token in text for token in ("DEMO_ONLY", "未经", "未核验", "未经过"))
        violation = any(phrase in text for phrase in FORBIDDEN_DEMO_PHRASES)
        info["demo_disclosed"] = disclosed and not violation
        if successful_types and not info["demo_disclosed"]:
            failures.append("demo: missing DEMO_ONLY disclosure or used forbidden wording")

    if "ambiguous_ok" in expect:
        ok = False
        if run.mode == "harness":
            disabled = None
            for row in reversed(run.submitted):
                params = row.get("params") or {}
                if params.get("disabled_shelter_ids"):
                    disabled = params["disabled_shelter_ids"]
                    break
            if disabled:
                ok = all(shelter in (run.final_text or "") for shelter in disabled)
        else:
            completed = [row for row in (baseline_state or {}).get("requests") or [] if row.get("completed")]
            if completed:
                disabled = (completed[-1].get("effective_params") or {}).get("disabled_shelter_ids") or []
                ok = all(shelter in (run.final_text or "") for shelter in disabled)
        ok = ok or _ask_or_refuse(run.final_text)
        info["ambiguous_ok"] = ok
        if not ok:
            failures.append("ambiguous: neither named the assumed shelter nor asked the user")

    return failures, info


def run(*, cases_path: Path, output_dir: Path, modes: list[str], split: str = "all",
        base_url: str | None = None, llm_config: LLMConfig | None = None,
        max_llm_calls: int = 10, only: str | None = None) -> dict[str, Any]:
    spec = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = [row for row in spec["cases"]
             if (split == "all" or row["group"] == split)
             and (not only or only in row["case_id"])]
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = output_dir / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)
    config = HarnessConfig()

    shared_client = TestClient(b_app)
    llm = LLMAdapter(llm_config or LLMConfig.from_env())

    records: list[dict[str, Any]] = []
    started = time.time()
    try:
        for case in cases:
            for mode in modes:
                case_started = time.perf_counter()
                session_id = f"v1-{mode}-{case['case_id'].lower()}"
                trace = TraceRecorder(trace_dir / f"{mode}-{case['case_id']}.jsonl", session_id)
                harness_service: HarnessService | None = None
                baseline_state: dict[str, Any] = {}

                if mode == "harness":
                    b_client = BClient(config, client=shared_client)
                    harness_service = HarnessService(config, client=b_client)
                    registry, harness_service = harness_registry(harness_service, session_id)
                    system_prompt = SHARED_SYSTEM_PROMPT
                else:
                    b_client = BClient(replace(config, max_transport_retries=0),
                                       client=shared_client)
                    registry, baseline_state = baseline_registry(b_client, config)
                    system_prompt = SHARED_SYSTEM_PROMPT

                runner = AgentRunner(mode, registry, system_prompt, llm, config=config,
                                     trace=trace, service=harness_service,
                                     session_id=session_id, max_llm_calls=max_llm_calls)
                run = runner.run_case(case["case_id"], case["turns"])
                failures, info = _evaluate(case, run, case.get("expect") or {},
                                           baseline_state=baseline_state)
                duration = (time.perf_counter() - case_started) * 1000
                expect = case.get("expect") or {}
                records.append({
                    "case_id": case["case_id"],
                    "group": case["group"],
                    "capability": case.get("capability"),
                    "mode": mode,
                    "expect_keys": sorted(expect.keys()),
                    "passed": not failures,
                    "failures": failures,
                    "failure_types": _classify(case, run, failures),
                    "final_text": run.final_text,
                    "info": info,
                    "llm_calls": len(run.llm_calls),
                    "llm_latency_ms": round(run.llm_latency_ms, 1),
                    "prompt_tokens": run.prompt_tokens,
                    "completion_tokens": run.completion_tokens,
                    "tool_calls": len(run.tool_calls),
                    "tool_failures": sum(1 for call in run.tool_calls if not call.ok),
                    "submitted": run.submitted,
                    "citations": run.citations,
                    "rewrite_used": run.rewrite_used,
                    "max_turns_hit": run.max_turns_hit,
                    "runner_error": run.error,
                    "duration_ms": round(duration, 1),
                    "session": harness_service.session_state(session_id) if harness_service else None,
                })
    finally:
        llm.close()

    by_mode: dict[str, Any] = {}
    for mode in modes:
        rows = [row for row in records if row["mode"] == mode]
        by_mode[mode] = _metrics(rows)

    report = {
        "run_id": f"v1-{int(started)}",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "model": {"base_url": llm_config.base_url if llm_config else LLMConfig.from_env().base_url,
                  "model": llm_config.model if llm_config else LLMConfig.from_env().model,
                  "temperature": (llm_config or LLMConfig.from_env()).temperature},
        "experiment": {
            "deterministic_v0_included": False,
            "note": ("V0 deterministic results are reported separately; the numbers below "
                     "describe a real LLM agent under two tool-arm conditions."),
        },
        "cases": records,
        "by_mode": by_mode,
        "comparison": _compare(by_mode),
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


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if not total:
        return {"case_count": 0}

    def expects(key: str) -> list[dict[str, Any]]:
        return [row for row in rows if key in row.get("expect_keys", [])]

    result_cases = expects("task_types")
    param_cases = expects("params")
    numeric_cases = expects("numeric_consistent")
    guard_cases = [row for row in rows if "no_result" in row.get("expect_keys", [])]
    ambiguous_cases = [row for row in rows if "ambiguous_ok" in row.get("expect_keys", [])]

    def pass_rate(group: list[dict[str, Any]]) -> float | None:
        return round(sum(row["passed"] for row in group) / len(group), 4) if group else None

    llm_latencies = [row["llm_latency_ms"] for row in rows]
    wall_latencies = [row["duration_ms"] for row in rows]
    taxonomy: dict[str, int] = {}
    for row in rows:
        for kind in row["failure_types"]:
            taxonomy[kind] = taxonomy.get(kind, 0) + 1
    return {
        "case_count": total,
        "case_pass_count": sum(row["passed"] for row in rows),
        "case_pass_rate": pass_rate(rows),
        "task_success_rate": pass_rate(result_cases),
        "task_case_count": len(result_cases),
        "param_correctness_rate": pass_rate(param_cases),
        "param_case_count": len(param_cases),
        "numeric_consistency_rate": pass_rate(numeric_cases),
        "numeric_case_count": len(numeric_cases),
        "guard_refusal_rate": pass_rate(guard_cases),
        "guard_case_count": len(guard_cases),
        "ambiguous_resolution_rate": pass_rate(ambiguous_cases),
        "demo_compliance_rate": pass_rate([row for row in rows
                                           if "demo_disclosed" in row.get("expect_keys", [])]),
        "failure_taxonomy": taxonomy,
        "llm_calls_per_case": round(sum(row["llm_calls"] for row in rows) / total, 3),
        "tool_calls_per_case": round(sum(row["tool_calls"] for row in rows) / total, 3),
        "tool_failures_total": sum(row["tool_failures"] for row in rows),
        "prompt_tokens_total": sum(row["prompt_tokens"] for row in rows),
        "completion_tokens_total": sum(row["completion_tokens"] for row in rows),
        "tokens_per_case": round(sum(row["prompt_tokens"] + row["completion_tokens"]
                                     for row in rows) / total, 1),
        "llm_latency_p50_ms": _percentile(llm_latencies, 0.5),
        "llm_latency_p95_ms": _percentile(llm_latencies, 0.95),
        "case_wall_p50_ms": _percentile(wall_latencies, 0.5),
        "case_wall_p95_ms": _percentile(wall_latencies, 0.95),
        "rewrite_used_count": sum(row["rewrite_used"] for row in rows),
        "loop_limit_count": sum(row["max_turns_hit"] for row in rows),
    }


def _compare(by_mode: dict[str, Any]) -> dict[str, Any]:
    baseline = by_mode.get("baseline", {})
    harness = by_mode.get("harness", {})
    keys = ["case_pass_rate", "task_success_rate", "param_correctness_rate",
            "numeric_consistency_rate", "guard_refusal_rate", "demo_compliance_rate",
            "llm_calls_per_case", "tool_calls_per_case", "tokens_per_case",
            "llm_latency_p50_ms", "llm_latency_p95_ms", "case_wall_p50_ms", "case_wall_p95_ms"]
    table = {}
    for key in keys:
        table[key] = {"baseline": baseline.get(key), "harness": harness.get(key)}
    deltas = {}
    for key in ("case_pass_rate", "task_success_rate", "param_correctness_rate",
                "numeric_consistency_rate", "guard_refusal_rate", "demo_compliance_rate"):
        b, h = baseline.get(key), harness.get(key)
        if isinstance(b, (int, float)) and isinstance(h, (int, float)):
            deltas[key] = round(h - b, 4)
    return {"table": table, "harness_minus_baseline": deltas}


def _write_summary(report: dict[str, Any], path: Path) -> None:
    lines = [f"# V1 run {report['run_id']}", ""]
    for mode, metrics in report["by_mode"].items():
        lines += [f"## {mode}", "", "| metric | value |", "|---|---:|"]
        for key, value in metrics.items():
            lines.append(f"| `{key}` | {value} |")
        lines.append("")
    lines += ["## failed cases", ""]
    for row in report["cases"]:
        if not row["passed"]:
            lines.append(f"- **{row['mode']}/{row['case_id']}** ({row['capability']}): "
                         f"{'; '.join(row['failures'])[:200]}")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m tests.module_c.run_v1")
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--output", default=str(ROOT / "outputs" / "module_c" / "v1"))
    parser.add_argument("--modes", default="baseline,harness")
    parser.add_argument("--split", default="all", choices=["all", "dev", "holdout"])
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--max-llm-calls", type=int, default=10)
    parser.add_argument("--only", default=None)
    args = parser.parse_args(argv)

    llm_config = LLMConfig.from_env()
    if args.model:
        llm_config = replace(llm_config, model=args.model)
    if args.llm_base_url:
        llm_config = replace(llm_config, base_url=args.llm_base_url)

    report = run(cases_path=Path(args.cases), output_dir=Path(args.output),
                 modes=args.modes.split(","), split=args.split, base_url=args.base_url,
                 llm_config=llm_config, max_llm_calls=args.max_llm_calls, only=args.only)
    print(json.dumps({"by_mode": report["by_mode"], "comparison": report["comparison"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
