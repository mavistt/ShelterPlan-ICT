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
from agent.llm_adapter import LLMAdapter, LLMConfig
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


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class TurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    client_turn_id: str | None = Field(default=None, max_length=80,
                                       description="optional idempotency key scoped to the session")


class SessionRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=80)


# --------------------------------------------------------------------------- #
class SessionHandle:
    def __init__(self, session_id: str, runner: StructuredRunner,
                 dialogue: DialogueState) -> None:
        self.session_id = session_id
        self.runner = runner
        self.dialogue = dialogue
        self.lock = threading.RLock()
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
    def create_session(self, session_id: str | None = None) -> str:
        session_id = session_id or f"s-{uuid.uuid4().hex[:12]}"
        with self._lock:
            if session_id in self._handles:
                raise ValueError("session already exists")
            self._handles[session_id] = self._build_handle(session_id, restore=True)
        return session_id

    def _build_handle(self, session_id: str, *, restore: bool) -> SessionHandle:
        dialogue = DialogueState()
        summaries: list[dict[str, Any]] = []
        persisted = self.store.load_session(session_id) if restore else None
        if persisted:
            snapshot = persisted.get("session") or {}
            if snapshot.get("lineage"):
                self.service.restore_session(session_id, snapshot)
            dialogue = DialogueState.from_dict(persisted.get("dialogue"))
            summaries = list(persisted.get("summaries") or [])
        runner = StructuredRunner(self.arm, self.service, self.llm, self.config,
                                  session_id=session_id,
                                  dialogue=dialogue,
                                  dialogue_enabled=self.dialogue_enabled)
        runner._summaries = summaries  # noqa: SLF001 - same package boundary
        handle = SessionHandle(session_id, runner, dialogue)
        if persisted:
            for envelope in self.store.load_turns(session_id):
                handle.turns[envelope["turn_id"]] = envelope
                self._by_turn[envelope["turn_id"]] = session_id
        return handle

    def handle(self, session_id: str) -> SessionHandle:
        with self._lock:
            handle = self._handles.get(session_id)
            if handle is None:
                raise KeyError(session_id)
            return handle

    def reset_session(self, session_id: str) -> dict[str, Any]:
        handle = self.handle(session_id)
        with handle.lock:
            self.service.reset_session(session_id)
            handle.runner = StructuredRunner(
                self.arm, self.service, self.llm, self.config, session_id=session_id,
                dialogue=DialogueState(), dialogue_enabled=self.dialogue_enabled)
            handle.dialogue = handle.runner.dialogue
            handle.turns.clear()
        self._persist(handle)
        return self.session_view(session_id)

    def session_view(self, session_id: str) -> dict[str, Any]:
        handle = self.handle(session_id)
        state = self.service.session_state(session_id)
        return {
            "session_id": session_id,
            "created_at": handle.created_at,
            "turn": state.get("turn"),
            "params": (state.get("current") or {}).get("params"),
            "scenario_id": (state.get("current") or {}).get("scenario_id"),
            "lineage": state.get("lineage"),
            "result_ids": state.get("result_ids"),
            "pending_clarification": (handle.dialogue.to_dict().get("pending")),
            "turns": [{"turn_id": row["turn_id"], "status": row["status"],
                       "created_at": row.get("created_at")}
                      for row in handle.turns.values()],
            "data_version": self.config.data_version,
            "data_status": self.config.expected_data_status,
        }

    # ------------------------------------------------------------------ #
    # Turns
    # ------------------------------------------------------------------ #
    def submit_turn(self, session_id: str, text: str,
                    client_turn_id: str | None = None) -> dict[str, Any]:
        handle = self.handle(session_id)
        with handle.lock:
            if client_turn_id and client_turn_id in handle.client_turns:
                return handle.turns[handle.client_turns[client_turn_id]]
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
                "task_id": None,
                "job_id": None,
                "result_id": None,
                "data_version": self.config.data_version,
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
            self._by_turn[turn_id] = session_id
            self._persist(handle)
        # Snapshot before the worker starts: a fast turn (a clarification needs
        # no Module B call) would otherwise mutate the dict we are about to
        # return and the 202 body would already be terminal.
        accepted = deepcopy(envelope)
        self._pool.submit(self._run_turn, session_id, turn_id, text)
        return accepted

    def _run_turn(self, session_id: str, turn_id: str, text: str) -> None:
        handle = self.handle(session_id)
        envelope = handle.turns[turn_id]
        if envelope.get("cancel_requested"):
            self._finish(handle, turn_id, _cancelled_envelope(envelope))
            return
        started = time.perf_counter()
        envelope["timings"]["started_at"] = _now()
        try:
            with handle.lock:
                record = handle.runner.run_turn(text)
            run = handle.runner.last_run
            envelope.update(self._project(handle, record, run))
            envelope["status"] = record.status
        except Exception as exc:  # noqa: BLE001 - the API must always answer
            envelope.update({
                "status": STATUS_FAILED,
                "error": {"code": "INTERNAL_ERROR",
                          "message": f"{exc.__class__.__name__}: {exc}"[:300],
                          "recoverable": False},
                "message": "服务内部错误，本次请求没有产生可用结果。",
            })
        envelope["timings"]["run_ms"] = round((time.perf_counter() - started) * 1000, 1)
        self._finish(handle, turn_id, envelope)

    def _finish(self, handle: SessionHandle, turn_id: str,
                envelope: dict[str, Any]) -> None:
        envelope["updated_at"] = _now()
        with handle.lock:
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
        return dict(self.handle(session_id).turns[turn_id])

    def cancel_turn(self, turn_id: str) -> dict[str, Any]:
        with self._lock:
            session_id = self._by_turn.get(turn_id)
        if session_id is None:
            raise KeyError(turn_id)
        handle = self.handle(session_id)
        envelope = handle.turns[turn_id]
        if envelope["status"] != STATUS_RUNNING:
            raise ValueError(f"turn is already {envelope['status']}")
        envelope["cancel_requested"] = True
        # A running Module B job cannot be aborted; only a queued turn can.
        if envelope["timings"].get("started_at"):
            return {**envelope, "cancel_accepted": False,
                    "note": "任务已开始执行，Module B 的求解不可中断；请等待其完成。"}
        self._finish(handle, turn_id, _cancelled_envelope(envelope))
        return {**envelope, "cancel_accepted": True}

    # ------------------------------------------------------------------ #
    def result_layers(self, session_id: str, result_id: str) -> dict[str, Any]:
        handle = self.handle(session_id)
        # Membership check: a result id from another session must not leak.
        if not self.service.session_has_result(session_id, result_id):
            raise PermissionError("result_id was not produced by this session")
        full = self.service.client.get_result(result_id)
        assignment = (full.get("result_detail") or {}).get("assignment") or {}
        loads = assignment.get("shelter_loads") or {}
        util = assignment.get("shelter_utilization") or {}
        rows = [{"shelter_id": shelter_id,
                 "load_persons": loads.get(shelter_id),
                 "utilization": util.get(shelter_id)}
                for shelter_id in sorted(loads)]
        deficits = assignment.get("deficits") or []
        return {
            "result_id": result_id,
            "layers": [{
                "layer_id": "shelter_utilization",
                "kind": "table",
                "geometry": None,
                "rows": rows,
                "note": ("Module B 的结果不含几何；本层以 shelter_id 为键，"
                         "Module D 可用自己的底图关联。"),
            }],
            "deficits": {"count": len(deficits), "sample": deficits[:20]},
            "data_version": full.get("data_version"),
            "data_status": full.get("data_status"),
        }

    def evidence(self, session_id: str, result_id: str, section: str,
                 offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        if not self.service.session_has_result(session_id, result_id):
            raise PermissionError("result_id was not produced by this session")
        return self.service.evidence(result_id, section=section, offset=offset, limit=limit)

    # ------------------------------------------------------------------ #
    def _persist(self, handle: SessionHandle) -> None:
        memory = {
            "last_task_type": handle.runner._memory.last_task_type,  # noqa: SLF001
            "last_options": handle.runner._memory.last_options,  # noqa: SLF001
            "last_result_id": handle.runner._memory.last_result_id,  # noqa: SLF001
        }
        self.store.save_session(
            handle.session_id,
            session=self.service.session_object(handle.session_id),
            dialogue=handle.dialogue.to_dict(),
            memory=memory,
            summaries=list(handle.runner._summaries),  # noqa: SLF001
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


# --------------------------------------------------------------------------- #
def create_app(*, conversation: ConversationService | None = None,
               config: HarnessConfig | None = None,
               service: HarnessService | None = None,
               llm: Any | None = None,
               store_dir: str | None = None,
               dialogue_enabled: bool = True) -> FastAPI:
    config = config or HarnessConfig.from_env()
    conversation = conversation or ConversationService(
        config, service=service, llm=llm, store_dir=store_dir,
        dialogue_enabled=dialogue_enabled)

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
            "limits": {
                "turn_timeout_s": config.job_timeout_s,
                "poll_interval_hint_ms": 500,
                "max_text_chars": 4000,
            },
            "disclosure": DEMO_NOTE,
        }

    @app.post("/v1/sessions", status_code=201)
    def create_session(body: SessionRequest | None = None) -> dict[str, Any]:
        try:
            session_id = conversation.create_session((body.session_id if body else None))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"session_id": session_id, "status": "READY",
                "data_version": config.data_version,
                "data_status": config.expected_data_status}

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
    def result_layers(result_id: str, session_id: str) -> dict[str, Any]:
        try:
            return conversation.result_layers(session_id, result_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown session") from exc
        except Exception as exc:  # noqa: BLE001 - guard rejects foreign results
            raise HTTPException(status_code=403,
                                detail=f"result not available to this session: {exc}") from exc

    @app.get("/v1/results/{result_id}/evidence/{section}")
    def result_evidence(result_id: str, section: str, session_id: str,
                        offset: int = 0, limit: int = 50) -> dict[str, Any]:
        try:
            return conversation.evidence(session_id, result_id, section,
                                         offset=offset, limit=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown session") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return app


app = create_app()
