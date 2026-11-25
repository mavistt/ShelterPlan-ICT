"""Immutable value objects shared by session, guards, executor and MCP tools."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def digest(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


SCENARIO_CHANGE_KEYS = (
    "population_demand_ratio",
    "max_walk_time_min",
    "budget_units",
    "disabled_shelter_ids",
)


@dataclass(frozen=True)
class ScenarioParams:
    """The complete planning parameter snapshot.

    Frozen on purpose: a later turn can never mutate an earlier scenario.
    """

    population_demand_ratio: float
    max_walk_time_min: float
    budget_units: float | None
    disabled_shelter_ids: tuple[str, ...]
    data_version: str
    analysis_mode: str = "demo"
    region_id: str | None = None

    def merged(self, changes: dict[str, Any]) -> "ScenarioParams":
        unknown = set(changes) - set(SCENARIO_CHANGE_KEYS)
        if unknown:
            raise ValueError(f"unsupported scenario change keys: {sorted(unknown)}")
        payload = self.to_dict()
        payload.update(changes)
        # Disabled ids must stay a tuple so the dataclass stays hashable/frozen.
        if payload.get("disabled_shelter_ids") is not None:
            payload["disabled_shelter_ids"] = tuple(payload["disabled_shelter_ids"])
        return ScenarioParams(**payload)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "population_demand_ratio": self.population_demand_ratio,
            "max_walk_time_min": self.max_walk_time_min,
            "budget_units": self.budget_units,
            "disabled_shelter_ids": list(self.disabled_shelter_ids),
            "data_version": self.data_version,
            "analysis_mode": self.analysis_mode,
        }
        if self.region_id is not None:
            payload["region_id"] = self.region_id
        return payload

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())

    def business_fields(self) -> dict[str, Any]:
        """Only the four user-facing planning knobs (used for inheritance checks)."""
        return {
            "population_demand_ratio": self.population_demand_ratio,
            "max_walk_time_min": self.max_walk_time_min,
            "budget_units": self.budget_units,
            "disabled_shelter_ids": list(self.disabled_shelter_ids),
        }


@dataclass(frozen=True)
class ScenarioState:
    scenario_id: str
    parent_scenario_id: str | None
    params: ScenarioParams
    changes: dict[str, Any] = field(default_factory=dict)
    turn: int = 1
    created_at: str = field(default_factory=utc_now)
    result_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "parent_scenario_id": self.parent_scenario_id,
            "turn": self.turn,
            "changes": self.changes,
            "params": self.params.to_dict(),
            "result_id": self.result_id,
            "created_at": self.created_at,
        }

    def to_b_scenario(self, *, include_region_id: bool = False) -> dict[str, Any]:
        """Full scenario object for Module B.

        B rebuilds every scenario from its config defaults, so C must send the
        complete parameter set - never only the delta.
        """
        payload: dict[str, Any] = {
            "scenario_id": self.scenario_id,
            "parent_scenario_id": self.parent_scenario_id,
            "data_version": self.params.data_version,
            "analysis_mode": self.params.analysis_mode,
            "population_demand_ratio": self.params.population_demand_ratio,
            "max_walk_time_min": self.params.max_walk_time_min,
            "budget_units": self.params.budget_units,
            "disabled_shelter_ids": list(self.params.disabled_shelter_ids),
            "changes": dict(self.changes),
        }
        if include_region_id and self.params.region_id:
            payload["region_id"] = self.params.region_id
        return payload

    def with_result(self, result_id: str) -> "ScenarioState":
        return replace(self, result_id=result_id)


@dataclass
class ToolCall:
    tool_name: str
    started_at: str
    finished_at: str
    duration_ms: float
    status: str
    attempt: int = 0
    error_code: str | None = None
    job_id: str | None = None
    result_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "attempt": self.attempt,
            "error_code": self.error_code,
            "job_id": self.job_id,
            "result_id": self.result_id,
            "detail": self.detail,
        }


@dataclass
class TaskOutcome:
    ok: bool
    task_type: str
    task_id: str
    session_id: str
    scenario: ScenarioState | None = None
    job_id: str | None = None
    result_id: str | None = None
    execution_trace_id: str | None = None
    data_version: str | None = None
    data_status: str | None = None
    solver_status: str | None = None
    optimality_proven: bool | None = None
    verification_status: str | None = None
    summary: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None
    attempts: int = 1
    retries: int = 0
    duration_ms: float = 0.0
    tool_calls: list[ToolCall] = field(default_factory=list)
    summary_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "task_type": self.task_type,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "scenario": self.scenario.to_dict() if self.scenario else None,
            "job_id": self.job_id,
            "result_id": self.result_id,
            "execution_trace_id": self.execution_trace_id,
            "data_version": self.data_version,
            "data_status": self.data_status,
            "solver_status": self.solver_status,
            "optimality_proven": self.optimality_proven,
            "verification_status": self.verification_status,
            "summary": self.summary,
            "warnings": self.warnings,
            "error": self.error,
            "attempts": self.attempts,
            "retries": self.retries,
            "duration_ms": round(self.duration_ms, 3),
            "summary_bytes": self.summary_bytes,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
        }
