"""End-to-end HTTP test: two real uvicorn processes, real sockets.

Module B is started as its own process (`backend.app:app`) and Module C talks to
it over HTTP exactly as it would in deployment. Nothing is stubbed except the
model, which is a rule-based substitute so the test is deterministic and does not
need a GPU.

What this proves that the in-process tests cannot:
* the C API is reachable over a socket and returns the documented status codes;
* POST returns 202 while the turn is still running (no blocking request);
* polling converges to a terminal status;
* a clarification round-trip works across two separate HTTP requests;
* the B client's timeout/proxy handling works against a real server.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SHIELD = "zs_fed820d94e"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(method: str, url: str, payload: dict | None = None,
             timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body or b"{}")
        except ValueError:
            return error.code, {"raw": body.decode("utf-8", "replace")}


def _wait(url: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            status, payload = _request("GET", url, timeout=3.0)
            if status == 200:
                return payload
            last = (status, payload)
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last = exc
        time.sleep(0.25)
    raise AssertionError(f"{url} did not become ready: {last}")


def _wait_turn(url: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        status, payload = _request("GET", url, timeout=5.0)
        if status != 200:
            raise AssertionError(f"{url} -> {status} {payload}")
        last = payload
        if payload.get("status") != "RUNNING":
            return payload
        time.sleep(0.1)
    raise AssertionError(f"turn did not finish in {timeout}s: {last}")


@pytest.fixture(scope="module")
def servers(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("http")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), *[part for part in os.environ.get("PYTHONPATH", "").split(os.pathsep) if part]])
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    env.pop("http_proxy", None)
    env.pop("https_proxy", None)

    b_port, c_port = _free_port(), _free_port()
    b_env = dict(env)
    b_env["SHELTERPLAN_DATA_VERSION"] = "zhongshan_pilot_v0.1"
    b_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1",
         "--port", str(b_port), "--log-level", "warning"],
        cwd=str(ROOT), env=b_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    c_env = dict(env)
    c_env.update({
        "PORT": str(c_port),
        "STORE_DIR": str(tmp_path / "state"),
        "TRACE_DIR": str(tmp_path / "trace"),
        "SHELTERPLAN_B_BASE_URL": f"http://127.0.0.1:{b_port}",
        "SHELTERPLAN_TRACE_DIR": str(tmp_path / "trace"),
        "SHELTERPLAN_PILOT_CONFIG": "configs/pilot.yaml",
        "SHELTERPLAN_DATA_VERSION": "zhongshan_pilot_v0.1",
        "SHELTERPLAN_B_SEND_REGION_ID": "false",
    })
    c_proc = subprocess.Popen(
        [sys.executable, "-m", "tests.module_c.api_http_launcher"],
        cwd=str(ROOT), env=c_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        _wait(f"http://127.0.0.1:{b_port}/health")
        _wait(f"http://127.0.0.1:{c_port}/healthz")
        yield {
            "b": f"http://127.0.0.1:{b_port}",
            "c": f"http://127.0.0.1:{c_port}",
            "procs": (b_proc, c_proc),
        }
    finally:
        for proc in (c_proc, b_proc):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        for proc in (c_proc, b_proc):
            if proc.stdout:
                tail = proc.stdout.read()[-1200:]
                if "Traceback" in tail:
                    print(tail)


def test_two_servers_full_flow(servers):
    base = servers["c"]

    status, health = _request("GET", f"{base}/healthz")
    assert status == 200 and health["service"] == "shelterplan-module-c"

    status, created = _request("POST", f"{base}/v1/sessions", {})
    assert status == 201, created
    session_id = created["session_id"]

    # --- turn 1: clarification -------------------------------------------- #
    status, accepted = _request("POST", f"{base}/v1/sessions/{session_id}/turns",
                                {"text": "帮我做个扩容优化。"})
    assert status == 202, accepted
    assert accepted["status"] == "RUNNING"
    turn_id = accepted["turn_id"]
    first = _wait_turn(f"{base}/v1/turns/{turn_id}")
    assert first["status"] == "NEEDS_CLARIFICATION"
    assert first["clarification"]["fields"] == ["budget_units"]

    # --- turn 2: answer the missing field ---------------------------------- #
    _, accepted = _request("POST", f"{base}/v1/sessions/{session_id}/turns",
                           {"text": "预算是2。"})
    second = _wait_turn(f"{base}/v1/turns/{accepted['turn_id']}")
    assert second["status"] == "NEEDS_CLARIFICATION"
    assert second["clarification"]["fields"] == ["improvement_options"]

    # --- turn 3: complete the task over HTTP ------------------------------- #
    _, accepted = _request("POST", f"{base}/v1/sessions/{session_id}/turns",
                           {"text": f"{SHIELD} 扩容 300 人，成本 1。"})
    third = _wait_turn(f"{base}/v1/turns/{accepted['turn_id']}")
    assert third["status"] == "SUCCEEDED", third.get("message")
    assert third["resumed_from_clarification"] is True
    assert third["result_id"]
    assert third["data_status"] == "DEMO_ONLY"
    assert third["metrics"]["served_demand"] > 0
    assert third["timings"]["run_ms"] > 0

    # --- the result is addressable from the session ------------------------ #
    status, view = _request("GET", f"{base}/v1/sessions/{session_id}")
    assert status == 200
    assert view["params"]["budget_units"] == 2
    assert third["result_id"] in view["result_ids"]

    status, layers = _request(
        "GET", f"{base}/v1/results/{third['result_id']}/layers?session_id={session_id}")
    assert status == 200, layers
    assert len(layers["layers"][0]["rows"]) == 24

    # --- B was reached over HTTP, not in-process --------------------------- #
    status, capabilities = _request("GET", f"{servers['b']}/planning/capabilities")
    assert status == 200
    assert "assess" in capabilities["task_types"]


def test_http_cancel_and_404(servers):
    base = servers["c"]
    _, created = _request("POST", f"{base}/v1/sessions", {})
    session_id = created["session_id"]

    _, accepted = _request("POST", f"{base}/v1/sessions/{session_id}/turns",
                           {"text": "帮我做个扩容优化。"})
    _wait_turn(f"{base}/v1/turns/{accepted['turn_id']}")

    _, accepted = _request("POST", f"{base}/v1/sessions/{session_id}/turns",
                           {"text": "算了。"})
    cancelled = _wait_turn(f"{base}/v1/turns/{accepted['turn_id']}")
    assert cancelled["status"] == "CANCELLED"

    status, _ = _request("GET", f"{base}/v1/turns/does-not-exist")
    assert status == 404
    status, _ = _request("POST", f"{base}/v1/sessions/does-not-exist/turns", {"text": "hi"})
    assert status == 404
