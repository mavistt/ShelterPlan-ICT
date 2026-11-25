"""Append-only JSONL execution trace.

One row per tool call plus one summary row per task. Rows are deliberately
narrow: identifiers, timings, status and a redacted argument digest. No
credentials, no raw population/road-network payloads, no prompt text.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from agent.contracts import digest, utc_now


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TraceRecorder:
    def __init__(self, path: str | Path, session_id: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self._lock = threading.Lock()
        self.rows_written = 0

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        row = {
            "ts": _now_iso(),
            "event": event,
            "session_id": self.session_id,
            **fields,
        }
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
            self.rows_written += 1
        return row

    def tool_call(
        self,
        *,
        task_id: str,
        tool_name: str,
        started_at: str,
        finished_at: str,
        duration_ms: float,
        status: str,
        attempt: int = 1,
        attempts: int = 1,
        retries: int = 0,
        scenario_id: str | None = None,
        parent_scenario_id: str | None = None,
        data_version: str | None = None,
        args: dict[str, Any] | None = None,
        job_id: str | None = None,
        result_id: str | None = None,
        execution_trace_id: str | None = None,
        error_type: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.log(
            "tool_call",
            task_id=task_id,
            tool_name=tool_name,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=round(duration_ms, 3),
            status=status,
            attempt=attempt,
            attempts=attempts,
            retries=retries,
            scenario_id=scenario_id,
            parent_scenario_id=parent_scenario_id,
            data_version=data_version,
            args_digest=digest(args or {}),
            args_summary=summarize_args(args or {}),
            job_id=job_id,
            result_id=result_id,
            execution_trace_id=execution_trace_id,
            error_type=error_type,
            detail=detail or {},
        )

    def task(self, *, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.log("task", task_id=task_id, **payload)


def summarize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Redact long lists; keep only shapes the trace needs for debugging."""
    summary: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, (list, tuple)):
            summary[key] = {"count": len(value), "sample": list(value[:3])}
        elif isinstance(value, dict):
            summary[key] = {"keys": sorted(map(str, value))[:8]}
        elif isinstance(value, str) and len(value) > 120:
            summary[key] = value[:120] + "…"
        else:
            summary[key] = value
    return summary


def read_trace(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def by_task(path: str | Path, task_id: str) -> list[dict[str, Any]]:
    return [row for row in read_trace(path) if row.get("task_id") == task_id]


def iter_tasks(path: str | Path) -> Iterator[str]:
    seen: set[str] = set()
    for row in read_trace(path):
        task_id = row.get("task_id")
        if task_id and task_id not in seen:
            seen.add(task_id)
            yield task_id
