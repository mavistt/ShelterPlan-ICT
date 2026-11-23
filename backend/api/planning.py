"""Coarse-grained AgentArts to Module B planning API."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import uuid
from pathlib import Path

import yaml

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.errors import PlanningError
from backend.graph.executor import execute_task
from backend.graph.registry import capabilities
from backend.models import TaskRequest
from backend.storage import JsonResultStore

router = APIRouter(prefix="/planning", tags=["planning"])
_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_idempotency: dict[str, str] = {}
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="module-b")
_results = JsonResultStore("outputs/module_b/runs")
MAX_ACTIVE_JOBS = 100


def capabilities_document() -> dict:
    """Expose the registered graph plus the configured A handoff identity."""
    document = dict(capabilities())
    root = Path(__file__).resolve().parents[2]
    region_id = None
    data_version = None
    data_status = None
    try:
        module_b = yaml.safe_load((root / "configs" / "module_b.yaml").read_text(encoding="utf-8")) or {}
        source = (module_b.get("module_b") or {}).get("source_config")
        if source:
            adapter = yaml.safe_load((root / source).read_text(encoding="utf-8")) or {}
            project = adapter.get("project") or {}
            region_id = project.get("region_id")
            data_version = project.get("data_version")
            data_status = project.get("data_status")
    except (OSError, TypeError, yaml.YAMLError):
        pass
    document.update({"region_id": region_id, "data_version": data_version, "data_status": data_status})
    return document


def _run(job_id: str, trace_id: str, request: TaskRequest) -> None:
    with _lock:
        _jobs[job_id]["status"] = "RUNNING"
    try:
        result = execute_task(request)
        result["job_id"] = job_id
        result["execution_trace_id"] = trace_id
        _results.save(result["result_id"], result)
        with _lock:
            _jobs[job_id].update({"status": "SUCCEEDED", "result_id": result["result_id"], "result_summary": result["result_summary"], "verification_status": result["verification_status"]})
    except PlanningError as exc:
        with _lock:
            _jobs[job_id].update({"status": "FAILED", "error": {"error_code": exc.error_code, "details": exc.details, "message": str(exc), "retryable": exc.retryable, "required_user_action": exc.required_user_action}})
    except Exception as exc:  # Convert internal faults to a stable envelope; details stay server-side.
        with _lock:
            _jobs[job_id].update({"status": "FAILED", "error": {"error_code": "INTERNAL_ERROR", "details": {}, "message": str(exc), "retryable": False, "required_user_action": None}})


def _submit(request: TaskRequest, task_type: str | None = None) -> dict:
    if task_type and request.task_type != task_type:
        raise HTTPException(status_code=422, detail={"error_code": "TASK_TYPE_MISMATCH", "expected": task_type})
    if not request.task_id:
        request = request.model_copy(update={"task_id": str(uuid.uuid4())})
    digest = request.idempotency_key
    with _lock:
        existing = _idempotency.get(digest) if digest else None
        if existing:
            return {"job_id": existing, **_jobs[existing], "idempotent_replay": True}
        active = sum(1 for row in _jobs.values() if row.get("status") in {"QUEUED", "RUNNING"})
        if active >= MAX_ACTIVE_JOBS:
            raise HTTPException(status_code=429, detail={"error_code": "JOB_LIMIT_REACHED", "retryable": True})
        job_id, trace_id = str(uuid.uuid4()), str(uuid.uuid4())
        _jobs[job_id] = {"task_id": request.task_id, "status": "QUEUED", "execution_trace_id": trace_id, "skill_graph_version": capabilities()["skill_graph_version"]}
        if digest:
            _idempotency[digest] = job_id
    _pool.submit(_run, job_id, trace_id, request)
    return {"job_id": job_id, **_jobs[job_id], "idempotent_replay": False}


@router.get("/capabilities")
def get_capabilities():
    return capabilities_document()


@router.post("/tasks", status_code=202)
def create_task(request: TaskRequest):
    return _submit(request)


@router.post("/stress-tests", status_code=202)
def create_stress_test(request: TaskRequest):
    return _submit(request, "stress_test")


@router.post("/comparisons", status_code=202)
def create_comparison(request: TaskRequest):
    return _submit(request, "compare")


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error_code": "JOB_NOT_FOUND"})
    return {"job_id": job_id, **job}


@router.get("/results/{result_id}")
def get_result(result_id: str):
    document = _results.get(result_id)
    if document is None:
        raise HTTPException(status_code=404, detail={"error_code": "RESULT_NOT_FOUND"})
    # Full assignment/evidence are served to the authorized local caller; no public URL is exposed.
    return document
