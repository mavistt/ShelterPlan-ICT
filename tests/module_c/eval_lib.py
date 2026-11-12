"""Fair grading and metrics for the V2 four-arm ablation.

Every rule here exists because the V1 evaluator was unfair or irreproducible in
a specific, documented way (see docs/module_c/V1_EVAL_AUDIT.md):

* the baseline is graded against the document it *received*, not the 5-11 MB
  document it never saw;
* refusal detection uses structured clarification records when the arm has
  them, and a narrower text heuristic otherwise (no bare "无法" match);
* per-case pairing replaces a single aggregate comparison;
* intent/slot metrics are only computed for arms that actually produce an
  intent envelope (otherwise they are reported as n/a, never as zero).
"""
from __future__ import annotations

import json
from pathlib import Path
import statistics
from typing import Any, Iterable

STRUCTURED_ARMS = ("harness_struct", "harness_opt")
ARMS = ("baseline", "harness", "harness_struct", "harness_opt")

REFUSAL_TOKENS = ("无法执行", "不能执行", "未执行", "拒绝执行", "缺少必要参数",
                  "没有可解释", "未通过执行前校验", "无法开始")
REQUEST_TOKENS = ("请提供", "请给出", "请指定", "请补充", "请确认", "请说明",
                  "需要您", "需要你", "请告诉我")


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(document["cases"])


