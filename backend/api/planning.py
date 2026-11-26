"""Coarse-grained AgentArts to Module B planning API."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import uuid
from pathlib import Path

import yaml

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.errors import PlanningError
from backend.graph.composite_recovery import execute_disaster_recovery
from backend.graph.executor import execute_task, module_a_source_registry
from backend.graph.registry import capabilities
from backend.models import DisasterRecoveryRequest, TaskRequest
from backend.storage import JsonResultStore, canonical_hash

router = APIRouter(prefix="/planning", tags=["planning"])
_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_idempotency: dict[str, str] = {}
_idempotency_payloads: dict[str, str] = {}
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="module-b")
_results = JsonResultStore("outputs/module_b/runs")
_logger = logging.getLogger(__name__)
MAX_ACTIVE_JOBS = 100


def capabilities_document() -> dict:
    """Expose the graph, default A identity, and its allowlisted region sources."""
    document = dict(capabilities())
    root = Path(__file__).resolve().parents[2]
    region_id = None
    data_version = None
    data_status = None
    try:
        module_b = yaml.safe_load((root / "configs" / "module_b.yaml").read_text(encoding="utf-8")) or {}
        module_b_settings = module_b.get("module_b") or {}
        registry = module_a_source_registry(module_b)
        default_source = registry.get(str(module_b_settings.get("region_id") or ""))
        if default_source:
            region_id = module_b_settings.get("region_id")
            data_version = default_source["data_version"]
            data_status = default_source["data_status"]
        document["supported_sources"] = [
            {
                "region_id": allowed_region,
                "data_version": source["data_version"],
                "data_status": source["data_status"],
                "enabled": source["enabled"],
                "exclusion_reason": source["exclusion_reason"],
            }
            for allowed_region, source in sorted(registry.items())
        ]
    except (OSError, TypeError, yaml.YAMLError):
        pass
    document.update({"region_id": region_id, "data_version": data_version, "data_status": data_status})
    return document


def _run(job_id: str, trace_id: str, request: TaskRequest | DisasterRecoveryRequest) -> None:
    with _lock:
        _jobs[job_id]["status"] = "RUNNING"
    try:
        result = (execute_disaster_recovery(request, execution_trace_id=trace_id)
                  if isinstance(request, DisasterRecoveryRequest) else execute_task(request))
        result["job_id"] = job_id
        result["execution_trace_id"] = trace_id
        composite = ((result.get("result_detail") or {}).get("composite") or {})
        contract = composite.get("contract")
        if isinstance(contract, dict):
            contract["execution_trace_id"] = trace_id
        _results.save(result["result_id"], result)
        with _lock:
            _jobs[job_id].update({"status": "SUCCEEDED", "result_id": result["result_id"], "result_summary": result["result_summary"], "verification_status": result["verification_status"]})
    except PlanningError as exc:
        with _lock:
            _jobs[job_id].update({"status": "FAILED", "error": {"error_code": exc.error_code, "details": exc.details, "message": str(exc), "retryable": exc.retryable, "required_user_action": exc.required_user_action}})
    except Exception as exc:  # Convert internal faults to a stable envelope; details stay server-side.
        _logger.exception("Module B internal task failure: job_id=%s trace_id=%s task_id=%s",
                          job_id, trace_id, request.task_id)
        with _lock:
            _jobs[job_id].update({"status": "FAILED", "error": {"error_code": "INTERNAL_ERROR", "details": {}, "message": str(exc), "retryable": False, "required_user_action": None}})


def _submit(request: TaskRequest | DisasterRecoveryRequest, task_type: str | None = None) -> dict:
    actual_type = "disaster_recovery" if isinstance(request, DisasterRecoveryRequest) else request.task_type
    if task_type and actual_type != task_type:
        raise HTTPException(status_code=422, detail={"error_code": "TASK_TYPE_MISMATCH", "expected": task_type})
    request_fingerprint = canonical_hash(request.model_dump())
    if not request.task_id:
        request = request.model_copy(update={"task_id": str(uuid.uuid4())})
    digest = request.idempotency_key
    with _lock:
        existing = _idempotency.get(digest) if digest else None
        if existing:
            if _idempotency_payloads.get(digest) != request_fingerprint:
                raise HTTPException(status_code=409, detail={
                    "error_code": "IDEMPOTENCY_KEY_REUSED",
                    "message": "The idempotency key was already used with a different request body.",
                })
            return {"job_id": existing, **_jobs[existing], "idempotent_replay": True}
        active = sum(1 for row in _jobs.values() if row.get("status") in {"QUEUED", "RUNNING"})
        if active >= MAX_ACTIVE_JOBS:
            raise HTTPException(status_code=429, detail={"error_code": "JOB_LIMIT_REACHED", "retryable": True})
        job_id, trace_id = str(uuid.uuid4()), str(uuid.uuid4())
        _jobs[job_id] = {"task_id": request.task_id, "task_type": actual_type, "status": "QUEUED", "execution_trace_id": trace_id, "skill_graph_version": capabilities()["skill_graph_version"]}
        if digest:
            _idempotency[digest] = job_id
            _idempotency_payloads[digest] = request_fingerprint
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


@router.post("/disaster-recovery", status_code=202)
def create_disaster_recovery(request: DisasterRecoveryRequest):
    return _submit(request, "disaster_recovery")


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
