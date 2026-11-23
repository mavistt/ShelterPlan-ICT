"""Runnable end-to-end business demo for the C -> D handoff.

    python examples/c_to_d_business_flow.py                  # deterministic model substitute
    python examples/c_to_d_business_flow.py --real-llm       # local Ollama model
    python examples/c_to_d_business_flow.py --output <file>

What "real" means here: two real uvicorn processes (Module B `backend.app:app`
and Module C's HTTP API), real sockets, real planner/solver calls, real session
state and real result ids. Only the *model* can be substituted: the default
`RuleLLM` reads the sentence with C's rule extractor so the demo is reproducible
in CI without a GPU. `--real-llm` uses `LLMAdapter` against the configured
local Ollama endpoint instead. Module B is never mocked or reimplemented.

The script prints a human-readable summary and writes the full per-step envelope
to `outputs/module_c/c_to_d_demo/`. It exits non-zero if any step fails.
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
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SHIELD = "zs_fed820d94e"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(method: str, url: str, payload: dict | None = None,
            timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body or b"{}")
        except ValueError:
            return error.code, {"raw": body.decode("utf-8", "replace")}


def wait_health(url: str, procs: list[subprocess.Popen], timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for proc in procs:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early: {proc.args}")
        try:
            status, _ = request("GET", url, timeout=2.0)
            if status == 200:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.3)
    raise RuntimeError(f"server not healthy: {url}")


def wait_turn(base: str, turn_id: str, timeout: float = 180.0) -> dict:
    url = f"{base}/v1/turns/{turn_id}"
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        status, payload = request("GET", url, timeout=5.0)
        if status != 200:
            raise RuntimeError(f"polling {url} -> {status} {payload}")
        last = payload
        if payload.get("status") != "RUNNING":
            return payload
        time.sleep(0.15)
    raise RuntimeError(f"turn {turn_id} did not finish: {last.get('status')}")


def start_servers(mode: str, workdir: Path) -> tuple[str, str, list[subprocess.Popen]]:
    env = dict(os.environ)
    paths = [str(ROOT), *[p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)

    b_port, c_port = free_port(), free_port()
    b_env = dict(env, SHELTERPLAN_DATA_VERSION="zhongshan_pilot_v0.1")
    b_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1",
         "--port", str(b_port), "--log-level", "warning"],
        cwd=str(ROOT), env=b_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    c_env = dict(env, PORT=str(c_port), STORE_DIR=str(workdir / "state"),
                 TRACE_DIR=str(workdir / "trace"), LLM_MODE=mode,
                 SHELTERPLAN_B_BASE_URL=f"http://127.0.0.1:{b_port}",
                 SHELTERPLAN_TRACE_DIR=str(workdir / "trace"),
                 SHELTERPLAN_PILOT_CONFIG="configs/pilot.yaml",
                 SHELTERPLAN_DATA_VERSION="zhongshan_pilot_v0.1",
                 SHELTERPLAN_B_SEND_REGION_ID="false")
    c_proc = subprocess.Popen(
        [sys.executable, "-m", "tests.module_c.api_http_launcher"],
        cwd=str(ROOT), env=c_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    procs = [c_proc, b_proc]
    try:
        wait_health(f"http://127.0.0.1:{b_port}/health", procs)
        wait_health(f"http://127.0.0.1:{c_port}/healthz", procs)
    except Exception:
        stop_servers(procs)
        raise
    return f"http://127.0.0.1:{b_port}", f"http://127.0.0.1:{c_port}", procs


def stop_servers(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def submit(base: str, session_id: str, text: str) -> dict:
    status, accepted = request("POST", f"{base}/v1/sessions/{session_id}/turns",
                               {"text": text})
    if status != 202 or accepted.get("status") != "RUNNING":
        raise RuntimeError(f"POST /turns -> {status} {accepted}")
    return wait_turn(base, accepted["turn_id"])


def projection(envelope: dict) -> dict:
    return {
        "turn_id": envelope.get("turn_id"),
        "status": envelope.get("status"),
        "task_type": envelope.get("task_type"),
        "result_id": envelope.get("result_id"),
        "scenario_id": envelope.get("scenario_id"),
        "job_id": envelope.get("job_id"),
        "data_version": envelope.get("data_version"),
        "data_status": envelope.get("data_status"),
        "metrics": {k: envelope.get("metrics", {}).get(k)
                    for k in ("served_demand", "total_demand",
                              "weighted_walk_time_min", "coverage_ratio")},
        "clarification": envelope.get("clarification"),
        "error": envelope.get("error"),
        "warnings": envelope.get("warnings"),
        "timings": envelope.get("timings"),
        "message": (envelope.get("message") or "")[:400],
    }


def run_flows(base: str) -> dict[str, list[dict]]:
    _, session = request("POST", f"{base}/v1/sessions", {})
    main_session = session["session_id"]
    business = []
    # 1 现状评估 -> 2 修改条件 -> 3 扩容优化 -> 4 方案比较
    steps = [
        ("现状评估", "评估一下现状。"),
        ("修改条件", "需求比例改成0.3，步行阈值改成20分钟，重新评估。"),
        ("扩容优化", f"预算设为2，给 {SHIELD} 扩容300人、成本1，做扩容优化。"),
        ("方案比较", "比较需求比例0.2和0.4两种情况的覆盖差异。"),
    ]
    for index, (label, text) in enumerate(steps, 1):
        envelope = submit(base, main_session, text)
        business.append({"step": index, "label": label, "text": text,
                         **projection(envelope)})
        print(f"  [{index}] {label:<8} {envelope.get('status'):<20} "
              f"result={envelope.get('result_id')} "
              f"served={envelope.get('metrics', {}).get('served_demand')}")
    for row in business:
        if row["status"] != "SUCCEEDED":
            raise AssertionError(f"business step {row['step']} failed: {row['status']} "
                                 f"{row['error']} {row['message'][:200]}")

    _, session = request("POST", f"{base}/v1/sessions", {})
    clarify_session = session["session_id"]
    clarification = []
    clarify_steps = [
        ("缺预算", "帮我做个扩容优化。"),
        ("补预算", "预算是2。"),
        ("补候选", f"{SHIELD} 扩容300人，成本1。"),
    ]
    for index, (label, text) in enumerate(clarify_steps, 1):
        envelope = submit(base, clarify_session, text)
        clarification.append({"step": index, "label": label, "text": text,
                              **projection(envelope)})
        print(f"  [c{index}] {label:<8} {envelope.get('status'):<20} "
              f"fields={(envelope.get('clarification') or {}).get('fields')} "
              f"result={envelope.get('result_id')}")
    assert clarification[0]["status"] == "NEEDS_CLARIFICATION"
    assert clarification[1]["status"] == "NEEDS_CLARIFICATION"
    assert clarification[2]["status"] == "SUCCEEDED", clarification[2]["message"]

    _, view = request("GET", f"{base}/v1/sessions/{main_session}")
    return {"business_flow": business, "clarification_flow": clarification,
            "final_session_view": {
                "session_id": main_session,
                "params": view.get("params"),
                "result_ids": view.get("result_ids"),
                "pending_clarification": view.get("pending_clarification"),
            }}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python examples/c_to_d_business_flow.py")
    parser.add_argument("--real-llm", action="store_true",
                        help="use the configured LLM adapter instead of RuleLLM")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    mode = "real" if args.real_llm else "rule"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = ROOT / "outputs" / "module_c" / "c_to_d_demo"
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = outdir / f"run-{mode}-{stamp}"
    workdir.mkdir(parents=True, exist_ok=True)
    target = Path(args.output) if args.output else outdir / f"business_flow_{mode}.json"
    if not target.is_absolute():
        target = ROOT / target

    print(f"Model mode: {mode}  (B and C are real uvicorn processes)")
    b_url, c_url, procs = start_servers(mode, workdir)
    try:
        flows = run_flows(c_url)
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model_mode": mode,
            "module_b": b_url, "module_c": c_url,
            "state_dir": str(workdir / "state"),
            "trace_dir": str(workdir / "trace"),
            **flows,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nOK: {len(flows['business_flow'])} business steps + "
              f"{len(flows['clarification_flow'])} clarification steps")
        print(f"written: {target}")
        return 0
    finally:
        stop_servers(procs)


if __name__ == "__main__":
    raise SystemExit(main())
