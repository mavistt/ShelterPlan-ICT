"""V2 four-arm ablation runner.

    python -m tests.module_c.run_v2 --split all --arms all --reps 2

Arms
----
baseline       V1 arm A: raw tools, stateless, single attempt.
harness        V1 arm B: session + guards + projection + gates.
harness_struct V2 arm C: structured intent + deterministic routing, model writes prose.
harness_opt    V2 arm D: C + cheap error handling + deterministic answer binding.

Fairness rules baked in (see docs/module_c/V1_EVAL_AUDIT.md):
* identical case order per arm, arm order rotated per case and repetition;
* every (case, arm, rep) gets its own session, so no state leaks;
* numbers are graded against what the arm's model actually received;
* repeated runs are reported per case, never only as an aggregate.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient  # noqa: E402

from agent.agent_loop import AgentRunner  # noqa: E402
from agent.agent_tools import baseline_registry, harness_registry, SHARED_SYSTEM_PROMPT  # noqa: E402
from agent.b_client import BClient  # noqa: E402
from agent.config import HarnessConfig  # noqa: E402
from agent.harness.trace import TraceRecorder  # noqa: E402
from agent.llm_adapter import LLMAdapter, LLMConfig  # noqa: E402
from agent.numeric_audit import check_citations, extract_planning_numbers  # noqa: E402
from agent.service import HarnessService  # noqa: E402
from agent.structured_runner import StructuredRunner  # noqa: E402
from backend.app import app as b_app  # noqa: E402
from tests.module_c.eval_lib import (  # noqa: E402
    ARMS,
    STRUCTURED_ARMS,
    demo_flags,
    grade,
    load_cases,
    metrics,
    normalise_params,
    ops_match,
    paired,
    percentile,
)

CASES_PATH = Path(__file__).resolve().parent / "cases" / "v2_cases.json"


# --------------------------------------------------------------------------- #
def _session_params(service: HarnessService, session_id: str) -> dict[str, Any]:
    state = service.session_state(session_id)
    return normalise_params((state.get("current") or {}).get("params") or {})


def _user_numbers(case: dict[str, Any]) -> list[float]:
    numbers: list[float] = []
    for turn in case["turns"]:
        numbers.extend(extract_planning_numbers(turn.get("user") or ""))
    return numbers


def _classify(failures: list[str]) -> list[str]:
    taxonomy = []
    for failure in failures:
        if failure.startswith("task_types:"):
            taxonomy.append("wrong_task")
        elif failure.startswith("params:"):
            taxonomy.append("param_error")
        elif failure.startswith("numeric"):
            taxonomy.append("numeric_violation")
        elif failure.startswith("demo"):
            taxonomy.append("demo_violation")
        elif failure.startswith("no_result"):
            taxonomy.append("executed_when_blocked")
        elif failure.startswith("clarify"):
            taxonomy.append("no_clarification")
        elif failure.startswith("intent:"):
            taxonomy.append("intent_error")
        elif failure.startswith("ops:"):
            taxonomy.append("slot_error")
        elif failure.startswith("runner_error"):
            taxonomy.append("runner_error")
        elif failure.startswith("loop_limit"):
            taxonomy.append("loop_limit")
        else:
            taxonomy.append("other")
    return sorted(set(taxonomy)) or ["none"]


def _intent_summary(intents: list[dict[str, Any]]) -> tuple[
        str | None, list[dict] | None, list[dict[str, Any]]]:
    """`intent` / `last_ops` must come from the *same* (last) turn.

    Taking the first turn's intent while taking the last turn's ops made
    multi-turn cases grade the wrong turn; V2.1 §6.5 documents the one case this
    affected. The full per-turn sequence is returned so nothing is hidden.
    """
    if not intents:
        return None, None, []
    last = intents[-1]
    payload = last.get("intent") or {}
    sequence = [{"turn": row.get("turn"),
                 "intent": (row.get("intent") or {}).get("intent"),
                 "ops": (row.get("intent") or {}).get("ops") or []} for row in intents]
    return payload.get("intent"), payload.get("ops"), sequence


def _record(case: dict[str, Any], arm: str, rep: int, *, final_text: str | None,
            successful_types: list[str], final_params: dict[str, Any],
            numeric_document: Any, user_numbers: list[float],
            llm_calls: int, llm_latency_ms: float, b_api_ms: float,
            duration_ms: float, prompt_tokens: int, completion_tokens: int,
            tool_calls: int, planning_tasks: int, retries: int,
            intent: str | None, last_ops: list[dict] | None,
            clarification_fields: list[str], runner_error: str | None,
            max_turns_hit: bool, b_submissions: int = 0,
            intents_per_turn: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    audit = check_citations(final_text or "", {
        **({"documents": numeric_document} if arm == "baseline" else
           {"summaries": numeric_document}),
        # Current parameters are legitimate state, not invented numbers: a
        # clarification may quote them even when no result exists yet.
        "params": final_params,
        "user_given": user_numbers,
    })
    produced = bool(successful_types)
    expect = case.get("expect") or {}
    record: dict[str, Any] = {
        "case_id": case["case_id"],
        "group": case.get("group"),
        "capability": case.get("capability"),
        "arm": arm,
        "rep": rep,
        "inherits": bool(case.get("inherits")),
        "final_text": final_text,
        "successful_task_types": sorted(set(successful_types)),
        "final_params": normalise_params(final_params),
        "numeric": audit,
        "demo": demo_flags(final_text, produced_result=produced),
        "clarification_fields": clarification_fields,
        # `intent` is the *last* turn's intent, the same turn `last_ops` comes
        # from. Recording the first turn's intent here used to make multi-turn
        # cases grade the wrong turn (V2.1 §6.5); the full sequence is kept so a
        # reviewer can still see every turn.
        "intent": intent,
        "last_ops": last_ops,
        "intents_per_turn": intents_per_turn or [],
        "slot_expected": bool("ops" in expect and arm in STRUCTURED_ARMS),
        "llm_calls": llm_calls,
        "llm_latency_ms": round(llm_latency_ms, 1),
        "b_api_ms": round(b_api_ms, 1),
        "duration_ms": round(duration_ms, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tool_calls": tool_calls,
        "planning_tasks": planning_tasks,
        # A business-task submission is not the same thing as an LLM call or a
        # tool call: one submission can poll Module B dozens of times.
        "b_submissions": b_submissions,
        "retries": retries,
        "runner_error": runner_error,
        "max_turns_hit": max_turns_hit,
    }
    record["slot_ok"] = (not ops_match(expect.get("ops"), last_ops)
                         if record["slot_expected"] else False)
    failures, info = grade(case, record)
    info = dict(info)
    if record["slot_expected"]:
        info["slot_ok"] = record["slot_ok"]
    if case.get("group") == "recover":
        info["recovered"] = not failures
    record.update({"passed": not failures, "failures": failures, "info": info,
                   "failure_types": _classify(failures)})
    return record


# --------------------------------------------------------------------------- #
def run_one(case: dict[str, Any], arm: str, rep: int, *, service: HarnessService,
            llm: LLMAdapter, config: HarnessConfig, shared_client: TestClient,
            trace_dir: Path, max_llm_calls: int) -> dict[str, Any]:
    session_id = f"v2-r{rep}-{arm}-{case['case_id'].lower()}"
    trace = TraceRecorder(trace_dir / f"r{rep}-{arm}-{case['case_id']}.jsonl", session_id)
    user_numbers = _user_numbers(case)
    started = time.perf_counter()

    if arm == "baseline":
        client = BClient(replace(config, max_transport_retries=0), client=shared_client)
        registry, state = baseline_registry(client, config)
        runner = AgentRunner("baseline", registry, SHARED_SYSTEM_PROMPT, llm, config=config,
                             trace=trace, session_id=session_id, max_llm_calls=max_llm_calls)
        result = runner.run_case(case["case_id"], case["turns"])
        duration = (time.perf_counter() - started) * 1000
        completed = [row for row in state["requests"] if row.get("completed")]
        successful = [row["payload"].get("task_type") for row in completed
                      if row["payload"].get("task_type")]
        last = (completed[-1] if completed else (state["requests"][-1] if state["requests"] else None))
        final_params = (last or {}).get("effective_params") or {}
        return _record(
            case, arm, rep, final_text=result.final_text, successful_types=successful,
            final_params=final_params, numeric_document=list(state["visible"].values()),
            user_numbers=user_numbers, llm_calls=len(result.llm_calls),
            llm_latency_ms=result.llm_latency_ms, b_api_ms=state.get("b_ms", 0.0),
            duration_ms=duration, prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens, tool_calls=len(result.tool_calls),
            planning_tasks=len(completed), retries=0, intent=None, last_ops=None,
            b_submissions=len(state["requests"]),
            clarification_fields=[], runner_error=result.error,
            max_turns_hit=result.max_turns_hit)

    if arm == "harness":
        registry, _ = harness_registry(service, session_id)
        runner = AgentRunner("harness", registry, SHARED_SYSTEM_PROMPT, llm, config=config,
                             trace=trace, service=service, session_id=session_id,
                             max_llm_calls=max_llm_calls)
        result = runner.run_case(case["case_id"], case["turns"])
        duration = (time.perf_counter() - started) * 1000
        successful = [row["task_type"] for row in result.successful_tasks if row.get("task_type")]
        summaries = [row["summary"] for row in result.submitted if row.get("summary")]
        return _record(
            case, arm, rep, final_text=result.final_text, successful_types=successful,
            final_params=_session_params(service, session_id), numeric_document=summaries,
            user_numbers=user_numbers, llm_calls=len(result.llm_calls),
            llm_latency_ms=result.llm_latency_ms,
            b_api_ms=sum(call.latency_ms for call in result.tool_calls),
            duration_ms=duration, prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens, tool_calls=len(result.tool_calls),
            planning_tasks=len({row.get("result_id") for row in result.successful_tasks
                                if row.get("result_id")}),
            retries=0, intent=None, last_ops=None, clarification_fields=[],
            b_submissions=sum(1 for call in result.tool_calls if call.name == "submit_task"),
            runner_error=result.error, max_turns_hit=result.max_turns_hit)

    runner = StructuredRunner(arm, service, llm, config, session_id=session_id, trace=trace,
                              max_llm_calls=max_llm_calls)
    result = runner.run_case(case["case_id"], case["turns"])
    duration = (time.perf_counter() - started) * 1000
    successful = [row["task_type"] for row in result.successful_tasks if row.get("task_type")]
    summaries = [row["summary"] for row in result.submitted if row.get("summary")]
    intent_name, last_ops, intents_per_turn = _intent_summary(result.intents)
    retries = sum(max(0, int(row.get("attempts") or 1) - 1) for row in result.controls)
    return _record(
        case, arm, rep, final_text=result.final_text, successful_types=successful,
        final_params=_session_params(service, session_id), numeric_document=summaries,
        user_numbers=user_numbers, llm_calls=len(result.llm_calls),
        llm_latency_ms=result.llm_latency_ms, b_api_ms=result.b_api_ms,
        duration_ms=duration, prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens, tool_calls=len(result.tool_calls),
        planning_tasks=sum(1 for row in result.controls if row.get("status") == "ok"),
        retries=retries, intent=intent_name, last_ops=last_ops,
        intents_per_turn=intents_per_turn,
        clarification_fields=list(result.clarification_fields),
        b_submissions=len(result.tool_calls),
        runner_error=result.error, max_turns_hit=result.max_turns_hit)


# --------------------------------------------------------------------------- #
def run(*, cases_path: Path = CASES_PATH, output_dir: Path, arms: list[str],
        split: str = "all", reps: int = 1, rep_start: int = 0,
        only: str | None = None, groups: list[str] | None = None,
        llm_config: LLMConfig | None = None, max_llm_calls: int = 10) -> dict[str, Any]:
    wanted = set(groups or ([] if split == "all" else [split]))
    needles = [part.strip() for part in (only or "").split(",") if part.strip()]
    cases = [case for case in load_cases(cases_path)
             if (not wanted or case.get("group") in wanted)
             and (not needles or any(needle in case["case_id"] for needle in needles))]
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = output_dir / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)

    config = HarnessConfig()
    shared_client = TestClient(b_app)
    llm = LLMAdapter(llm_config or LLMConfig.from_env())
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
             for left, right in (("baseline", "harness"), ("harness", "harness_struct"),
                                 ("harness_struct", "harness_opt"), ("baseline", "harness_opt"))}
    report = {
        "run_id": f"v2-{int(started)}",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "model": {
            "base_url": (llm_config or LLMConfig.from_env()).base_url,
            "model": (llm_config or LLMConfig.from_env()).model,
            "temperature": (llm_config or LLMConfig.from_env()).temperature,
        },
        "design": {
            "arms": arms,
            "reps": reps,
            "rep_start": rep_start,
            "case_count": len(cases),
            "split": split,
            "groups": sorted(wanted) or "all",
            "arm_order": "rotated per case and repetition",
            "note": ("Real local LLM. No paid API. Deterministic V0 numbers are not mixed in. "
                     "The V1 holdout is a regression set, not a blind set."),
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


def _capability_matrix(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Pass counts per (group, arm), so a single aggregate cannot hide a collapse."""
    matrix: dict[str, dict[str, Any]] = {}
    for row in records:
        group = row.get("group") or "unknown"
        bucket = matrix.setdefault(group, {})
        entry = bucket.setdefault(row["arm"], {"passed": 0, "total": 0})
        entry["total"] += 1
        entry["passed"] += int(row["passed"])
    for bucket in matrix.values():
        for entry in bucket.values():
            entry["rate"] = round(entry["passed"] / entry["total"], 4) if entry["total"] else None
    return matrix


