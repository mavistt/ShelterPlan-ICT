"""Module C application API for the Module D web client.

Design rules
------------
* One computation path. Every turn goes through `StructuredRunner` (arm D:
  structured intent -> deterministic routing -> `TaskController` -> Module B).
  This module contains no planning maths and no second state machine.
* Five statuses only: NEEDS_CLARIFICATION / RUNNING / SUCCEEDED / FAILED /
  CANCELLED. A caller never has to parse prose to know what happened.
* POST returns 202 immediately and the turn runs in a worker. The client polls
  `GET /v1/turns/{turn_id}` instead of holding a request open for the model plus
  Module B (measured p50 was ~11 s, p95 ~21 s for a planning turn).
* Every envelope carries the identifiers the UI needs, the structured metrics,
  the data-status disclaimer, and a user-readable sentence.
* State is in memory plus an optional on-disk store; see `agent/state_store.py`
  for exactly what survives a restart and what does not.

Authentication is deliberately out of scope for a local demo: bind to
127.0.0.1. Deployment (HTTPS, bearer tokens, multi-worker) is an AgentArts
concern and is marked unverified in docs/module_c/V21_AGENTARTS_MAPPING.md.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time
from typing import Any
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.errors import BJobTimeout, BResultMissing, BTransportError, HarnessError
from agent.llm_adapter import LLMAdapter, LLMConfig
from agent.regions import RegionCatalog, RegionInfo
from agent.service import HarnessService
from agent.state_store import SessionStore
from agent.structured_runner import StructuredRunner, TurnRecord
from agent.workflow.dialogue import DialogueState

STATUS_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"

DEMO_NOTE = "DEMO_ONLY：基于演示数据，未经真实现场核验，仅供演示与算法评估。"
LAYER_VARIANTS = {"auto", "baseline", "optimized", "fixed", "adaptive"}


class RegionUnavailableError(ValueError):
    """Raised when a region exists in config but its OD matrix is unavailable."""


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class TurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    client_turn_id: str | None = Field(default=None, max_length=80,
                                       description="optional idempotency key scoped to the session")


class SessionRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=80)
    region_id: str | None = Field(default=None, max_length=64)


# --------------------------------------------------------------------------- #
class SessionHandle:
    def __init__(self, session_id: str, runner: StructuredRunner,
                 dialogue: DialogueState) -> None:
        self.session_id = session_id
        self.runner = runner
        self.dialogue = dialogue
        self.lock = threading.RLock()
        self.envelope_lock = threading.RLock()
        self.turns: dict[str, dict[str, Any]] = {}
        self.client_turns: dict[str, str] = {}
        self.created_at = _now()


class ConversationService:
    """Owns sessions, the worker pool and the envelope contract."""

    def __init__(self, config: HarnessConfig | None = None, *,
                 service: HarnessService | None = None,
                 llm: Any | None = None,
                 store: SessionStore | None = None,
                 store_dir: str | None = None,
                 regions: RegionCatalog | None = None,
                 max_workers: int = 2,
                 dialogue_enabled: bool = True,
                 arm: str = "harness_opt") -> None:
        self.config = config or HarnessConfig.from_env()
        self.service = service or HarnessService(self.config,
                                                 client=BClient(self.config),
                                                 trace_dir=str(self.config.trace_dir))
        self._own_service = service is None
        self.llm = llm or LLMAdapter(LLMConfig.from_env())
        self._own_llm = llm is None
        self.store = store or SessionStore(
            store_dir or (self.config.trace_dir.parent / "api_state"))
        self.regions = regions or RegionCatalog(self.config.project_root, config=self.config)
        self.dialogue_enabled = dialogue_enabled
        self.arm = arm
        self._handles: dict[str, SessionHandle] = {}
        self._by_turn: dict[str, str] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="module-c-api")
        self.interrupted = self.store.mark_interrupted()

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #
    def create_session(self, session_id: str | None = None, *,
                       region_id: str | None = None) -> str:
        session_id = session_id or f"s-{uuid.uuid4().hex[:12]}"
        region_info: RegionInfo | None = None
        if region_id is not None:
            region_info = self.regions.get_region(region_id)
            if not region_info.od_available:
                raise RegionUnavailableError(
                    f"DATA_UNAVAILABLE: region '{region_id}' has no travel matrix yet "
                    f"(release_status={region_info.release_status})."
                )
        with self._lock:
            if session_id in self._handles:
                raise ValueError("session already exists")
            persisted = self.store.load_session(session_id)
            if persisted and region_id is not None and persisted.get("region_id"):
                if persisted["region_id"] != region_id:
                    raise ValueError(
                        f"session '{session_id}' is already bound to region '{persisted['region_id']}'"
                    )
            handle = self._build_handle(session_id, restore=True, region_info=region_info)
            self._handles[session_id] = handle
            if persisted is None and region_info is not None:
                self._persist(handle)
        return session_id

    def _build_handle(self, session_id: str, *, restore: bool,
                      region_info: RegionInfo | None = None) -> SessionHandle:
        dialogue = DialogueState()
        summaries: list[dict[str, Any]] = []
        memory_data: dict[str, Any] = {}
        client_turns: dict[str, str] = {}
        persisted = self.store.load_session(session_id) if restore else None
        bound_region_id = (persisted or {}).get("region_id") or (
            region_info.region_id if region_info is not None else None
        )
        if region_info is None and bound_region_id:
            try:
                region_info = self.regions.get_region(bound_region_id)
            except KeyError:
                region_info = None
        if region_info is not None:
            valid_shelters = (
                self.regions.get_shelter_ids(region_info.region_id)
                if region_info.source_mode == "module_a_v2"
                else None
            )
            self.service.configure_session(
                session_id,
                region_id=region_info.region_id,
                data_version=region_info.data_version,
                valid_shelter_ids=valid_shelters,
            )
        if persisted:
            snapshot = persisted.get("session") or {}
            if snapshot.get("lineage"):
                self.service.restore_session(session_id, snapshot)
            dialogue = DialogueState.from_dict(persisted.get("dialogue"))
            summaries = list(persisted.get("summaries") or [])
            memory_data = dict(persisted.get("memory") or {})
            client_turns = dict(persisted.get("client_turns") or {})
        runner = StructuredRunner(self.arm, self.service, self.llm, self.config,
                                  session_id=session_id,
                                  dialogue=dialogue,
                                  dialogue_enabled=self.dialogue_enabled)
        runner._summaries = summaries  # noqa: SLF001 - same package boundary
        if memory_data:
            runner._memory.last_task_type = memory_data.get("last_task_type")  # noqa: SLF001
            runner._memory.last_options = list(memory_data.get("last_options") or [])  # noqa: SLF001
            runner._memory.last_result_id = memory_data.get("last_result_id")  # noqa: SLF001
            runner._memory.last_changes = dict(memory_data.get("last_changes") or {})  # noqa: SLF001
            runner._memory.actions = list(memory_data.get("actions") or [])  # noqa: SLF001
        handle = SessionHandle(session_id, runner, dialogue)
        handle.client_turns = client_turns
        if persisted:
            for envelope in self.store.load_turns(session_id):
                handle.turns[envelope["turn_id"]] = envelope
                self._by_turn[envelope["turn_id"]] = session_id
        return handle

    def handle(self, session_id: str) -> SessionHandle:
        with self._lock:
            handle = self._handles.get(session_id)
            if handle is None:
                persisted = self.store.load_session(session_id)
                if persisted is None:
                    raise KeyError(session_id)
                handle = self._build_handle(session_id, restore=True)
                self._handles[session_id] = handle
            return handle

    def reset_session(self, session_id: str) -> dict[str, Any]:
        handle = self.handle(session_id)
        with self._lock, handle.lock, handle.envelope_lock:
            old_turn_ids = list(handle.turns.keys())
            for tid in old_turn_ids:
                if self._by_turn.get(tid) == session_id:
                    self._by_turn.pop(tid, None)
            self.store.delete_session_turns(session_id, turn_ids=old_turn_ids)
            self.service.reset_session(session_id)
            handle.runner = StructuredRunner(
                self.arm, self.service, self.llm, self.config, session_id=session_id,
                dialogue=DialogueState(), dialogue_enabled=self.dialogue_enabled)
            handle.dialogue = handle.runner.dialogue
            handle.turns.clear()
            handle.client_turns.clear()
            self._persist(handle)
        return self.session_view(session_id)

    def session_view(self, session_id: str) -> dict[str, Any]:
        handle = self.handle(session_id)
        with handle.envelope_lock:
            state = self.service.session_state(session_id)
            defaults = self.service.session_defaults(session_id)
            return {
                "session_id": session_id,
                "created_at": handle.created_at,
                "turn": state.get("turn"),
                "region_id": defaults.region_id,
                "params": (state.get("current") or {}).get("params"),
                "scenario_id": (state.get("current") or {}).get("scenario_id"),
                "lineage": state.get("lineage"),
                "result_ids": state.get("result_ids"),
                "pending_clarification": (handle.dialogue.to_dict().get("pending")),
                "turns": [{"turn_id": row["turn_id"], "status": row["status"],
                           "created_at": row.get("created_at")}
                          for row in handle.turns.values()],
                "data_version": defaults.data_version,
                "data_status": self.config.expected_data_status,
            }

    # ------------------------------------------------------------------ #
    # Turns
    # ------------------------------------------------------------------ #
    def submit_turn(self, session_id: str, text: str,
                    client_turn_id: str | None = None) -> dict[str, Any]:
        handle = self.handle(session_id)
        with handle.envelope_lock:
            if client_turn_id and client_turn_id in handle.client_turns:
                return deepcopy(handle.turns[handle.client_turns[client_turn_id]])
            defaults = self.service.session_defaults(session_id)
            turn_id = f"t-{uuid.uuid4().hex[:12]}"
            envelope = {
                "turn_id": turn_id,
                "session_id": session_id,
                "status": STATUS_RUNNING,
                "user_text": text,
                "created_at": _now(),
                "updated_at": _now(),
                "scenario_id": self.service.session_state(session_id)
                .get("current", {}).get("scenario_id"),
                "region_id": defaults.region_id,
                "task_id": None,
                "job_id": None,
                "result_id": None,
                "data_version": defaults.data_version,
                "data_status": self.config.expected_data_status,
                "metrics": {},
                "map": None,
                "warnings": [],
                "message": "正在处理请求。",
                "clarification": None,
                "error": None,
                "timings": {"queued_at": _now(), "queue_ms": None, "run_ms": None},
                "llm": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
                "cancel_requested": False,
                "state_persisted": self.store is not None,
            }
            handle.turns[turn_id] = envelope
            if client_turn_id:
                handle.client_turns[client_turn_id] = turn_id
            self._persist(handle)
            accepted = deepcopy(envelope)
        with self._lock:
            self._by_turn[turn_id] = session_id
        # Snapshot before the worker starts: a fast turn (a clarification needs
        # no Module B call) would otherwise mutate the dict we are about to
        # return and the 202 body would already be terminal.
        self._pool.submit(self._run_turn, session_id, turn_id, text)
        return accepted

    def _run_turn(self, session_id: str, turn_id: str, text: str) -> None:
        handle = self.handle(session_id)
        with handle.lock:
            with handle.envelope_lock:
                envelope = handle.turns[turn_id]
                if envelope.get("cancel_requested") or envelope.get("status") == STATUS_CANCELLED:
                    if envelope.get("status") != STATUS_CANCELLED:
                        self._finish(handle, turn_id, _cancelled_envelope(envelope))
                    return
                started = time.perf_counter()
                envelope["timings"]["started_at"] = _now()
            try:
                record = handle.runner.run_turn(text)
                run = handle.runner.last_run
                projected = self._project(handle, record, run)
                projected["status"] = record.status
            except Exception as exc:  # noqa: BLE001 - the API must always answer
                projected = {
                    "status": STATUS_FAILED,
                    "error": {"code": "INTERNAL_ERROR",
                              "message": f"{exc.__class__.__name__}: {exc}"[:300],
                              "recoverable": False},
                    "message": "服务内部错误，本次请求没有产生可用结果。",
                }
            with handle.envelope_lock:
                envelope.update(projected)
                envelope["timings"]["run_ms"] = round((time.perf_counter() - started) * 1000, 1)
                self._finish(handle, turn_id, envelope)

    def _finish(self, handle: SessionHandle, turn_id: str,
                envelope: dict[str, Any]) -> None:
        with handle.envelope_lock:
            envelope["updated_at"] = _now()
            self.store.save_turn(envelope)
            self._persist(handle)
        return None

    def _project(self, handle: SessionHandle, record: TurnRecord, run: Any) -> dict[str, Any]:
        bindings = dict(record.bindings or {})
        metrics = {key: value for key, value in bindings.items()
                   if isinstance(value, (int, float)) and not isinstance(value, bool)}
        result_id = bindings.get("result_id")
        warnings = list(bindings.get("warnings") or [])
        if self.config.expected_data_status == "DEMO_ONLY":
            warnings.append(DEMO_NOTE)
        envelope: dict[str, Any] = {
            "scenario_id": (bindings.get("scenario_id")
                            or self.service.session_state(handle.session_id)
                            .get("current", {}).get("scenario_id")),
            "task_id": (record.control.outcome or {}).get("task_id") if record.control else None,
            "job_id": record.job_id,
            "result_id": result_id,
            "metrics": metrics,
            "map": _map_block(result_id),
            "warnings": _dedupe(warnings),
            "message": record.text,
            "clarification": None,
            "error": None,
            "intent": (record.intent or {}).get("intent"),
            "task_type": (record.control.plan.task_type if record.control else None),
            "llm": {
                "calls": len(getattr(run, "llm_calls", []) or []),
                "prompt_tokens": int(getattr(run, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(run, "completion_tokens", 0) or 0),
            },
        }
        if record.status == STATUS_NEEDS_CLARIFICATION:
            envelope["clarification"] = {
                "fields": list(record.clarification_fields),
                "question": (record.plan.question if record.plan else None),
                "pending_task_type": (record.plan.task_type if record.plan else None),
                "resume_hint": "下一轮只需补充这些字段，任务会自动续跑。",
            }
        if record.status == STATUS_FAILED and record.control is not None:
            envelope["error"] = {
                "code": record.control.error_code or "UNKNOWN",
                "message": (record.control.error or {}).get("message", "")[:300],
                "class": record.control.error_class,
                "recoverable": record.control.error_class == "upstream_transient",
            }
        if record.resumed:
            envelope["resumed_from_clarification"] = True
        return envelope

    def get_turn(self, turn_id: str) -> dict[str, Any]:
        with self._lock:
            session_id = self._by_turn.get(turn_id)
        if session_id is None:
            persisted = self.store.load_turn(turn_id)
            if persisted is None:
                raise KeyError(turn_id)
            return persisted
        handle = self.handle(session_id)
        with handle.envelope_lock:
            return deepcopy(handle.turns[turn_id])

    def cancel_turn(self, turn_id: str) -> dict[str, Any]:
        with self._lock:
            session_id = self._by_turn.get(turn_id)
        if session_id is None:
            persisted = self.store.load_turn(turn_id)
            if persisted is None:
                raise KeyError(turn_id)
            raise ValueError(f"turn is already {persisted.get('status')}")
        handle = self.handle(session_id)
        with handle.envelope_lock:
            envelope = handle.turns[turn_id]
            if envelope["status"] != STATUS_RUNNING:
                raise ValueError(f"turn is already {envelope['status']}")
            envelope["cancel_requested"] = True
            # A running Module B job cannot be aborted; only a queued turn can.
            if envelope["timings"].get("started_at"):
                return {**deepcopy(envelope), "cancel_accepted": False,
                        "note": "任务已开始执行，Module B 的求解不可中断；请等待其完成。"}
            self._finish(handle, turn_id, _cancelled_envelope(envelope))
            return {**deepcopy(envelope), "cancel_accepted": True}

    # ------------------------------------------------------------------ #
    def result_layers(self, session_id: str, result_id: str, *,
                      variant: str = "auto") -> dict[str, Any]:
        if variant not in LAYER_VARIANTS:
            raise ValueError(f"unsupported layer variant '{variant}'; allowed: {sorted(LAYER_VARIANTS)}")
        self.handle(session_id)
        # Membership check: a result id from another session must not leak.
        if not self.service.session_has_result(session_id, result_id):
            raise PermissionError("result_id was not produced by this session")
        full = self.service.get_full_result(result_id)
        detail = full.get("result_detail") or {}
        baseline_assignment = detail.get("assignment") or {}
        opt_block = detail.get("optimization") if isinstance(detail.get("optimization"), dict) else None
        stress_block = detail.get("stress") if isinstance(detail.get("stress"), dict) else None
        fixed_assignment = ((stress_block or {}).get("fixed_plan_stress") or {}).get("assignment")
        adaptive_assignment = ((stress_block or {}).get("adaptive_replan") or {}).get("assignment")

        if variant == "baseline":
            primary_assignment = baseline_assignment
            primary_variant = "baseline"
        elif variant == "optimized":
            if not isinstance((opt_block or {}).get("assignment"), dict):
                raise ValueError("variant='optimized' is only available for optimization results")
            primary_assignment = opt_block["assignment"]
            primary_variant = "optimized"
        elif variant == "fixed":
            if not isinstance(fixed_assignment, dict):
                raise ValueError("variant='fixed' is only available for stress_test results")
            primary_assignment = fixed_assignment
            primary_variant = "fixed_plan_stress"
        elif variant == "adaptive":
            if not isinstance(adaptive_assignment, dict):
                raise ValueError("variant='adaptive' is only available when adaptive_replan exists")
            primary_assignment = adaptive_assignment
            primary_variant = "adaptive_replan"
        else:
            if isinstance((opt_block or {}).get("assignment"), dict):
                primary_assignment = opt_block["assignment"]
                primary_variant = "optimized"
            elif isinstance(fixed_assignment, dict):
                primary_assignment = fixed_assignment
                primary_variant = "fixed_plan_stress"
            else:
                primary_assignment = baseline_assignment
                primary_variant = "baseline"

        layers: list[dict[str, Any]] = [{
            "layer_id": "shelter_utilization",
            "variant": primary_variant,
            "kind": "table",
            "geometry": None,
            "rows": _assignment_rows(primary_assignment),
            "note": ("Module B 的结果不含几何；本层以 shelter_id 为键，"
                     "Module D 可用自己的底图关联。"),
        }]
        if primary_variant != "baseline" and baseline_assignment:
            layers.append({
                "layer_id": "shelter_utilization_baseline",
                "variant": "baseline",
                "kind": "table",
                "geometry": None,
                "rows": _assignment_rows(baseline_assignment),
            })
            layers.append({
                "layer_id": "shelter_utilization_delta",
                "variant": f"{primary_variant}_vs_baseline",
                "kind": "table",
                "geometry": None,
                "rows": _assignment_delta_rows(baseline_assignment, primary_assignment),
            })
        if isinstance(adaptive_assignment, dict) and primary_variant != "adaptive_replan":
            layers.append({
                "layer_id": "shelter_utilization_adaptive",
                "variant": "adaptive_replan",
                "kind": "table",
                "geometry": None,
                "rows": _assignment_rows(adaptive_assignment),
            })
        if isinstance(fixed_assignment, dict) and primary_variant == "adaptive_replan":
            layers.append({
                "layer_id": "shelter_utilization_fixed",
                "variant": "fixed_plan_stress",
                "kind": "table",
                "geometry": None,
                "rows": _assignment_rows(fixed_assignment),
            })

        deficits = primary_assignment.get("deficits") or []
        payload: dict[str, Any] = {
            "result_id": result_id,
            "variant": primary_variant,
            "layers": layers,
            "deficits": {"count": len(deficits), "sample": deficits[:20]},
            "data_version": full.get("data_version"),
            "data_status": full.get("data_status"),
        }
        if opt_block is not None:
            payload["selected_option_ids"] = list(opt_block.get("selected_option_ids") or [])
        return payload

    def evidence(self, session_id: str, result_id: str, section: str,
                 offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        self.handle(session_id)
        if not self.service.session_has_result(session_id, result_id):
            raise PermissionError("result_id was not produced by this session")
        return self.service.evidence(result_id, section=section, offset=offset, limit=limit)

    # ------------------------------------------------------------------ #
    def _persist(self, handle: SessionHandle) -> None:
        memory = {
            "last_task_type": handle.runner._memory.last_task_type,  # noqa: SLF001
            "last_options": list(handle.runner._memory.last_options),  # noqa: SLF001
            "last_result_id": handle.runner._memory.last_result_id,  # noqa: SLF001
            "last_changes": dict(handle.runner._memory.last_changes),  # noqa: SLF001
            "actions": list(handle.runner._memory.actions[-20:]),  # noqa: SLF001
        }
        defaults = self.service.session_defaults(handle.session_id)
        self.store.save_session(
            handle.session_id,
            session=self.service.session_object(handle.session_id),
            dialogue=handle.dialogue.to_dict(),
            memory=memory,
            summaries=list(handle.runner._summaries),  # noqa: SLF001
            client_turns=dict(handle.client_turns),
            turn_ids=list(handle.turns.keys()),
            region_id=defaults.region_id,
        )

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        if self._own_llm:
            self.llm.close()
        if self._own_service:
            self.service.close()


# --------------------------------------------------------------------------- #
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _map_block(result_id: str | None) -> dict[str, Any] | None:
    if not result_id:
        return None
    return {
        "available": False,
        "reason": "Module B 的结果不含几何图层；可按 shelter_id 关联自备底图。",
        "layers_href": f"/v1/results/{result_id}/layers",
    }


def _cancelled_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    envelope["status"] = STATUS_CANCELLED
    envelope["message"] = "已取消本次请求，未执行规划任务，也没有产生新结果。"
    envelope["updated_at"] = _now()
    return envelope


def _assignment_rows(assignment: dict[str, Any]) -> list[dict[str, Any]]:
    loads = assignment.get("shelter_loads") or {}
    util = assignment.get("shelter_utilization") or {}
    return [
        {
            "shelter_id": shelter_id,
            "load_persons": loads.get(shelter_id),
            "utilization": util.get(shelter_id),
        }
        for shelter_id in sorted(loads)
    ]


def _assignment_delta_rows(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_loads = before.get("shelter_loads") or {}
    after_loads = after.get("shelter_loads") or {}
    before_util = before.get("shelter_utilization") or {}
    after_util = after.get("shelter_utilization") or {}
    rows: list[dict[str, Any]] = []
    for shelter_id in sorted(set(before_loads) | set(after_loads)):
        b_load = before_loads.get(shelter_id)
        a_load = after_loads.get(shelter_id)
        delta = (
            round(float(a_load) - float(b_load), 6)
            if isinstance(a_load, (int, float)) and not isinstance(a_load, bool)
            and isinstance(b_load, (int, float)) and not isinstance(b_load, bool)
            else None
        )
        rows.append({
            "shelter_id": shelter_id,
            "baseline_load_persons": b_load,
            "target_load_persons": a_load,
            "delta_load_persons": delta,
            "baseline_utilization": before_util.get(shelter_id),
            "target_utilization": after_util.get(shelter_id),
        })
    return rows


def _translate_result_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="unknown session")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=f"result not available to this session: {exc}")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, BResultMissing):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, (BTransportError, BJobTimeout)):
        return HTTPException(status_code=503, detail=exc.message)
    if isinstance(exc, HarnessError):
        return HTTPException(status_code=502, detail=exc.message)
    return HTTPException(status_code=500, detail=f"{exc.__class__.__name__}: {exc}")


# --------------------------------------------------------------------------- #
def create_app(*, conversation: ConversationService | None = None,
               config: HarnessConfig | None = None,
               service: HarnessService | None = None,
               llm: Any | None = None,
               store_dir: str | None = None,
               regions: RegionCatalog | None = None,
               dialogue_enabled: bool = True) -> FastAPI:
    config = config or HarnessConfig.from_env()
    conversation = conversation or ConversationService(
        config, service=service, llm=llm, store_dir=store_dir,
        regions=regions, dialogue_enabled=dialogue_enabled)

    app = FastAPI(title="ShelterPlan Module C application API", version="2.1.0",
                  description="Stable entry point for the Module D web client.")
    app.state.conversation = conversation
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # local demo; tighten at deployment
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "shelterplan-module-c",
            "arm": conversation.arm,
            "dialogue_enabled": conversation.dialogue_enabled,
            "region_mode": conversation.regions.mode,
            "data_version": config.data_version,
            "data_status": config.expected_data_status,
            "sessions": len(conversation._handles),  # noqa: SLF001
            "interrupted_turns_on_start": conversation.interrupted,
            "state_dir": str(conversation.store.root),
        }

    @app.get("/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "statuses": [STATUS_NEEDS_CLARIFICATION, STATUS_RUNNING, STATUS_SUCCEEDED,
                         STATUS_FAILED, STATUS_CANCELLED],
            "task_types": conversation.service.capabilities().get("task_types"),
            "data_version": config.data_version,
            "data_status": config.expected_data_status,
            "region_mode": conversation.regions.mode,
            "regions": conversation.regions.list_regions(),
            "limits": {
                "turn_timeout_s": config.job_timeout_s,
                "poll_interval_hint_ms": 500,
                "max_text_chars": 4000,
            },
            "disclosure": DEMO_NOTE,
        }

    @app.get("/v1/regions")
    @app.get("/api/regions")
    def list_regions() -> dict[str, Any]:
        return {
            "source_mode": conversation.regions.mode,
            "regions": conversation.regions.list_regions(),
            "cross_region_scenarios": conversation.regions.list_cross_region_scenarios(),
        }

    @app.get("/v1/regions/{region_id}/layers")
    @app.get("/api/regions/{region_id}/layers")
    def region_layers(region_id: str) -> dict[str, Any]:
        try:
            return conversation.regions.get_layers(region_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown region: {region_id}") from exc

    @app.get("/v1/regions/{region_id}/scenarios")
    @app.get("/api/regions/{region_id}/scenarios")
    def region_scenarios(region_id: str) -> dict[str, Any]:
        try:
            return conversation.regions.get_scenarios(region_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown region: {region_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/sessions", status_code=201)
    def create_session(body: SessionRequest | None = None) -> dict[str, Any]:
        try:
            session_id = conversation.create_session(
                (body.session_id if body else None),
                region_id=(body.region_id if body else None),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown region: {body.region_id if body else ''}") from exc
        except RegionUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        defaults = conversation.service.session_defaults(session_id)
        return {
            "session_id": session_id,
            "status": "READY",
            "region_id": defaults.region_id,
            "data_version": defaults.data_version,
            "data_status": config.expected_data_status,
        }

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        try:
            return conversation.session_view(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown session") from exc

    @app.post("/v1/sessions/{session_id}/reset")
    def reset_session(session_id: str) -> dict[str, Any]:
        try:
            return conversation.reset_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown session") from exc

    @app.post("/v1/sessions/{session_id}/turns", status_code=202)
    def create_turn(session_id: str, body: TurnRequest) -> dict[str, Any]:
        try:
            return conversation.submit_turn(session_id, body.text, body.client_turn_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown session") from exc

    @app.get("/v1/turns/{turn_id}")
    def get_turn(turn_id: str) -> dict[str, Any]:
        try:
            return conversation.get_turn(turn_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown turn") from exc

    @app.post("/v1/turns/{turn_id}/cancel")
    def cancel_turn(turn_id: str) -> dict[str, Any]:
        try:
            return conversation.cancel_turn(turn_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown turn") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/results/{result_id}/layers")
    def result_layers(result_id: str, session_id: str,
                      variant: str = "auto") -> dict[str, Any]:
        try:
            return conversation.result_layers(session_id, result_id, variant=variant)
        except Exception as exc:  # noqa: BLE001
            raise _translate_result_error(exc) from exc

    @app.get("/v1/results/{result_id}/evidence/{section}")
    def result_evidence(result_id: str, section: str, session_id: str,
                        offset: int = 0, limit: int = 50) -> dict[str, Any]:
        try:
            return conversation.evidence(session_id, result_id, section,
                                         offset=offset, limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise _translate_result_error(exc) from exc

    return app


app = create_app()
