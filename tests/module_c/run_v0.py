"""Deterministic V0 evaluation runner.

Runs the P0 task set through the harness, evaluates every expectation and writes
a machine-readable report plus the raw JSONL trace. No model is involved: these
numbers describe the harness, not an LLM agent.

    python -m tests.module_c.run_v0 --output outputs/module_c/v0
"""
from __future__ import annotations

import argparse
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

from agent.b_client import BClient  # noqa: E402
from agent.config import HarnessConfig  # noqa: E402
from agent.contracts import ScenarioParams  # noqa: E402
from agent.harness.executor import ExecuteRequest, HarnessExecutor  # noqa: E402
from agent.harness.session import Session  # noqa: E402
from agent.harness.trace import TraceRecorder  # noqa: E402
from backend.app import app as b_app  # noqa: E402
from tests.module_c.conftest import FlakyClient, JobLostClient  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "cases" / "p0_cases.json"


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 3)


def _resolve_options(step: dict[str, Any], spec: dict[str, Any]) -> list[dict] | None:
    ref = step.get("options_ref")
    return list(spec["option_sets"][ref]) if ref else None


def _resolve_changes(step: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    changes = step.get("changes")
    if not changes:
        return None
    resolved = {}
    for key, value in changes.items():
        resolved[key] = list(spec["scarce_shelters"]) if value == "@scarce" else value
    return resolved


def _make_session(case: dict[str, Any], config: HarnessConfig) -> Session:
    overrides = case.get("session") or {}
    params = ScenarioParams(
        population_demand_ratio=overrides.get("population_demand_ratio", config.default_population_demand_ratio),
        max_walk_time_min=overrides.get("max_walk_time_min", config.default_max_walk_time_min),
        budget_units=overrides.get("budget_units"),
        disabled_shelter_ids=tuple(overrides.get("disabled_shelter_ids", ())),
        data_version=overrides.get("data_version", config.data_version),
        analysis_mode=overrides.get("analysis_mode", "demo"),
    )
    return Session(f"v0-{case['case_id'].lower()}", params)


def _client_for(case: dict[str, Any], config: HarnessConfig, base_url: str | None):
    injection = case.get("inject")
    if base_url:
        client = BClient(replace(config, b_base_url=base_url),
                         client=None)
        inner = client
    else:
        inner = TestClient(b_app)
        client = BClient(config, client=inner)
    if injection == "job_lost":
        return BClient(config, client=JobLostClient(inner, fail_after=1))
    if injection == "transport_flake":
        return BClient(replace(config, max_transport_retries=2),
                       client=FlakyClient(inner, failures=1))
    return client


def _evaluate(case: dict[str, Any], expect: dict[str, Any], *, outcomes: list, session: Session,
              snapshots: list[dict], extra: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    last = outcomes[-1] if outcomes else None
    summary = (last.summary if last and last.ok else None) or {}

    if "ok" in expect:
        actual = bool(last.ok) if last else bool(extra.get("duplicate_ok"))
        if actual != expect["ok"]:
            failures.append(f"ok: expected {expect['ok']} got {actual} "
                            f"({(last.error or {}).get('code') if last else 'no outcome'})")
    if "error_code" in expect:
        actual = (last.error or {}).get("code") if last else None
        if actual != expect["error_code"]:
            failures.append(f"error_code: expected {expect['error_code']} got {actual}")
    for field in ("verification_status", "data_status", "solver_status"):
        if field in expect:
            actual = getattr(last, field, None) if last else None
            if actual != expect[field]:
                failures.append(f"{field}: expected {expect[field]} got {actual}")
    if "optimality_proven" in expect:
        actual = getattr(last, "optimality_proven", None) if last else None
        if bool(actual) != expect["optimality_proven"]:
            failures.append(f"optimality_proven: expected {expect['optimality_proven']} got {actual}")
    if "tool_call_count" in expect:
        actual = len(last.tool_calls) if last else 0
        if actual != expect["tool_call_count"]:
            failures.append(f"tool_call_count: expected {expect['tool_call_count']} got {actual}")
    if "summary_max_bytes" in expect:
        actual = getattr(last, "summary_bytes", 0) if last else 0
        if actual > expect["summary_max_bytes"]:
            failures.append(f"summary_bytes: {actual} > {expect['summary_max_bytes']}")

    detail = summary.get("detail") or {}
    if "selected_option_ids" in expect:
        actual = (detail.get("optimization") or {}).get("selected_option_ids")
        if actual != expect["selected_option_ids"]:
            failures.append(f"selected_option_ids: expected {expect['selected_option_ids']} got {actual}")
    if "total_cost_units" in expect:
        actual = (detail.get("optimization") or {}).get("total_cost_units")
        if actual != expect["total_cost_units"]:
            failures.append(f"total_cost_units: expected {expect['total_cost_units']} got {actual}")
    if "served_gt" in expect:
        # For budget tasks the root summary is the *pre-optimization* baseline;
        # the optimized total lives in detail.optimization.served_demand.
        optimization = detail.get("optimization") or {}
        actual = optimization.get("served_demand")
        if actual is None:
            actual = (summary.get("metrics") or {}).get("served_demand")
        if actual is None or actual <= expect["served_gt"]:
            failures.append(f"served_demand: {actual} <= {expect['served_gt']}")
    if "served_demand" in expect and expect["served_demand"] is not None:
        actual = (summary.get("metrics") or {}).get("served_demand")
        if actual is None or abs(actual - expect["served_demand"]) > 1e-6:
            failures.append(f"served_demand: expected {expect['served_demand']} got {actual}")
    if "scenario_count" in expect:
        actual = (detail.get("comparison") or {}).get("scenario_count")
        if actual != expect["scenario_count"]:
            failures.append(f"scenario_count: expected {expect['scenario_count']} got {actual}")
    if "unique_recommendation" in expect:
        actual = (detail.get("comparison") or {}).get("unique_recommendation")
        if actual != expect["unique_recommendation"]:
            failures.append(f"unique_recommendation: expected {expect['unique_recommendation']} got {actual}")
    if "point_count" in expect:
        actual = (detail.get("sensitivity") or {}).get("point_count")
        if actual != expect["point_count"]:
            failures.append(f"point_count: expected {expect['point_count']} got {actual}")
    if "fixed_served_lt" in expect:
        actual = ((detail.get("stress") or {}).get("fixed_plan") or {}).get("served_demand")
        if actual is None or actual >= expect["fixed_served_lt"]:
            failures.append(f"fixed served: {actual} >= {expect['fixed_served_lt']}")
    if "adaptive_present" in expect:
        actual = (detail.get("stress") or {}).get("adaptive_replan") is not None
        if actual != expect["adaptive_present"]:
            failures.append(f"adaptive_replan present: expected {expect['adaptive_present']} got {actual}")

    if "preserved" in expect:
        params = session.current.params.business_fields()
        for key, value in expect["preserved"].items():
            actual = params.get(key)
            if isinstance(value, list):
                actual = list(actual or [])
            if actual != value:
                failures.append(f"preserved[{key}]: expected {value} got {actual}")
    if expect.get("history_immutable"):
        for index, snapshot in enumerate(snapshots):
            if index >= len(session.history):
                failures.append(f"history[{index}] missing")
                continue
            if session.history[index].params.to_dict() != snapshot:
                failures.append(f"history[{index}] was mutated after commit")
    if "summary_consistent" in expect:
        if not extra.get("summary_consistent"):
            failures.append("summary_consistent: summary metrics != full result")
    if expect.get("same_job") and not extra.get("same_job"):
        failures.append("same_job: duplicate submissions produced different job ids")
    if "min_attempts" in expect:
        attempts = max((call.attempt for call in (last.tool_calls if last else [])), default=1)
        if attempts < expect["min_attempts"]:
            failures.append(f"attempts: {attempts} < {expect['min_attempts']}")
    if "max_duration_ms" in expect:
        actual = getattr(last, "duration_ms", 0) if last else 0
        if actual > expect["max_duration_ms"]:
            failures.append(f"duration_ms: {actual:.0f} > {expect['max_duration_ms']}")
    return failures


def _duplicate_case(case: dict[str, Any], config: HarnessConfig, session: Session,
                    trace: TraceRecorder, base_url: str | None) -> tuple[list, dict]:
    inner = TestClient(b_app) if not base_url else None
    client = BClient(replace(config, b_base_url=base_url) if base_url else config, client=inner)
    payload = {"task_type": "assess", "analysis_mode": "demo", "task_id": "v0-duplicate",
               "idempotency_key": f"{session.session_id}:duplicate"}
    first = client.submit(payload)
    second = client.submit(payload)
    job = client.wait_for_job(first["job_id"])
    result = client.get_result(job["result_id"])
    trace.log("tool_call", task_id="v0-duplicate", tool_name="submit_task",
              status="ok", job_id=first["job_id"], attempt=1, attempts=1, retries=0,
              args_digest="", args_summary={"duplicate": True})
    trace.log("task", task_id="v0-duplicate", ok=True, result_id=result["result_id"])
    return [], {"same_job": first["job_id"] == second["job_id"], "result_id": result["result_id"]}


def run(cases_path: Path, output_dir: Path, *, base_url: str | None = None,
        only: str | None = None) -> dict[str, Any]:
    spec = json.loads(cases_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = output_dir / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)
    config = HarnessConfig()

    records: list[dict[str, Any]] = []
    all_latencies: list[float] = []
    tool_calls_total = tool_ok = retries_total = 0
    fault_tool_failures = unexpected_tool_failures = 0
    summary_sizes: list[int] = []
    inheritance_checks = inheritance_passed = 0
    error_cases = error_correct = 0
    consistency_checks = consistency_passed = 0

    started_at = time.time()
    for case in spec["cases"]:
        if only and only not in case["case_id"]:
            continue
        case_started = time.perf_counter()
        case_config = config
        if case.get("inject") == "timeout":
            case_config = replace(config, job_timeout_s=0.3, max_job_retries=0)
        session = _make_session(case, case_config)
        trace = TraceRecorder(trace_dir / f"{case['case_id']}.jsonl", session.session_id)
        client = _client_for(case, case_config, base_url)
        executor = HarnessExecutor(client, session, trace, case_config)

        outcomes: list = []
        snapshots: list[dict] = []
        history_snapshot: list[dict] | None = None
        step_records: list[dict[str, Any]] = []
        extra: dict[str, Any] = {}
        failures: list[str] = []

        try:
            if case.get("inject") == "duplicate":
                _, extra = _duplicate_case(case, case_config, session, trace, base_url)
                extra["duplicate_ok"] = bool(extra.get("same_job"))
                outcomes = []
                step_records.append({"task_type": "duplicate", "ok": extra.get("same_job")})
            else:
                previous_result_id = None
                for step in case["steps"]:
                    changes = _resolve_changes(step, spec)
                    parameters = dict(step.get("parameters") or {})
                    request = ExecuteRequest(
                        task_type=step["task_type"], changes=changes, parameters=parameters,
                        improvement_options=_resolve_options(step, spec),
                        result_id=(previous_result_id if step.get("result_from") == "last"
                                   else step.get("result_id")),
                        claim=step.get("claim"),
                    )
                    outcome = executor.execute(request)
                    outcomes.append(outcome)
                    all_latencies.append(outcome.duration_ms)
                    fault_expected = bool(case.get("inject")) or case.get("kind") == "expected_error"
                    for call in outcome.tool_calls:
                        tool_calls_total += 1
                        tool_ok += int(call.status == "ok")
                        if call.status != "ok":
                            if fault_expected:
                                fault_tool_failures += 1
                            else:
                                unexpected_tool_failures += 1
                    retries_total += outcome.retries
                    if outcome.summary_bytes:
                        summary_sizes.append(outcome.summary_bytes)
                    if outcome.ok:
                        previous_result_id = outcome.result_id
                        if history_snapshot is None:
                            history_snapshot = [row.params.to_dict() for row in session.history]
                    step_records.append({
                        "task_type": step["task_type"],
                        "ok": outcome.ok,
                        "error_code": (outcome.error or {}).get("code"),
                        "result_id": outcome.result_id,
                        "scenario_id": outcome.scenario.scenario_id if outcome.scenario else None,
                        "solver_status": outcome.solver_status,
                        "summary_bytes": outcome.summary_bytes,
                        "duration_ms": round(outcome.duration_ms, 1),
                        "tool_calls": len(outcome.tool_calls),
                    })
                    if outcome.ok and step.get("expect_full_consistency"):
                        full = client.get_result(outcome.result_id)
                        extra["summary_consistent"] = all(
                            outcome.summary["metrics"][key] == full["result_summary"][key]
                            for key in ("served_demand", "total_demand", "weighted_walk_time_min"))
                        consistency_checks += 1
                        consistency_passed += int(extra["summary_consistent"])

            expect = dict(case.get("expect") or {})
            # P0-22 always verifies summary/result equality even without a flag.
            if expect.get("summary_consistent"):
                if outcomes and outcomes[-1].ok and "summary_consistent" not in extra:
                    full = client.get_result(outcomes[-1].result_id)
                    extra["summary_consistent"] = all(
                        outcomes[-1].summary["metrics"][key] == full["result_summary"][key]
                        for key in ("served_demand", "total_demand", "weighted_walk_time_min"))
                consistency_checks += 1
                consistency_passed += int(bool(extra.get("summary_consistent")))
            if "preserved" in expect:
                inheritance_checks += 1
            failures = _evaluate(case, expect, outcomes=outcomes, session=session,
                                 snapshots=history_snapshot or [], extra=extra)
            if "preserved" in expect:
                inheritance_passed += int(not failures)
            if case.get("kind") == "expected_error":
                error_cases += 1
                error_correct += int(not failures)
        except Exception as exc:  # noqa: BLE001
            failures = [f"runner exception: {exc.__class__.__name__}: {exc}"]
            step_records.append({"runner_error": str(exc)})
        finally:
            duration = (time.perf_counter() - case_started) * 1000
            records.append({
                "case_id": case["case_id"],
                "title": case["title"],
                "group": case["group"],
                "kind": case["kind"],
                "passed": not failures,
                "failures": failures,
                "steps": step_records,
                "duration_ms": round(duration, 1),
                "session": session.snapshot(),
            })

    total = len(records)
    passed = sum(row["passed"] for row in records)
    report = {
        "run_id": f"v0-{int(started_at)}",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "harness": {"model_in_loop": False, "version": "V0", "module_b_client": "FastAPI TestClient"
                    if not base_url else base_url},
        "config": {
            "summary_max_bytes": config.summary_max_bytes,
            "job_timeout_s": config.job_timeout_s,
            "max_transport_retries": config.max_transport_retries,
            "max_job_retries": config.max_job_retries,
        },
        "metrics": {
            "case_count": total,
            "case_pass_count": passed,
            "case_pass_rate": round(passed / total, 4) if total else None,
            "tool_call_count": tool_calls_total,
            "tool_success_rate": round(tool_ok / tool_calls_total, 4) if tool_calls_total else None,
            "tool_failures_by_design": fault_tool_failures,
            "unexpected_tool_failures": unexpected_tool_failures,
            "parameter_inheritance_rate": (round(inheritance_passed / inheritance_checks, 4)
                                           if inheritance_checks else None),
            "error_detection_rate": (round(error_correct / error_cases, 4)
                                     if error_cases else None),
            "summary_consistency_rate": (round(consistency_passed / consistency_checks, 4)
                                         if consistency_checks else None),
            "latency_p50_ms": _percentile(all_latencies, 0.5),
            "latency_p95_ms": _percentile(all_latencies, 0.95),
            "latency_mean_ms": round(statistics.fmean(all_latencies), 3) if all_latencies else None,
            "tool_calls_per_task": round(tool_calls_total / total, 3) if total else None,
            "retry_total": retries_total,
            "summary_max_bytes": max(summary_sizes) if summary_sizes else None,
            "summary_mean_bytes": round(statistics.fmean(summary_sizes), 1) if summary_sizes else None,
            "parameters_inheritance_checks": inheritance_checks,
            "error_cases": error_cases,
            "consistency_checks": consistency_checks,
        },
        "cases": records,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    combined = output_dir / "trace.jsonl"
    with combined.open("w", encoding="utf-8") as sink:
        for path in sorted(trace_dir.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    sink.write(line + "\n")
    report["trace_path"] = str(combined)
    _write_summary(report, output_dir / "summary.md")
    return report


def _write_summary(report: dict[str, Any], path: Path) -> None:
    metrics = report["metrics"]
    lines = [
        f"# Module C Harness V0 — run {report['run_id']}",
        "",
        f"Generated: {report['generated_at']} · model in loop: **no**",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in metrics.items():
        lines.append(f"| `{key}` | {value} |")
    lines += ["", "## Cases", "", "| Case | Group | Result | Failures |", "|---|---|---|---|"]
    for row in report["cases"]:
        status = "PASS" if row["passed"] else "FAIL"
        failures = "; ".join(row["failures"])[:160]
        lines.append(f"| {row['case_id']} | {row['group']} | {status} | {failures} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.module_c.run_v0")
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--output", default=str(ROOT / "outputs" / "module_c" / "v0"))
    parser.add_argument("--base-url", default=None, help="Run against a live Module B instead of ASGI.")
    parser.add_argument("--only", default=None, help="Run a single case id prefix.")
    args = parser.parse_args(argv)
    report = run(Path(args.cases), Path(args.output), base_url=args.base_url, only=args.only)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    failed = [row["case_id"] for row in report["cases"] if not row["passed"]]
    print("failed:", failed or "none")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
