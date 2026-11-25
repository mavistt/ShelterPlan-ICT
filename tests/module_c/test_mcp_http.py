"""MCP over Streamable HTTP - the transport AgentArts would use.

Proves the same four tools work without stdio, which is the prerequisite for a
future hosted deployment.
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.module_c.conftest import free_port, python_env

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mcp_http_server(live_b_server):
    port = free_port()
    env = python_env(SHELTERPLAN_B_BASE_URL=live_b_server)
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent.mcp_server", "--transport", "http",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"MCP HTTP server exited early (code {proc.returncode})")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.3)
    else:  # pragma: no cover
        proc.terminate()
        raise RuntimeError("MCP HTTP server did not open its port")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


def _payload(result) -> dict:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    text = getattr(result.content[0], "text", "") if result.content else ""
    return json.loads(text) if text else {}


def test_http_transport_lists_tools_and_runs_task(mcp_http_server):
    async def scenario():
        async with streamablehttp_client(mcp_http_server) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = [tool.name for tool in (await session.list_tools()).tools]
                caps = _payload(await session.call_tool("get_capabilities", {}))
                task = _payload(await session.call_tool(
                    "submit_task", {"task_type": "assess", "session_id": "http-smoke"}))
                return tools, caps, task

    tools, caps, task = asyncio.run(scenario())
    assert set(tools) == {"get_capabilities", "submit_task", "get_job", "get_result"}
    assert caps["ok"] and caps["capabilities"]["data_status"] == "DEMO_ONLY"
    assert task["ok"] and task["task"]["verification_status"] == "PASS_DEMO"