# --------------------------------------------------------------------------- #
# Parameter comparison
# --------------------------------------------------------------------------- #
def normalise_params(params: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(params or {})
    if "disabled_shelter_ids" in out:
        out["disabled_shelter_ids"] = sorted(map(str, out.get("disabled_shelter_ids") or []))
    return out


def params_match(expected: dict[str, Any], actual: dict[str, Any] | None) -> list[str]:
    actual = normalise_params(actual)
    failures = []
    for key, value in expected.items():
        got = actual.get(key)
        if isinstance(value, list):
            if sorted(map(str, got or [])) != sorted(map(str, value)):
                failures.append(f"params: {key} expected {value} got {got}")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if got is None or abs(float(got) - float(value)) > 1e-6:
                failures.append(f"params: {key} expected {value} got {got}")
        elif got != value:
            failures.append(f"params: {key} expected {value} got {got}")
    return failures


def ops_match(expected: list[dict[str, Any]] | None,
              actual: list[dict[str, Any]] | None) -> list[str]:
    """Compare a turn's parameter operations, order-insensitive."""
    if expected is None:
        return []
    expected_rows = {_op_key(row) for row in expected}
    actual_rows = {_op_key(row) for row in (actual or [])}
    missing = expected_rows - actual_rows
    if missing:
        return [f"ops: missing {sorted(missing)}; got {sorted(actual_rows)}"]
    return []


def _op_key(row: dict[str, Any]) -> str:
    value = row.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = round(float(value), 6)
    return f"{row.get('field')}:{row.get('op')}:{value}"


# --------------------------------------------------------------------------- #
# Refusal / disclosure heuristics
# --------------------------------------------------------------------------- #
def asks_or_refuses(text: str | None) -> bool:
    text = text or ""
    if any(token in text for token in REFUSAL_TOKENS):
        return True
    if any(token in text for token in REQUEST_TOKENS):
        return True
    # A question mark only counts when the sentence is short and interrogative;
    # a long answer ending in "？" is usually rhetorical, not a clarification.
    if "？" in text or "?" in text:
        for sentence in text.replace("?", "？").split("？"):
            if 0 < len(sentence.strip()) <= 80:
                return True
    return False


def demo_flags(text: str | None, *, produced_result: bool) -> dict[str, Any]:
    from agent.agent_loop import FORBIDDEN_DEMO_PHRASES
    text = text or ""
    forbidden = [phrase for phrase in FORBIDDEN_DEMO_PHRASES if phrase in text]
    disclosed = ("DEMO_ONLY" in text) or ("未经" in text) or ("未核验" in text)
    return {
        "disclosed": bool(disclosed and produced_result),
        "forbidden": forbidden,
        "ok": bool(produced_result and disclosed and not forbidden),
    }


# --------------------------------------------------------------------------- #
# Grading one record
# --------------------------------------------------------------------------- #
def grade(case: dict[str, Any], record: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    expect = case.get("expect") or {}
    failures: list[str] = []
    info: dict[str, Any] = {}
    structured = record.get("arm") in STRUCTURED_ARMS
    produced = bool(record.get("successful_task_types"))

    if "task_types" in expect:
        missing = [task for task in expect["task_types"]
                   if task not in record.get("successful_task_types", [])]
        info["task_types_ok"] = not missing
        if missing:
            failures.append(f"task_types: missing {missing}; "
                            f"got {record.get('successful_task_types')}")

    if "params" in expect:
        matched = params_match(expect["params"], record.get("final_params"))
        info["params_ok"] = not matched
        failures.extend(matched)

    if expect.get("numeric_consistent"):
        consistent = bool((record.get("numeric") or {}).get("consistent"))
        info["numeric_ok"] = consistent
        if not consistent:
            failures.append(f"numeric: cited numbers not in tool output "
                            f"{(record.get('numeric') or {}).get('violations')}")

    if expect.get("demo_disclosed"):
        demo = record.get("demo") or {}
        info["demo_ok"] = bool(demo.get("ok"))
        if produced and not demo.get("ok"):
            failures.append("demo: missing DEMO_ONLY disclosure or used forbidden wording "
                            f"{demo.get('forbidden')}")

    if "no_result" in expect:
        blocked = expect["no_result"]
        if blocked in record.get("successful_task_types", []):
            failures.append(f"no_result: produced a successful {blocked} result")
        clarified = bool(record.get("clarification_fields")) or asks_or_refuses(record.get("final_text"))
        info["clarified"] = clarified
        if not clarified:
            failures.append("clarify: answer neither asked for the missing input nor explained the refusal")

    if "clarify_fields" in expect and structured:
        expected_fields = set(expect["clarify_fields"])
        actual_fields = set(record.get("clarification_fields") or [])
        info["clarify_fields"] = sorted(actual_fields)
        missing = expected_fields - actual_fields
        if missing:
            failures.append(f"clarify_fields: missing {sorted(missing)}; got {sorted(actual_fields)}")

    if "intent" in expect and structured:
        expected_intent = expect["intent"]
        got = record.get("intent")
        info["intent"] = got
        if isinstance(expected_intent, list):
            ok = got in expected_intent
        else:
            ok = got == expected_intent
        info["intent_ok"] = ok
        if not ok:
            failures.append(f"intent: expected {expected_intent} got {got}")

    if "ops" in expect and structured:
        failures.extend(ops_match(expect["ops"], record.get("last_ops")))

    if record.get("runner_error"):
        failures.append(f"runner_error: {record['runner_error'][:160]}")
    if record.get("max_turns_hit"):
        failures.append("loop_limit: exhausted the LLM call budget")

    return failures, info


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 1)


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return round(sum(1 for row in rows if (row.get("info") or {}).get(key)) / len(rows), 4)


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"case_count": 0}
    passed = sum(1 for row in rows if row["passed"])
    structured = [row for row in rows if row.get("arm") in STRUCTURED_ARMS]
    taxonomy: dict[str, int] = {}
    for row in rows:
        for kind in row.get("failure_types") or []:
            taxonomy[kind] = taxonomy.get(kind, 0) + 1

    def subset(predicate) -> list[dict[str, Any]]:
        return [row for row in rows if predicate(row)]

    return {
        "case_count": len(rows),
        "case_pass_count": passed,
        "case_pass_rate": round(passed / len(rows), 4),
        "task_routing_rate": _rate(subset(lambda row: "task_types_ok" in (row.get("info") or {})),
                                   "task_types_ok"),
        "param_rate": _rate(subset(lambda row: "params_ok" in (row.get("info") or {})), "params_ok"),
        "inheritance_rate": _rate(
            subset(lambda row: row.get("inherits") and "params_ok" in (row.get("info") or {})),
            "params_ok"),
        "numeric_consistency_rate": _rate(
            subset(lambda row: "numeric_ok" in (row.get("info") or {})), "numeric_ok"),
        "demo_compliance_rate": _rate(
            subset(lambda row: "demo_ok" in (row.get("info") or {})), "demo_ok"),
        "clarify_rate": _rate(subset(lambda row: "clarified" in (row.get("info") or {})), "clarified"),
        "intent_accuracy": _rate(subset(lambda row: "intent_ok" in (row.get("info") or {})), "intent_ok")
        if structured else None,
        "slot_accuracy": _slot_accuracy(subset(lambda row: row.get("slot_expected"))),
        "recovery_rate": _rate(subset(lambda row: row.get("group") == "recover"), "recovered"),
        "llm_calls_per_case": round(statistics.fmean(row["llm_calls"] for row in rows), 3),
        "tool_calls_per_case": round(statistics.fmean(row["tool_calls"] for row in rows), 3),
        "planning_tasks_per_case": round(statistics.fmean(row["planning_tasks"] for row in rows), 3),
        "tokens_per_case": round(statistics.fmean(
            row["prompt_tokens"] + row["completion_tokens"] for row in rows), 1),
        "prompt_tokens_total": sum(row["prompt_tokens"] for row in rows),
        "completion_tokens_total": sum(row["completion_tokens"] for row in rows),
        "llm_latency_p50_ms": percentile((row["llm_latency_ms"] for row in rows), 0.5),
        "llm_latency_p95_ms": percentile((row["llm_latency_ms"] for row in rows), 0.95),
        "b_api_latency_p50_ms": percentile((row["b_api_ms"] for row in rows), 0.5),
        "b_api_latency_p95_ms": percentile((row["b_api_ms"] for row in rows), 0.95),
        # Only cases that actually completed a business task: otherwise failed
        # arms look "fast" because they never waited for Module B.
        "b_api_success_p50_ms": percentile(
            (row["b_api_ms"] for row in rows if row.get("planning_tasks")), 0.5),
        "b_api_success_p95_ms": percentile(
            (row["b_api_ms"] for row in rows if row.get("planning_tasks")), 0.95),
        "case_latency_p50_ms": percentile((row["duration_ms"] for row in rows), 0.5),
        "case_latency_p95_ms": percentile((row["duration_ms"] for row in rows), 0.95),
        "retries_total": sum(row["retries"] for row in rows),
        "failure_taxonomy": taxonomy,
    }


def _slot_accuracy(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return round(sum(1 for row in rows if row.get("slot_ok")) / len(rows), 4)


def paired(rows: list[dict[str, Any]], left: str, right: str) -> dict[str, Any]:
    """Per-case paired comparison between two arms (same case, same repetition)."""
    by_key: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_key.setdefault((row["case_id"], row["rep"]), {})[row["arm"]] = row
    pairs = []
    for (case_id, rep), arms in by_key.items():
        first, second = arms.get(left), arms.get(right)
        if first is None or second is None:
            continue
        pairs.append({
            "case_id": case_id,
            "rep": rep,
            "left": first["passed"],
            "right": second["passed"],
            "latency_left": first["duration_ms"],
            "latency_right": second["duration_ms"],
        })
    wins = sum(1 for pair in pairs if pair["right"] and not pair["left"])
    losses = sum(1 for pair in pairs if pair["left"] and not pair["right"])
    ties = len(pairs) - wins - losses
    return {
        "left": left,
        "right": right,
        "pairs": len(pairs),
        "right_wins": wins,
        "right_losses": losses,
        "ties": ties,
        "cases": pairs,
    }