def _write_summary(report: dict[str, Any], path: Path) -> None:
    lines = [f"# V2 run {report['run_id']}", "",
             f"model `{report['model']['model']}` · {report['design']['case_count']} cases × "
             f"{len(report['design']['arms'])} arms × {report['design']['reps']} rep(s)", "",
             "## per-arm metrics", "",
             "| metric | " + " | ".join(report["by_arm"]) + " |",
             "|---|" + "---:|" * len(report["by_arm"])]
    keys = ["case_pass_rate", "task_routing_rate", "param_rate", "inheritance_rate",
            "numeric_consistency_rate", "demo_compliance_rate", "clarify_rate",
            "intent_accuracy", "slot_accuracy", "recovery_rate", "llm_calls_per_case",
            "planning_tasks_per_case", "tokens_per_case", "case_latency_p50_ms",
            "case_latency_p95_ms", "llm_latency_p50_ms", "b_api_latency_p50_ms",
            "b_api_success_p50_ms", "retries_total"]
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
    lines += ["", "## paired comparisons", ""]
    for name, pair in report["paired"].items():
        lines.append(f"- **{name}**: {pair['right_wins']} wins / {pair['right_losses']} losses / "
                     f"{pair['ties']} ties over {pair['pairs']} pairs")
    lines += ["", "## failed cases", ""]
    for row in report["cases"]:
        if not row["passed"]:
            lines.append(f"- **{row['arm']}/{row['case_id']}** (r{row['rep']}): "
                         f"{'; '.join(row['failures'])[:220]}")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m tests.module_c.run_v2")
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--output", default=str(ROOT / "outputs" / "module_c" / "v2"))
    parser.add_argument("--arms", default="all")
    parser.add_argument("--split", default="all",
                        choices=["all", "plan", "multi", "relative", "clarify", "recover"])
    parser.add_argument("--groups", default=None,
                        help="comma-separated groups, e.g. clarify,relative")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--rep-start", type=int, default=0,
                        help="first repetition index (lets a later run extend an earlier one)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--max-llm-calls", type=int, default=10)
    parser.add_argument("--only", default=None)
    args = parser.parse_args(argv)

    arms = list(ARMS) if args.arms == "all" else args.arms.split(",")
    llm_config = LLMConfig.from_env()
    if args.model:
        llm_config = replace(llm_config, model=args.model)
    if args.llm_base_url:
        llm_config = replace(llm_config, base_url=args.llm_base_url)

    report = run(cases_path=Path(args.cases), output_dir=Path(args.output), arms=arms,
                 split=args.split, reps=args.reps, rep_start=args.rep_start, only=args.only,
                 groups=(args.groups.split(",") if args.groups else None),
                 llm_config=llm_config, max_llm_calls=args.max_llm_calls)
    print(json.dumps({"by_arm": report["by_arm"], "paired": {
        key: {k: v for k, v in value.items() if k != "cases"}
        for key, value in report["paired"].items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
