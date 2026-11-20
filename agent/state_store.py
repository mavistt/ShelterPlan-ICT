"""Durable state for the Web application layer.

The conversation state lives in memory (one `StructuredRunner` per session).
Memory is fast and simple, but it is not durable: a process restart would drop
the scenario snapshot, the pending clarification and the turn envelopes, and a
poll for a turn id would answer 404 forever.

This store closes that gap without pretending to be a database:

* one JSON file per session, written atomically (tmp + os.replace);
* only *terminal* turn envelopes are persisted; a turn that was RUNNING when the
  process died is rewritten as FAILED with `INTERRUPTED_BY_RESTART` on load, so
  a client never polls a zombie;
* Module B's own jobs are **not** resumable: the store records the job id and
  says so, instead of pretending the computation can be resumed.

Limits (documented, tested, not hidden):
* single process, single writer per session; there is no cross-process lock;
* no TTL eviction yet - files accumulate under `state_dir/sessions`;
* payload size follows the projected summary (<= 20 KB) plus turn history.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from agent.harness.session import Session


class SessionStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.sessions_dir = self.root / "sessions"
        self.turns_dir = self.root / "turns"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.turns_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{_safe(session_id)}.json"

    def _turn_path(self, turn_id: str) -> Path:
        return self.turns_dir / f"{_safe(turn_id)}.json"

    # ------------------------------------------------------------------ #
    def save_session(self, session_id: str, *, session: Session,
                     dialogue: dict[str, Any], memory: dict[str, Any],
                     summaries: list[dict[str, Any]],
                     client_turns: dict[str, str] | None = None,
                     turn_ids: list[str] | None = None,
                     region_id: str | None = None) -> None:
        payload: dict[str, Any] = {
            "session_id": session_id,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session": session.dump(),
            "dialogue": dialogue,
            "memory": memory,
            "summaries": summaries[-5:],
        }
        if client_turns is not None:
            payload["client_turns"] = dict(client_turns)
        if turn_ids is not None:
            payload["turn_ids"] = list(turn_ids)
        if region_id is not None:
            payload["region_id"] = region_id
        _atomic_write(self._session_path(session_id), payload)

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        return _read(self._session_path(session_id))

    def list_sessions(self) -> list[str]:
        return sorted(path.stem for path in self.sessions_dir.glob("*.json"))

    # ------------------------------------------------------------------ #
    def save_turn(self, envelope: dict[str, Any]) -> None:
        _atomic_write(self._turn_path(envelope["turn_id"]), envelope)

    def load_turn(self, turn_id: str) -> dict[str, Any] | None:
        return _read(self._turn_path(turn_id))

    def load_turns(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        session_payload = self.load_session(session_id)
        if session_payload and isinstance(session_payload.get("turn_ids"), list):
            for turn_id in session_payload["turn_ids"]:
                if not isinstance(turn_id, str) or not turn_id:
                    continue
                payload = self.load_turn(turn_id)
                if payload and payload.get("session_id") == session_id:
                    rows.append(payload)
        else:
            for path in self.turns_dir.glob("*.json"):
                payload = _read(path)
                if payload and payload.get("session_id") == session_id:
                    rows.append(payload)
        rows.sort(key=lambda row: row.get("created_at") or "")
        return rows[-limit:]

    def delete_session_turns(self, session_id: str, *,
                             turn_ids: list[str] | None = None) -> int:
        deleted = 0
        session_payload = self.load_session(session_id)
        candidates: list[str] = []
        if turn_ids:
            candidates.extend(turn_ids)
        if session_payload and isinstance(session_payload.get("turn_ids"), list):
            for tid in session_payload["turn_ids"]:
                if isinstance(tid, str) and tid not in candidates:
                    candidates.append(tid)
        if candidates:
            for tid in candidates:
                path = self._turn_path(tid)
                if path.is_file():
                    try:
                        path.unlink()
                        deleted += 1
                    except OSError:
                        pass
            return deleted
        for path in self.turns_dir.glob("*.json"):
            payload = _read(path)
            if payload and payload.get("session_id") == session_id:
                try:
                    path.unlink()
                    deleted += 1
                except OSError:
                    pass
        return deleted

    def mark_interrupted(self) -> int:
        """Rewrite turns that were mid-flight when the process stopped."""
        changed = 0
        for path in self.turns_dir.glob("*.json"):
            payload = _read(path)
            if not payload or payload.get("status") != "RUNNING":
                continue
            payload["status"] = "FAILED"
            payload["error"] = {
                "code": "INTERRUPTED_BY_RESTART",
                "message": ("应用进程在本次任务完成前重启；Module B 的计算不可续跑，"
                            "请重新发起该请求。"),
                "recoverable": False,
            }
            payload["message"] = "任务因应用重启而中断，未产生可用结果。请重新发起。"
            payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _atomic_write(path, payload)
            changed += 1
        return changed


def _safe(name: str) -> str:
    keep = [char if char.isalnum() or char in "-_" else "_" for char in name]
    return "".join(keep)[:120] or "unnamed"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
