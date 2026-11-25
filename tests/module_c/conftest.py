"""Shared fixtures for Module C tests.

Module B is exercised through its real FastAPI app (in-process TestClient).
Nothing here stubs the planner: only transport faults and timeouts are injected.
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import warnings

import pytest

warnings.filterwarnings("ignore")

# Localhost must never go through the workstation's corporate proxy: the MCP
# HTTP transport and the Module B client both speak plain HTTP on 127.0.0.1.
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient  # noqa: E402

from backend.app import app as b_app  # noqa: E402
from agent.b_client import BClient  # noqa: E402
from agent.config import HarnessConfig  # noqa: E402
from agent.contracts import ScenarioParams  # noqa: E402
from agent.harness.executor import HarnessExecutor  # noqa: E402
from agent.harness.session import Session  # noqa: E402
from agent.harness.trace import TraceRecorder  # noqa: E402


@pytest.fixture(scope="session")
def b_app_client() -> TestClient:
    return TestClient(b_app)


@pytest.fixture()
def config(tmp_path: Path) -> HarnessConfig:
    return HarnessConfig(trace_dir=tmp_path / "trace")


@pytest.fixture()
def b_client(config: HarnessConfig, b_app_client: TestClient) -> BClient:
    return BClient(config, client=b_app_client)


@pytest.fixture()
def session(config: HarnessConfig) -> Session:
    return Session("s-test", ScenarioParams(
        population_demand_ratio=config.default_population_demand_ratio,
        max_walk_time_min=config.default_max_walk_time_min,
        budget_units=None,
        disabled_shelter_ids=(),
        data_version=config.data_version,
    ))


@pytest.fixture()
def trace(config: HarnessConfig, session: Session) -> TraceRecorder:
    return TraceRecorder(config.trace_dir / f"{session.session_id}.jsonl", session.session_id)


@pytest.fixture()
def executor(b_client: BClient, session: Session, trace: TraceRecorder,
             config: HarnessConfig) -> HarnessExecutor:
    return HarnessExecutor(b_client, session, trace, config)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def python_env(**overrides: str) -> dict[str, str]:
    """Inherit the current interpreter's import path so subprocesses see the same deps."""
    env = dict(os.environ)
    paths = [str(ROOT), *[p for p in sys.path if p and p not in ("", ".")]]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update(overrides)
    return env


@pytest.fixture(scope="session")
def live_b_server():
    """A real uvicorn process, so MCP tests speak HTTP instead of ASGI."""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=python_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    import httpx
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"Module B failed to start:\n{output[-2000:]}")
        try:
            if httpx.get(f"{base_url}/health", timeout=2.0).status_code == 200:
                break
        except Exception:
            time.sleep(0.4)
    else:
        proc.terminate()
        raise RuntimeError("Module B did not become healthy in time")
    yield base_url
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


@pytest.fixture()
def mcp_env(live_b_server: str) -> dict[str, str]:
    return python_env(SHELTERPLAN_B_BASE_URL=live_b_server,
                      SHELTERPLAN_JOB_TIMEOUT_S="180",
                      SHELTERPLAN_TRACE_DIR=str(ROOT / "outputs" / "module_c" / "trace"))


class FlakyClient:
    """Wraps a client and fails the first N calls with a transport error."""

    def __init__(self, inner, failures: int = 1) -> None:
        self.inner = inner
        self.failures = failures
        self.calls = 0

    def request(self, method, path, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            import httpx
            raise httpx.ConnectError("injected transport failure")
        return self.inner.request(method, path, **kwargs)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def close(self) -> None:
        self.inner.close()


class JobLostClient:
    """Returns 404 for every job poll (simulates a Module B restart)."""

    def __init__(self, inner, fail_after: int = 1) -> None:
        self.inner = inner
        self.fail_after = fail_after
        self.job_polls = 0

    def request(self, method, path, **kwargs):
        if method == "GET" and str(path).startswith("/planning/jobs/"):
            self.job_polls += 1
            if self.job_polls > self.fail_after:
                import httpx
                return httpx.Response(404, json={"detail": {"error_code": "JOB_NOT_FOUND"}})
        return self.inner.request(method, path, **kwargs)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def close(self) -> None:
        self.inner.close()
