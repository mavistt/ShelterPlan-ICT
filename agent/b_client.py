"""HTTP client for the Module B planning service.

Rules encoded here (all verified against the running service):
* A 202 only means "accepted" - the outcome arrives through the job.
* Business failures surface as `job.status == FAILED` with `job.error`.
* `404` on a job id means the in-process job table no longer knows it (restart).
* `FEASIBLE` is publishable; it just is not proven optimal.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from agent.config import HarnessConfig
from agent.errors import (
    BError,
    BJobMissing,
    BJobTimeout,
    BResultMissing,
    BTransportError,
    HarnessError,
)

ENDPOINTS = {
    "assess": "/planning/tasks",
    "optimize": "/planning/tasks",
    "diagnose_intervene": "/planning/tasks",
    "sensitivity": "/planning/tasks",
    "explain_result": "/planning/tasks",
    "stress_test": "/planning/stress-tests",
    "compare": "/planning/comparisons",
}


class BClient:
    def __init__(self, config: HarnessConfig | None = None, *, client: Any | None = None) -> None:
        self.config = config or HarnessConfig()
        self.base_url = self.config.b_base_url.rstrip("/")
        self._owns_client = client is None
        if client is None:
            local = any(token in self.base_url for token in ("127.0.0.1", "localhost", "::1"))
            self._client = httpx.Client(
                base_url=self.base_url,
                # A workstation proxy must never intercept loopback traffic.
                trust_env=not local,
                timeout=httpx.Timeout(
                    self.config.read_timeout_s,
                    connect=self.config.connect_timeout_s,
                    read=self.config.read_timeout_s,
                ),
            )
        else:
            self._client = client
        self.call_count = 0

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "BClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 retries: int | None = None, timeout_s: float | None = None) -> dict[str, Any]:
        retries = self.config.max_transport_retries if retries is None else retries
        effective_timeout = self.config.read_timeout_s if timeout_s is None else timeout_s
        is_test_client = type(self._client).__name__ == "TestClient"
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                self.call_count += 1
                if is_test_client:
                    response = self._client.request(method, path, json=json_body)
                else:
                    response = self._client.request(
                        method, path, json=json_body,
                        timeout=httpx.Timeout(
                            effective_timeout,
                            connect=self.config.connect_timeout_s,
                            read=effective_timeout,
                        ),
                    )
            except TypeError:
                # TestClient-like shims do not accept a per-call timeout object.
                response = self._client.request(method, path, json=json_body)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt >= retries:
                    raise BTransportError(
                        f"Module B is unreachable: {exc.__class__.__name__}",
                        details={"url": f"{self.base_url}{path}", "attempts": attempt + 1},
                    ) from exc
                time.sleep(self.config.retry_backoff_s * (2 ** attempt))
                continue

            status = int(getattr(response, "status_code", 0))
            if status >= 500 and attempt < retries:
                last_error = BTransportError(f"Module B returned HTTP {status}")
                time.sleep(self.config.retry_backoff_s * (2 ** attempt))
                continue
            if status == 429 and attempt < retries:
                time.sleep(self.config.retry_backoff_s * (2 ** attempt))
                continue
            return self._decode(response)

        raise BTransportError("Module B request failed after retries",
                              details={"error": str(last_error)})

    def _decode(self, response: Any) -> dict[str, Any]:
        status = int(response.status_code)
        try:
            body = response.json()
        except Exception:
            body = None

        if status < 400:
            if isinstance(body, dict):
                return body
            raise BTransportError("Module B returned a non-JSON success body", details={"status": status})

        error_code, message, details, retryable, action = _split_error_body(body, status)
        if status == 404 and error_code == "JOB_NOT_FOUND":
            raise BJobMissing("Module B does not know this job id.",
                              details={"error_code": error_code, "status": status})
        if status == 404 and error_code == "RESULT_NOT_FOUND":
            raise BResultMissing("Module B does not know this result id.",
                                 details={"error_code": error_code, "status": status})
        raise BError(error_code or f"HTTP_{status}", message or f"HTTP {status}",
                     details=details, retryable=retryable, user_action=action, http_status=status)

    # ------------------------------------------------------------------ #
    # Endpoints
    # ------------------------------------------------------------------ #
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def get_capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/planning/capabilities")

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_type = payload.get("task_type")
        endpoint = ENDPOINTS.get(task_type)
        if endpoint is None:
            raise ValueError(f"unsupported task_type for submit: {task_type}")
        return self._request("POST", endpoint, json_body=payload, retries=0,
                             timeout_s=self.config.submit_timeout_s)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/planning/jobs/{job_id}", retries=1)

    def get_result(self, result_id: str) -> dict[str, Any]:
        return self._request("GET", f"/planning/results/{result_id}", retries=1,
                             timeout_s=self.config.read_timeout_s)

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #
    def wait_for_job(self, job_id: str, *, deadline_s: float | None = None) -> dict[str, Any]:
        deadline_s = self.config.job_timeout_s if deadline_s is None else deadline_s
        started = time.monotonic()
        interval = self.config.poll_initial_s
        last_status = None
        while True:
            job = self.get_job(job_id)
            status = job.get("status")
            last_status = status
            if status == "SUCCEEDED":
                return job
            if status == "FAILED":
                raise _job_error(job)
            elapsed = time.monotonic() - started
            if elapsed >= deadline_s:
                raise BJobTimeout(
                    f"Job {job_id} did not finish within {deadline_s:.0f}s (last status: {last_status}).",
                    details={"job_id": job_id, "last_status": last_status, "elapsed_s": round(elapsed, 3)},
                )
            time.sleep(min(interval, max(0.0, deadline_s - elapsed)))
            interval = min(interval * 1.6, self.config.poll_max_s)

    def submit_and_wait(self, payload: dict[str, Any], *, deadline_s: float | None = None
                        ) -> tuple[dict[str, Any], dict[str, Any]]:
        submitted = self.submit(payload)
        job = self.wait_for_job(submitted["job_id"], deadline_s=deadline_s)
        return submitted, job


def _split_error_body(body: Any, status: int) -> tuple[str | None, str | None, dict, bool, str | None]:
    """Module B uses two envelope shapes; normalise both."""
    if not isinstance(body, dict):
        return None, None, {}, False, None
    if "error_code" in body:  # planning-stage flat envelope
        return (body.get("error_code"), body.get("message"),
                body.get("details") or {}, bool(body.get("retryable")),
                body.get("required_user_action"))
    detail = body.get("detail")
    if isinstance(detail, dict):
        return (detail.get("error_code"), detail.get("message") or detail.get("error_code"),
                detail, bool(detail.get("retryable")), detail.get("required_user_action"))
    if isinstance(detail, str):
        return (None, detail, {}, False, None)
    return None, None, {}, False, None


def _job_error(job: dict[str, Any]) -> HarnessError:
    error = job.get("error") or {}
    code = error.get("error_code") or "UNKNOWN"
    return BError(
        code,
        error.get("message") or f"Module B job failed with {code}",
        details={**error.get("details", {}), "job_id": job.get("job_id")},
        retryable=bool(error.get("retryable")),
        user_action=error.get("required_user_action"),
    )
