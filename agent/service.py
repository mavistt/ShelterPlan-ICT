"""Application service shared by the MCP tools and the V0 evaluation runner."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
import threading
from typing import Any
import uuid

from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.errors import HarnessError
from agent.harness.executor import ExecuteRequest, HarnessExecutor
from agent.harness.results import paginate_evidence, project
from agent.harness.session import Session, SessionRegistry
from agent.harness.trace import TraceRecorder

DEFAULT_SESSION_ID = "default"


@dataclass
class SessionRuntime:
    session: Session
    trace: TraceRecorder
    lock: threading.RLock = field(default_factory=threading.RLock)


class HarnessService:
    def __init__(self, config: HarnessConfig | None = None, *, client: BClient | None = None,
                 trace_dir: str | None = None) -> None:
        self.config = config or HarnessConfig()
        self.client = client or BClient(self.config)
        self._trace_dir = trace_dir
        defaults = ScenarioParams(
            population_demand_ratio=self.config.default_population_demand_ratio,
            max_walk_time_min=self.config.default_max_walk_time_min,
            budget_units=None,
            disabled_shelter_ids=(),
            data_version=self.config.data_version,
            analysis_mode="demo",
        )
        self._defaults = defaults
        self._registry = SessionRegistry(defaults)
        self._runtimes: dict[str, SessionRuntime] = {}
        self._result_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._result_cache_max = 16
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def _runtime(self, session_id: str | None) -> SessionRuntime:
        session_id = session_id or DEFAULT_SESSION_ID
        with self._lock:
            runtime = self._runtimes.get(session_id)
            if runtime is None:
                session = self._registry.get_or_create(session_id)
                base = Path(self._trace_dir) if self._trace_dir else Path(self.config.trace_dir)
                path = base / f"{session_id}.jsonl"
                runtime = SessionRuntime(session=session, trace=TraceRecorder(path, session_id))
                self._runtimes[session_id] = runtime
            return runtime

    def configure_session(self, session_id: str | None = None, *,
                          region_id: str | None = None,
                          data_version: str | None = None,
                          valid_shelter_ids: frozenset[str] | None = None) -> dict[str, Any]:
        session_id = session_id or DEFAULT_SESSION_ID
        session_defaults = replace(
            self._defaults,
            data_version=data_version or self._defaults.data_version,
            region_id=region_id if region_id is not None else self._defaults.region_id,
        )
        with self._lock:
            session = self._registry.configure(
                session_id, session_defaults, valid_shelter_ids=valid_shelter_ids)
            base = Path(self._trace_dir) if self._trace_dir else Path(self.config.trace_dir)
            path = base / f"{session_id}.jsonl"
            self._runtimes[session_id] = SessionRuntime(
                session=session, trace=TraceRecorder(path, session_id))
            return session.snapshot()

    def reset_session(self, session_id: str | None = None) -> dict[str, Any]:
        session_id = session_id or DEFAULT_SESSION_ID
        with self._lock:
            existing = self._runtimes.get(session_id)
            session = self._registry.reset(
                session_id,
                defaults=(existing.session.defaults if existing is not None else None),
                valid_shelter_ids=(existing.session.valid_shelter_ids if existing is not None else None),
            )
            base = Path(self._trace_dir) if self._trace_dir else Path(self.config.trace_dir)
            path = base / f"{session_id}.jsonl"
            self._runtimes[session_id] = SessionRuntime(
                session=session, trace=TraceRecorder(path, session_id))
            return session.snapshot()

    def session_state(self, session_id: str | None = None) -> dict[str, Any]:
        return self._runtime(session_id).session.snapshot()

    def session_defaults(self, session_id: str | None = None) -> ScenarioParams:
        return self._runtime(session_id).session.defaults

    def restore_session(self, session_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Replace an in-memory session with a persisted one (restart recovery)."""
        runtime = self._runtime(session_id)
        with runtime.lock:
            runtime.session.restore(snapshot)
        return runtime.session.snapshot()

    def session_history(self, session_id: str | None = None) -> list:
        """Full immutable snapshots, used for restore operations."""
        return list(self._runtime(session_id).session.history)

    @property
    def defaults(self):
        return self._defaults

    # ------------------------------------------------------------------ #
    def capabilities(self) -> dict[str, Any]:
        raw = self.client.get_capabilities()
        skills = raw.get("skills") or []
        composites = raw.get("composites") or []
        return {
            "skill_graph_version": raw.get("skill_graph_version"),
            "task_types": raw.get("task_types") or [],
            "skill_count": len(skills),
            "composite_count": len(composites),
            "composites": [{"composite_id": row.get("composite_id"), "name": row.get("name")}
                           for row in composites],
            "solver": raw.get("solver") or {},
            "data_modes": raw.get("data_modes") or [],
            "data_version": self.config.data_version,
            "data_status": self.config.expected_data_status,
            "admission": {
                "demo_allowed": True,
                "verified_mode_allowed": self.config.expected_data_status == "VERIFIED",
                "note": ("Module A source is DEMO_ONLY. Plans may be produced for demo and "
                         "algorithm evaluation only; they must never be presented as "
                         "field-verified emergency guidance."),
            },
            "tool_contract": {
                "exposed_tools": ["get_capabilities", "submit_task", "get_job", "get_result"],
                "atomic_skills_exposed": False,
                "result_payload": f"projected summary <= {self.config.summary_max_bytes} bytes",
            },
        }

    # ------------------------------------------------------------------ #
    def submit(self, task_type: str, *, session_id: str | None = None,
               changes: dict[str, Any] | None = None,
               parameters: dict[str, Any] | None = None,
               improvement_options: list[dict[str, Any]] | None = None,
               result_id: str | None = None,
               claim: str | None = None) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        executor = HarnessExecutor(self.client, runtime.session, runtime.trace, self.config)
        with runtime.lock:
            outcome = executor.execute(ExecuteRequest(
                task_type=task_type, changes=changes, parameters=parameters,
                improvement_options=improvement_options, result_id=result_id, claim=claim,
            ))
        payload = outcome.to_dict()
        payload["session_id"] = runtime.session.session_id
        return payload

    def job_status(self, job_id: str) -> dict[str, Any]:
        return self.client.get_job(job_id)

    def get_full_result(self, result_id: str) -> dict[str, Any]:
        with self._lock:
            cached = self._result_cache.get(result_id)
            if cached is not None:
                self._result_cache.move_to_end(result_id)
                return cached
        full = self.client.get_result(result_id)
        with self._lock:
            self._result_cache[result_id] = full
            self._result_cache.move_to_end(result_id)
            while len(self._result_cache) > self._result_cache_max:
                self._result_cache.popitem(last=False)
        return full

    def result(self, result_id: str, session_id: str | None = None) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        full = self.get_full_result(result_id)
        task_type = _infer_task_type(full)
        summary = project(full, task_type=task_type, config=self.config)
        runtime.session.remember_result(result_id, scenario_id=full.get("scenario_id", ""),
                                        data_version=full.get("data_version", ""),
                                        summary=summary)
        return summary

    def evidence(self, result_id: str, section: str, offset: int = 0,
                 limit: int | None = None) -> dict[str, Any]:
        full = self.get_full_result(result_id)
        return paginate_evidence(full, section=section, offset=offset,
                                 limit=limit or self.config.evidence_page_default,
                                 config=self.config)

    def last_result_id(self, session_id: str | None = None) -> str | None:
        ids = self._runtime(session_id).session.known_result_ids()
        return ids[-1] if ids else None

    def session_has_result(self, session_id: str | None, result_id: str) -> bool:
        """Membership check: another session's result must never be served."""
        return self._runtime(session_id).session.has_result(result_id)

    def session_object(self, session_id: str | None = None) -> Session:
        return self._runtime(session_id).session

    def close(self) -> None:
        self.client.close()


def _infer_task_type(full: dict[str, Any]) -> str:
    detail = full.get("result_detail") or {}
    if "optimization" in detail:
        return "optimize"
    if "stress" in detail:
        return "stress_test"
    if "comparison" in detail:
        return "compare"
    if "sensitivity" in detail:
        return "sensitivity"
    if "intervention" in detail:
        return "diagnose_intervene"
    if "explanation" in detail:
        return "explain_result"
    return "assess"


def new_session_id(prefix: str = "s") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
