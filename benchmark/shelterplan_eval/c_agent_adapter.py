"""Transport adapter for real Module C Agent sessions.

This module does not submit a structured B request. It creates a region-bound
C session, sends reviewed natural-language user turns, polls the documented C
turn endpoint, and preserves the returned session and turn evidence. It is an
execution adapter only; GT and VETS scoring remain in the evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


C_STATUS_TERMINAL = {
    "NEEDS_CLARIFICATION",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
}


@dataclass(frozen=True)
class AgentOutcome:
    """Raw C execution evidence, without an evaluator verdict."""

    status: str
    reason: str
    session_id: str | None = None
    session: dict[str, Any] | None = None
    turns: tuple[dict[str, Any], ...] = ()
    error: dict[str, Any] | None = None


def _number_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _option_text(option: dict[str, Any]) -> str:
    return (
        f"{option.get('shelter_id')} 扩容 {_number_text(option.get('delta_capacity_persons'))} 人，"
        f"成本 {_number_text(option.get('cost_units'))}"
    )


def _request_text(payload: dict[str, Any], *, safe_stop: bool = False) -> str:
    scenario = payload.get("scenario") or {}
    region_id = payload.get("region_id") or scenario.get("region_id") or "当前区域"
    parts = [f"请针对 {region_id} 的应急避难服务做评估或恢复规划。"]
    disabled = list(scenario.get("disabled_shelter_ids") or [])
    if disabled:
        parts.append(f"假设停用这些场所：{', '.join(disabled)}。")
    ratio = scenario.get("population_demand_ratio")
    walk = scenario.get("max_walk_time_min")
    if ratio is not None:
        parts.append(f"需求比例按 {_number_text(ratio)} 计算。")
    if walk is not None:
        parts.append(f"步行阈值为 {_number_text(walk)} 分钟。")
    budget = scenario.get("budget_units")
    if budget is not None:
        parts.append(f"预算为 {_number_text(budget)} 个单位。")
    options = list(payload.get("improvement_options") or [])
    if options:
        parts.append("候选恢复项目：" + "；".join(_option_text(option) for option in options) + "。")
    if safe_stop:
        parts.append("请先核验当前容量和可达性；如果已经满足目标，请安全停止，不要虚构新的恢复项目。")
    elif payload.get("task_type") == "assess":
        parts.append("请给出现状服务量、缺口和数据状态，并保留可追溯结果证据。")
    else:
        parts.append("请在满足约束的前提下执行，并返回可核验的阶段结果和证据。")
    return "".join(parts)


def build_agent_user_turns(task: dict[str, Any]) -> list[dict[str, str]]:
    """Build reviewed user-simulator turns without exposing hidden GT."""
    if task.get("user_turns"):
        turns = task["user_turns"]
        if not isinstance(turns, list) or not all(isinstance(row, str) for row in turns):
            raise ValueError("task.user_turns must be a list of strings")
        return [{"text": text, "client_turn_id": f"eval-{task.get('task_id')}-{index}"}
                for index, text in enumerate(turns, start=1)]

    if task.get("task_type") == "multi_round":
        rounds = task.get("rounds") or []
        if len(rounds) < 2:
            raise ValueError("multi_round task requires two reviewed rounds")
        first = _request_text(rounds[0]["payload"])
        second_payload = rounds[1]["payload_template"]
        second = _request_text(second_payload) + "这是同一会话的后续修改，请保留前一轮的区域、受损场所和数据版本。"
        return [
            {"text": first, "client_turn_id": f"eval-{task.get('task_id')}-1"},
            {"text": second, "client_turn_id": f"eval-{task.get('task_id')}-2"},
        ]

    payload = task.get("b_request_payload") or {}
    return [{
        "text": _request_text(payload, safe_stop=task.get("expected_behavior") == "SAFE_STOP"),
        "client_turn_id": f"eval-{task.get('task_id')}-1",
    }]


def _json_request(method: str, url: str, payload: dict[str, Any] | None,
                  *, timeout_seconds: float) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = Request(url, data=body, method=method, headers=headers)
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout_seconds) as response:  # noqa: S310 - base URL is caller supplied
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("C response is not a JSON object")
    return decoded


def _http_error(exc: HTTPError) -> dict[str, Any]:
    try:
        raw = exc.read().decode("utf-8")
        detail = json.loads(raw) if raw else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        detail = {}
    return {"http_status": exc.code, "detail": detail}


def submit_real_c_agent(
    base_url: str,
    task: dict[str, Any],
    *,
    timeout_seconds: float = 180.0,
    poll_interval_seconds: float = 0.5,
    request_fn: Callable[..., dict[str, Any]] | None = None,
) -> AgentOutcome:
    """Run one task through the real C session and turn API."""
    request = request_fn or _json_request
    root = base_url.rstrip("/")
    try:
        created = request(
            "POST",
            f"{root}/v1/sessions",
            {
                "region_id": task.get("region_id"),
                "data_version": task.get("data_version"),
            },
            timeout_seconds=30.0,
        )
    except HTTPError as exc:
        if exc.code in {404, 405}:
            return AgentOutcome("NOT_RUN", "C session API is not available at this base URL", error=_http_error(exc))
        return AgentOutcome("INFRA_ERROR", "C session creation returned an HTTP error", error=_http_error(exc))
    except (URLError, TimeoutError, OSError) as exc:
        return AgentOutcome("INFRA_ERROR", f"C session creation transport error: {exc}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return AgentOutcome("INFRA_ERROR", f"C session creation returned an invalid response: {exc}")

    session_id = created.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return AgentOutcome("INFRA_ERROR", "C did not return a session_id", error=created)

    turns: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_seconds
    try:
        for turn in build_agent_user_turns(task):
            if time.monotonic() >= deadline:
                return AgentOutcome("NOT_RUN", "C agent run exceeded the execution timeout", session_id, created, tuple(turns))
            accepted = request(
                "POST", f"{root}/v1/sessions/{session_id}/turns",
                {"text": turn["text"], "client_turn_id": turn["client_turn_id"]},
                timeout_seconds=30.0,
            )
            turn_id = accepted.get("turn_id")
            if not isinstance(turn_id, str) or not turn_id:
                return AgentOutcome("INFRA_ERROR", "C did not return a turn_id", session_id, created, tuple(turns), accepted)
            while True:
                if time.monotonic() >= deadline:
                    return AgentOutcome("NOT_RUN", "C turn polling timed out", session_id, created, tuple(turns))
                observed = request("GET", f"{root}/v1/turns/{turn_id}", None, timeout_seconds=30.0)
                status = observed.get("status")
                if status in C_STATUS_TERMINAL:
                    turns.append(observed)
                    break
                if status != "RUNNING":
                    return AgentOutcome("INFRA_ERROR", f"C returned an unknown turn status: {status!r}", session_id, created, tuple(turns), observed)
                time.sleep(poll_interval_seconds)
            if turns[-1].get("status") in {"FAILED", "CANCELLED"}:
                break
    except HTTPError as exc:
        return AgentOutcome("INFRA_ERROR", "C agent request returned an HTTP error", session_id, created, tuple(turns), _http_error(exc))
    except (URLError, TimeoutError, OSError) as exc:
        return AgentOutcome("INFRA_ERROR", f"C agent transport error: {exc}", session_id, created, tuple(turns))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return AgentOutcome("INFRA_ERROR", f"C agent response was invalid: {exc}", session_id, created, tuple(turns))

    try:
        session = request("GET", f"{root}/v1/sessions/{session_id}", None, timeout_seconds=30.0)
    except (HTTPError, URLError, TimeoutError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return AgentOutcome("INFRA_ERROR", f"C session snapshot was unavailable: {exc}", session_id, created, tuple(turns))

    final_status = turns[-1].get("status") if turns else "NOT_RUN"
    return AgentOutcome(final_status, "real C session and turn execution completed", session_id, session, tuple(turns))


def normalize_agent_evidence(outcome: AgentOutcome) -> dict[str, Any]:
    """Project C evidence for the evaluator without assigning a score."""
    final = outcome.turns[-1] if outcome.turns else {}
    return {
        "status": outcome.status,
        "reason": outcome.reason,
        "session_id": outcome.session_id,
        "region_id": final.get("region_id") or (outcome.session or {}).get("region_id"),
        "data_version": final.get("data_version") or (outcome.session or {}).get("data_version"),
        "data_status": final.get("data_status") or (outcome.session or {}).get("data_status"),
        "result_id": final.get("result_id"),
        "execution_trace_id": final.get("execution_trace_id"),
        "turn_count": len(outcome.turns),
        "session": outcome.session,
        "turns": list(outcome.turns),
        "error": outcome.error,
    }
