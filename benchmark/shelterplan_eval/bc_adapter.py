"""Explicit Eval -> Module C -> Module B adapter.

This module is intentionally a transport adapter, not an Agent and not a
fallback solver.  It only submits the frozen task contract to C's real
``/planning/disaster-recovery`` route.  A missing route is reported as
``NOT_RUN``/``UNSUPPORTED``; it is never replaced with a direct or mocked
result.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DISASTER_RECOVERY_PATH = "/planning/disaster-recovery"


@dataclass(frozen=True)
class AdapterOutcome:
    status: str
    reason: str
    response: dict[str, Any] | None = None
    execution_trace_id: str | None = None
    result_id: str | None = None


def build_disaster_recovery_payload(task: dict[str, Any], *, session_id: str, turn_id: str, idempotency_key: str) -> dict[str, Any]:
    """Translate one Eval task into C's declared disaster-recovery contract."""
    if task.get("expected_behavior") not in {"EXECUTE", "CLARIFY", "SAFE_STOP", "REJECT"}:
        raise ValueError("task has no supported GT behavior")
    if task.get("task_type") == "multi_round":
        source = task["rounds"][1]["payload_template"]
    else:
        source = task["b_request_payload"]
    scenario = dict(source.get("scenario") or {})
    gt = task.get("gt_assertions") or {}
    return {
        "task_type": "disaster_recovery",
        "task_id": task.get("task_id"),
        "client_session_id": session_id,
        "client_turn_id": turn_id,
        "user_goal": f"ShelterPlan Eval task {task.get('scenario_id')}",
        "fixture_id": None,
        "region_id": task.get("region_id"),
        "data_version": task.get("data_version"),
        "source_manifest_digest": (task.get("agent_input") or {}).get("source_manifest_sha256"),
        "analysis_mode": gt.get("analysis_mode", "demo"),
        "scenario": scenario,
        "baseline_parent_scenario_id": scenario.get("parent_scenario_id"),
        "damaged_shelter_ids": list(gt.get("disabled_shelter_ids") or scenario.get("disabled_shelter_ids") or []),
        "budget_units": scenario.get("budget_units"),
        "improvement_options": list(source.get("improvement_options") or []),
        "idempotency_key": idempotency_key,
    }


def submit_real_c(base_url: str, payload: dict[str, Any], *, timeout_seconds: float = 180.0, poll_interval_seconds: float = 0.1) -> AdapterOutcome:
    """Submit to C and poll its real B-backed job; never fall back silently."""
    url = base_url.rstrip("/") + DISASTER_RECOVERY_PATH
    try:
        accepted = _json_request("POST", url, payload, timeout_seconds=min(timeout_seconds, 30.0))
    except HTTPError as exc:
        if exc.code in {404, 405}:
            return AdapterOutcome("NOT_RUN", "C disaster-recovery endpoint is not available at this base URL")
        return AdapterOutcome("INFRA_ERROR", f"C submit HTTP {exc.code}")
    except (URLError, TimeoutError, OSError) as exc:
        return AdapterOutcome("INFRA_ERROR", f"C submit transport error: {exc}")
    if not isinstance(accepted, dict) or not accepted.get("job_id"):
        return AdapterOutcome("INFRA_ERROR", "C did not return a job_id", accepted if isinstance(accepted, dict) else None)
    job_id = str(accepted["job_id"])
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            job = _json_request("GET", f"{base_url.rstrip('/')}/planning/jobs/{job_id}", None, timeout_seconds=min(timeout_seconds, 30.0))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return AdapterOutcome("INFRA_ERROR", f"C polling error: {exc}", accepted, accepted.get("execution_trace_id"))
        if job.get("status") == "FAILED":
            return AdapterOutcome("FAIL", "C/B rejected or failed the real request", job, job.get("execution_trace_id"))
        if job.get("status") == "SUCCEEDED" and job.get("result_id"):
            result = _json_request("GET", f"{base_url.rstrip('/')}/planning/results/{job['result_id']}", None, timeout_seconds=min(timeout_seconds, 30.0))
            return AdapterOutcome("PASS", "C/B real disaster-recovery execution completed", normalize_composite_result(result), result.get("execution_trace_id"), result.get("result_id"))
        time.sleep(poll_interval_seconds)
    return AdapterOutcome("NOT_RUN", "C job polling timed out", accepted, accepted.get("execution_trace_id"))


def normalize_composite_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep only contract evidence needed by Eval; do not turn it into GT."""
    composite = ((result.get("result_detail") or {}).get("composite") or {})
    contract = composite.get("contract") or {}
    return {
        "status": result.get("status"),
        "task_id": result.get("task_id"),
        "result_id": result.get("result_id"),
        "scenario_id": result.get("scenario_id"),
        "region_id": result.get("region_id"),
        "data_version": result.get("data_version"),
        "data_status": result.get("data_status"),
        "source_manifest_digest": result.get("source_manifest_digest") or contract.get("source_manifest_digest"),
        "verification_status": result.get("verification_status"),
        "execution_trace_id": result.get("execution_trace_id") or contract.get("execution_trace_id"),
        "stage_results": contract.get("stage_results") or composite.get("stage_results") or [],
        "branch_decisions": contract.get("branch_decisions") or [],
        "constraint_checks": contract.get("constraint_checks") or [],
        "verification_results": contract.get("verification_results") or [],
    }


def _json_request(method: str, url: str, payload: dict[str, Any] | None, *, timeout_seconds: float) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, method=method, headers={"Content-Type": "application/json"} if body else {})
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - caller supplies the explicit local/approved base URL
        content = response.read()
    value = json.loads(content.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON response is not an object")
    return value
