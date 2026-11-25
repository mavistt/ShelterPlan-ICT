"""Deterministic task execution: submit -> poll -> fetch -> guard -> project.

This is the V0 "harness": no model in the loop. It exists so that a later
AgentArts agent has one boring, auditable path to the planner.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any
import uuid

from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.contracts import TaskOutcome, ToolCall, digest, utc_now
from agent.errors import (
    BError,
    BJobMissing,
    BJobStateLost,
    BJobTimeout,
    BResultMissing,
    BTransportError,
    HarnessError,
)
from agent.harness import guards
from agent.harness.results import (
    evaluate_pair_monotonicity,
    json_bytes,
    project,
)
from agent.harness.session import Session
from agent.harness.trace import TraceRecorder

# Parameters that are really scenario knobs. They must land in the scenario
# snapshot, otherwise the next turn silently loses them.
SCENARIO_PARAM_KEYS = ("budget_units", "disabled_shelter_ids")


@dataclass
class ExecuteRequest:
    task_type: str
    changes: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None
    improvement_options: list[dict[str, Any]] | None = None
    result_id: str | None = None
    claim: str | None = None


class HarnessExecutor:
    def __init__(self, client: BClient, session: Session, trace: TraceRecorder,
                 config: HarnessConfig | None = None) -> None:
        self.client = client
        self.session = session
        self.trace = trace
        self.config = config or client.config
        self._task_id = ""

    # ------------------------------------------------------------------ #
    def execute(self, request: ExecuteRequest) -> TaskOutcome:
        started = time.perf_counter()
        task_id = f"c-{uuid.uuid4()}"
        self._task_id = task_id
        tool_calls: list[ToolCall] = []
        outcome = TaskOutcome(ok=False, task_type=request.task_type, task_id=task_id,
                              session_id=self.session.session_id)
        state = None
        try:
            changes, parameters = _normalize_request(request)
            changes = guards.validate_changes(changes)
            state = self.session.plan_turn(changes) if changes else self.session.current

            checked = guards.preflight(
                task_type=request.task_type,
                state=state,
                config=self.config,
                session=self.session,
                parameters=parameters,
                improvement_options=request.improvement_options,
                result_id=request.result_id,
            )
            payload = self._build_payload(task_id, request, state, checked)

            submitted, job = self._submit_and_poll(payload, tool_calls, state)
            result = self._fetch_result(job, tool_calls, state)

            guards.postflight(full_result=result, state=state, config=self.config,
                              claim=request.claim)
            summary = project(result, task_type=request.task_type, config=self.config,
                              scenario_params=state.params.business_fields())
            if (request.task_type == "explain_result" and request.result_id
                    and self.session.has_result(request.result_id)):
                stored_target = self.session.get_result(request.result_id).get("summary") or {}
                explanation = summary.setdefault("detail", {}).setdefault("explanation", {})
                if isinstance(explanation, dict):
                    target_snapshot: dict[str, Any] = {
                        "result_id": stored_target.get("result_id"),
                        "task_type": stored_target.get("task_type"),
                        "solver_status": stored_target.get("solver_status"),
                        "optimality_proven": stored_target.get("optimality_proven"),
                        "verification_status": stored_target.get("verification_status"),
                        "metrics": stored_target.get("metrics"),
                        "detail": stored_target.get("detail"),
                        "evidence": stored_target.get("evidence"),
                        "warnings": stored_target.get("warnings"),
                    }
                    explanation["target_summary"] = target_snapshot
                    if json_bytes(summary) > self.config.summary_max_bytes:
                        target_snapshot.pop("detail", None)
                    if json_bytes(summary) > self.config.summary_max_bytes:
                        target_snapshot.pop("warnings", None)
                        target_snapshot.pop("evidence", None)
                    for _ in range(3):
                        measured = json_bytes(summary)
                        if summary.get("summary_bytes") == measured:
                            break
                        summary["summary_bytes"] = measured
            self._assert_summary_matches(result, summary)

            options_digest = digest(checked.get("improvement_options") or [])
            if (summary.get("monotonicity_check") is None
                    and request.task_type in {"assess", "optimize"}
                    and state.parent_scenario_id):
                parent_state = self.session.find_scenario(state.parent_scenario_id)
                if (parent_state is not None and parent_state.result_id
                        and self.session.has_result(parent_state.result_id)):
                    parent_record = self.session.get_result(parent_state.result_id)
                    parent_summary = parent_record.get("summary")
                    if isinstance(parent_summary, dict):
                        same_opts = (
                            request.task_type == "assess"
                            or options_digest == parent_record.get("options_digest")
                        )
                        mono = evaluate_pair_monotonicity(
                            parent_summary, summary, same_options=same_opts
                        )
                        summary["monotonicity_check"] = mono
                        for _ in range(3):
                            measured = json_bytes(summary)
                            if summary.get("summary_bytes") == measured:
                                break
                            summary["summary_bytes"] = measured
                        if json_bytes(summary) > self.config.summary_max_bytes:
                            summary.pop("monotonicity_check", None)
                            for _ in range(3):
                                measured = json_bytes(summary)
                                if summary.get("summary_bytes") == measured:
                                    break
                                summary["summary_bytes"] = measured

            self.session.remember_result(result["result_id"],
                                         scenario_id=result["scenario_id"],
                                         data_version=result["data_version"],
                                         summary=summary,
                                         options_digest=options_digest)
            self.session.commit(state, result["result_id"])

            outcome.ok = True
            outcome.scenario = state
            outcome.job_id = job.get("job_id") or submitted.get("job_id")
            outcome.result_id = result["result_id"]
            outcome.execution_trace_id = result.get("execution_trace_id")
            outcome.data_version = result.get("data_version")
            outcome.data_status = result.get("data_status")
            outcome.solver_status = result.get("solver_status")
            from agent.harness.results import resolve_optimality
            outcome.optimality_proven = bool(resolve_optimality(result))
            outcome.verification_status = result.get("verification_status")
            outcome.summary = summary
            outcome.warnings = summary.get("warnings") or []
            outcome.summary_bytes = json_bytes(summary)
        except HarnessError as exc:
            outcome.error = exc.to_dict()
        except Exception as exc:  # noqa: BLE001 - converted to a stable envelope
            outcome.error = {
                "code": "INTERNAL_ERROR",
                "message": f"{exc.__class__.__name__}: {exc}",
                "details": {},
                "retryable": False,
                "user_action": None,
            }
        finally:
            outcome.duration_ms = (time.perf_counter() - started) * 1000
            outcome.tool_calls = tool_calls
            outcome.retries = max((call.attempt for call in tool_calls), default=1) - 1
            self._write_task_trace(outcome, state)
            self._task_id = ""
        return outcome

    # ------------------------------------------------------------------ #
    def _build_payload(self, task_id: str, request: ExecuteRequest, state, checked) -> dict[str, Any]:
        # Module B only accepts a parent id that already exists in its own
        # scenario ledger. A scenario C planned but never executed is unknown to
        # B, so lineage is declared only once the parent has a stored result.
        parent = self._resolvable_parent(state)
        scenario = state.to_b_scenario(include_region_id=self.config.send_region_id_to_b)
        scenario["parent_scenario_id"] = parent
        payload: dict[str, Any] = {
            "task_id": task_id,
            "task_type": request.task_type,
            "analysis_mode": state.params.analysis_mode,
            "scenario": scenario,
            # B regenerates a content-hash id whenever `changes` is present and
            # the request-level scenario_id is empty. Send ours explicitly so the
            # conversation, the result and the trace share one identifier.
            "scenario_id": state.scenario_id,
            "parent_scenario_id": parent,
            "changes": dict(state.changes),
            # One logical request == one idempotency key. Retries reuse it.
            "idempotency_key": f"{self.session.session_id}:{state.turn}:{task_id}",
            "parameters": dict(checked.get("parameters") or {}),
        }
        if self.config.send_region_id_to_b and state.params.region_id:
            payload["region_id"] = state.params.region_id
        options = checked.get("improvement_options") or []
        if options:
            payload["improvement_options"] = options
        if request.task_type == "explain_result":
            payload["result_id"] = checked.get("result_id")
        return payload

    def _resolvable_parent(self, state) -> str | None:
        parent_id = getattr(state, "parent_scenario_id", None)
        if not parent_id:
            return None
        parent_state = self.session.find_scenario(parent_id)
        if parent_state is None or not parent_state.result_id:
            return None
        return parent_id

    def _submit_and_poll(self, payload: dict[str, Any], tool_calls: list[ToolCall], state
                         ) -> tuple[dict[str, Any], dict[str, Any]]:
        attempts = 0
        submitted: dict[str, Any] | None = None
        while True:
            attempts += 1
            if submitted is None:
                tick, started_at = time.perf_counter(), utc_now()
                try:
                    submitted = self.client.submit(payload)
                except BTransportError as exc:
                    tool_calls.append(self._call("submit_task", started_at, tick, "error",
                                                 attempt=attempts, error_code=exc.code))
                    if attempts > self.config.max_job_retries:
                        raise
                    time.sleep(self.config.retry_backoff_s * attempts)
                    continue
                except HarnessError as exc:
                    tool_calls.append(self._call("submit_task", started_at, tick, "error",
                                                 attempt=attempts, error_code=exc.code))
                    raise
                tool_calls.append(self._call("submit_task", started_at, tick, "ok",
                                             attempt=attempts,
                                             job_id=submitted.get("job_id"), state=state))

            job_id = submitted["job_id"]
            poll_tick, poll_started = time.perf_counter(), utc_now()
            try:
                job = self.client.wait_for_job(job_id, deadline_s=self.config.job_timeout_s)
            except BJobMissing as exc:
                tool_calls.append(self._call("get_job", poll_started, poll_tick, "error",
                                             attempt=attempts, error_code="JOB_STATE_LOST"))
                raise BJobStateLost(
                    "Module B no longer knows this job; its in-memory job table was reset.",
                    details={"job_id": job_id, "cause": exc.details},
                ) from exc
            except BJobTimeout as exc:
                tool_calls.append(self._call("get_job", poll_started, poll_tick, "error",
                                             attempt=attempts, error_code=exc.code))
                if attempts > self.config.max_job_retries:
                    raise
                # Re-poll the same job: no new computation, no new key.
                time.sleep(self.config.retry_backoff_s)
                continue
            except BError as exc:
                tool_calls.append(self._call("get_job", poll_started, poll_tick, "error",
                                             attempt=attempts, error_code=exc.code))
                raise
            tool_calls.append(self._call("get_job", poll_started, poll_tick, "ok",
                                         attempt=attempts, job_id=job_id,
                                         result_id=job.get("result_id"), state=state))
            return submitted, job

    def _fetch_result(self, job: dict[str, Any], tool_calls: list[ToolCall], state) -> dict[str, Any]:
        result_id = job.get("result_id")
        tick, started_at = time.perf_counter(), utc_now()
        if not result_id:
            tool_calls.append(self._call("get_result", started_at, tick, "error",
                                         error_code="RESULT_NOT_FOUND"))
            raise BResultMissing("Job succeeded without a result_id.",
                                 details={"job_id": job.get("job_id")})
        try:
            result = self.client.get_result(result_id)
        except HarnessError as exc:
            tool_calls.append(self._call("get_result", started_at, tick, "error",
                                         result_id=result_id, error_code=exc.code))
            raise
        tool_calls.append(self._call("get_result", started_at, tick, "ok",
                                     result_id=result_id, state=state))
        return result

    # ------------------------------------------------------------------ #
    @staticmethod
    def _assert_summary_matches(full: dict[str, Any], summary: dict[str, Any]) -> None:
        source = full.get("result_summary") or {}
        for key in ("served_demand", "total_demand", "weighted_walk_time_min"):
            left, right = source.get(key), (summary.get("metrics") or {}).get(key)
            if left is None and right is None:
                continue
            try:
                if abs(float(left) - float(right)) > max(1e-6, abs(float(left)) * 1e-9):
                    raise HarnessError(
                        "Projected summary diverges from the service result.",
                        details={"field": key, "service": left, "summary": right},
                    )
            except (TypeError, ValueError) as exc:
                raise HarnessError("Projected summary contains a non-numeric metric.",
                                   details={"field": key, "service": left, "summary": right}) from exc

    def _call(self, tool: str, started_at: str, tick: float, status: str, *,
              attempt: int = 1, error_code: str | None = None,
              job_id: str | None = None, result_id: str | None = None,
              state=None) -> ToolCall:
        finished = utc_now()
        duration = (time.perf_counter() - tick) * 1000
        call = ToolCall(tool_name=tool, started_at=started_at, finished_at=finished,
                        duration_ms=duration, status=status, attempt=attempt,
                        error_code=error_code, job_id=job_id, result_id=result_id)
        self.trace.tool_call(
            task_id=self._task_id, tool_name=tool, started_at=started_at,
            finished_at=finished, duration_ms=duration, status=status,
            attempt=attempt, attempts=attempt, retries=max(0, attempt - 1),
            scenario_id=getattr(state, "scenario_id", None) if state else None,
            parent_scenario_id=getattr(state, "parent_scenario_id", None) if state else None,
            data_version=(getattr(getattr(state, "params", None), "data_version", None)
                          if state else None),
            args={"tool": tool}, job_id=job_id, result_id=result_id,
            error_type=error_code,
        )
        return call

    def _write_task_trace(self, outcome: TaskOutcome, state) -> None:
        self.trace.task(task_id=outcome.task_id, payload={
            "ok": outcome.ok,
            "task_type": outcome.task_type,
            "turn": getattr(state, "turn", None),
            "scenario_id": getattr(state, "scenario_id", None),
            "parent_scenario_id": getattr(state, "parent_scenario_id", None),
            "data_version": outcome.data_version or (
                getattr(getattr(state, "params", None), "data_version", None) if state else None),
            "job_id": outcome.job_id,
            "result_id": outcome.result_id,
            "execution_trace_id": outcome.execution_trace_id,
            "status": "ok" if outcome.ok else "error",
            "error_type": (outcome.error or {}).get("code"),
            "retries": outcome.retries,
            "duration_ms": round(outcome.duration_ms, 3),
            "summary_bytes": outcome.summary_bytes,
            "tool_call_count": len(outcome.tool_calls),
        })


def _normalize_request(request: ExecuteRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    """Promote scenario knobs out of `parameters` and into the scenario delta."""
    changes = dict(request.changes or {})
    parameters = dict(request.parameters or {})
    for key in SCENARIO_PARAM_KEYS:
        if key in parameters and key not in changes:
            changes[key] = parameters.pop(key)
    if request.task_type == "stress_test" and "disabled_shelter_ids" not in changes:
        disabled = parameters.pop("disabled_shelter_ids", None)
        if disabled:
            changes["disabled_shelter_ids"] = disabled
    # `budget_units` for optimize belongs to the scenario, not to parameters.
    if request.task_type in {"optimize", "diagnose_intervene"}:
        parameters.pop("budget_units", None)
    return changes, parameters
