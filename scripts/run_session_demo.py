"""Run one reproducible Module B -> C Session/Turn demo.

The default ``rule`` mode is a deterministic harness smoke test.  Set
``--llm-mode real`` only when the configured LLM credentials are available.
The script starts two real localhost HTTP processes, binds C to the explicit
A v2 region/version, polls the turn to a terminal state, and writes the
session snapshot as inspectable evidence.  It never fabricates a result.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REGION_ID = "shiqi"
DATA_VERSION = "shelterplan_module_a_v2.0-demo__shiqi"
DEFAULT_TEXT = (
    "请评估石岐当前避难服务恢复情况，服务需求比例为0.2，"
    "最大步行时间为10分钟，预算为13个单位。"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_request(url: str, *, method: str = "GET", body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=payload, method=method)
    request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait(url: str, label: str) -> None:
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            _json_request(url)
            return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"{label} did not become ready: {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-mode", choices=("rule", "real"), default="rule")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument(
        "--output",
        default="outputs/integration/demo/session_demo.json",
    )
    args = parser.parse_args()

    run_dir = ROOT / "outputs" / "integration" / "demo" / f"run-{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"

    b_port, c_port = _free_port(), _free_port()
    b_env = dict(env)
    b_env.update({
        "SHELTERPLAN_DATA_VERSION": DATA_VERSION,
        "SHELTERPLAN_B_SEND_REGION_ID": "true",
    })
    c_env = dict(env)
    c_env.update({
        "PORT": str(c_port),
        "STORE_DIR": str(run_dir / "state"),
        "TRACE_DIR": str(run_dir / "trace"),
        "SHELTERPLAN_TRACE_DIR": str(run_dir / "trace"),
        "SHELTERPLAN_B_BASE_URL": f"http://127.0.0.1:{b_port}",
        "SHELTERPLAN_PILOT_CONFIG": "configs/module_a_v2_b/shiqi.yaml",
        "SHELTERPLAN_DATA_VERSION": DATA_VERSION,
        "SHELTERPLAN_B_SEND_REGION_ID": "true",
        "LLM_MODE": args.llm_mode,
    })

    b = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1",
         "--port", str(b_port), "--log-level", "warning"],
        cwd=ROOT, env=b_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    c = subprocess.Popen(
        [sys.executable, "-m", "tests.module_c.api_http_launcher"],
        cwd=ROOT, env=c_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )

    document: dict = {
        "status": "NOT_RUN",
        "llm_mode": args.llm_mode,
        "region_id": REGION_ID,
        "data_version": DATA_VERSION,
        "data_status": "DEMO_ONLY",
        "user_text": args.text,
        "servers": {
            "module_b": f"http://127.0.0.1:{b_port}",
            "module_c": f"http://127.0.0.1:{c_port}",
        },
    }
    try:
        _wait(f"http://127.0.0.1:{b_port}/health", "Module B")
        _wait(f"http://127.0.0.1:{c_port}/healthz", "Module C")
        session = _json_request(
            f"http://127.0.0.1:{c_port}/v1/sessions",
            method="POST",
            body={"region_id": REGION_ID, "data_version": DATA_VERSION},
        )
        session_id = session["session_id"]
        accepted = _json_request(
            f"http://127.0.0.1:{c_port}/v1/sessions/{session_id}/turns",
            method="POST",
            body={"text": args.text, "client_turn_id": "demo-turn-1"},
        )
        turn_id = accepted["turn_id"]
        deadline = time.time() + 180
        turn = accepted
        while time.time() < deadline:
            turn = _json_request(f"http://127.0.0.1:{c_port}/v1/turns/{turn_id}")
            if turn.get("status") not in {"RUNNING", "PENDING"}:
                break
            time.sleep(0.5)
        snapshot = _json_request(f"http://127.0.0.1:{c_port}/v1/sessions/{session_id}")
        document.update({
            "status": turn.get("status", "UNKNOWN"),
            "session": session,
            "turn": turn,
            "session_snapshot": snapshot,
        })
        if turn.get("status") in {"RUNNING", "PENDING"}:
            raise RuntimeError("C turn did not reach a terminal state within 180 seconds")
    except Exception as exc:  # Keep a machine-readable failed demo artifact.
        document.update({"status": "INFRA_ERROR", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        for process in (c, b):
            process.terminate()
        for name, process in (("c", c), ("b", b)):
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            (run_dir / f"{name}.out").write_text(stdout or "", encoding="utf-8")
            (run_dir / f"{name}.err").write_text(stderr or "", encoding="utf-8")

    output = (ROOT / args.output).resolve()
    output.relative_to(ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0 if document["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
