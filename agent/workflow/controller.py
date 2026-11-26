"""Task execution with error classification and bounded, cheap recovery.

The controller is the only place that calls Module B for an agent turn. It
never re-plans B's internal DAG: it submits one business task, waits for the
harness executor (which polls, guards and projects), then classifies whatever
came back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from agent.config import HarnessConfig
from agent.harness.results import resolve_optimality
from agent.workflow.router import RoutePlan

# Codes worth a second attempt without asking the model anything. Everything
# else is either the caller's fault (guard) or a result-integrity problem where
# retrying would only burn compute.
RETRYABLE_CODES = {"TRANSPORT_ERROR", "JOB_TIMEOUT", "JOB_STATE_LOST"}

GUARD_PREFIX = "GUARD_"


def classify_error(code: str | None) -> str:
    if not code:
        return "internal"
    if code.startswith(GUARD_PREFIX):
        return "guard"
    if code in RETRYABLE_CODES:
        return "upstream_transient"
    if code.startswith("B_") or code in {
        "HTTP_ERROR", "JOB_FAILED", "JOB_NOT_FOUND",
        # Module B rejects these before computing; retrying would not help and
        # the user can act on the message.
        "SCENARIO_INVALID", "CANDIDATE_INVALID", "DATA_INVALID", "DATA_UNAVAILABLE",
        "DATA_NOT_VERIFIED", "TASK_TYPE_MISMATCH", "REQUEST_TOO_LARGE",
        "JOB_LIMIT_REACHED", "FIXTURE_INVALID",
    }:
        return "upstream_rejected"
    if code in {"RESULT_MISMATCH", "RESULT_NOT_FOUND"}:
        return "integrity"
    return "internal"


@dataclass
class ControlResult:
    status: str  # ok | clarify | refuse | error
    plan: RoutePlan
    outcome: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    error_class: str | None = None
    attempts: int = 0
    b_api_ms: float = 0.0
    summary: dict[str, Any] | None = None
    bindings: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def error_code(self) -> str | None:
        return (self.error or {}).get("code")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "task_type": self.plan.task_type,
            "action": self.plan.action,
            "attempts": self.attempts,
            "b_api_ms": round(self.b_api_ms, 1),
            "error_code": self.error_code,
            "error_class": self.error_class,
            "result_id": (self.summary or {}).get("result_id"),
            "reason_codes": self.plan.reason_codes,
            "clarify_fields": self.plan.clarify_fields,
        }


class TaskController:
    def __init__(self, service: Any, config: HarnessConfig | None = None, *,
                 session_id: str | None = None,
                 max_attempts: int = 2, backoff_s: float = 0.5) -> None:
        self.service = service
        self.config = config or HarnessConfig()
        self.session_id = session_id
        self.max_attempts = max(1, max_attempts)
        self.backoff_s = backoff_s

    def run(self, plan: RoutePlan) -> ControlResult:
        if plan.action != "task":
            return ControlResult(status=plan.action, plan=plan)

        attempts = 0
        total_ms = 0.0
        outcome: dict[str, Any] | None = None
        while attempts < self.max_attempts:
            attempts += 1
            outcome = self.service.submit(
                plan.task_type,
                session_id=self.session_id,
                changes=plan.changes,
                parameters=plan.parameters,
                improvement_options=plan.options,
                result_id=plan.result_id,
                claim=plan.claim,
                user_goal=plan.user_goal,
            )
            total_ms += float(outcome.get("duration_ms") or 0.0)
            if outcome.get("ok"):
                return ControlResult(
                    status="ok", plan=plan, outcome=outcome, attempts=attempts,
                    b_api_ms=total_ms, summary=outcome.get("summary"),
                    bindings=bind_outcome(outcome),
                )
            code = (outcome.get("error") or {}).get("code")
            if code not in RETRYABLE_CODES or attempts >= self.max_attempts:
                break
            time.sleep(self.backoff_s * attempts)

        return ControlResult(
            status="error", plan=plan, outcome=outcome,
            error=(outcome or {}).get("error"),
            error_class=classify_error((outcome or {}).get("error", {}).get("code")),
            attempts=attempts, b_api_ms=total_ms,
        )


def bind_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    """Everything the answer is allowed to say, bound to the verified summary."""
    summary = outcome.get("summary") or {}
    metrics = summary.get("metrics") or {}
    bindings: dict[str, Any] = {
        key: value for key, value in metrics.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    served, total = metrics.get("served_demand"), metrics.get("total_demand")
    if isinstance(served, (int, float)) and not isinstance(served, bool) and isinstance(total, (int, float)) and not isinstance(total, bool) and total:
        bindings["coverage_ratio"] = served / total
    detail = summary.get("detail") or {}
    opt = detail.get("optimization") if isinstance(detail, dict) else None
    if isinstance(opt, dict):
        opt_served = opt.get("served_demand")
        if isinstance(opt_served, (int, float)) and not isinstance(opt_served, bool):
            if isinstance(total, (int, float)) and not isinstance(total, bool) and total:
                bindings["optimized_coverage_ratio"] = opt_served / total
            if isinstance(served, (int, float)) and not isinstance(served, bool):
                bindings["coverage_delta_persons"] = opt_served - served
        opt_walk = opt.get("weighted_walk_time_min")
        if opt_walk is None or (isinstance(opt_walk, (int, float)) and not isinstance(opt_walk, bool)):
            bindings["optimized_weighted_walk_time_min"] = opt_walk
        bindings["primary_stage_optimal"] = opt.get("primary_stage_optimal")
        bindings["greedy_comparison"] = opt.get("greedy_comparison")
    mono = summary.get("monotonicity_check")
    if mono is None and isinstance(detail, dict):
        mono = ((detail.get("sensitivity") or {}).get("monotonicity_check")
                or (detail.get("comparison") or {}).get("monotonicity_check"))
    if mono is not None:
        bindings["monotonicity_check"] = mono
    state = outcome.get("scenario") or {}
    bindings.update({
        "task_type": outcome.get("task_type"),
        "result_id": summary.get("result_id") or outcome.get("result_id"),
        "execution_trace_id": (
            summary.get("execution_trace_id")
            or (summary.get("provenance") or {}).get("execution_trace_id")
            or outcome.get("execution_trace_id")
        ),
        "scenario_id": (state.get("scenario_id") or summary.get("scenario_id")),
        # The projected summary is the authoritative document: if the outcome
        # envelope and the summary ever disagree, the summary wins.
        "data_status": summary.get("data_status") or outcome.get("data_status"),
        "solver_status": summary.get("solver_status") or outcome.get("solver_status"),
        "optimality_proven": resolve_optimality(summary),
        "verification_status": (summary.get("verification_status")
                                or outcome.get("verification_status")),
        "scenario_params": summary.get("scenario_params") or (state.get("params") or {}),
        "detail": detail,
        "warnings": summary.get("warnings") or [],
    })
    return bindings
