"""MCP end-to-end tests with the official SDK client (stdio transport).

A real uvicorn Module B process is started by the `live_b_server` fixture; the
MCP server is a real subprocess. Nothing is mocked.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TOOLS = {"get_capabilities", "submit_task", "get_job", "get_result"}


def _payload(result) -> dict:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    text = getattr(result.content[0], "text", "") if result.content else ""
    return json.loads(text) if text else {}


def _run(coro):
    return asyncio.run(coro)


def _server_params(env: dict[str, str]) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent.mcp_server", "--transport", "stdio"],
        env=env, cwd=str(ROOT),
    )


def test_stdio_discovers_exactly_four_coarse_tools(mcp_env):
    async def scenario():
        async with stdio_client(_server_params(mcp_env)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                return [tool.name for tool in tools.tools]

    names = _run(scenario())
    assert set(names) == EXPECTED_TOOLS
    assert len(names) == 4  # the 12 atomic skills are not exposed


def test_stdio_capabilities_report_demo_admission(mcp_env):
    async def scenario():
        async with stdio_client(_server_params(mcp_env)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return _payload(await session.call_tool("get_capabilities", {}))

    payload = _run(scenario())
    assert payload["ok"] is True
    caps = payload["capabilities"]
    assert caps["data_status"] == "DEMO_ONLY"
    assert caps["admission"]["verified_mode_allowed"] is False
    assert caps["tool_contract"]["atomic_skills_exposed"] is False


def test_stdio_runs_a_real_assessment(mcp_env):
    async def scenario():
        async with stdio_client(_server_params(mcp_env)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return _payload(await session.call_tool(
                    "submit_task", {"task_type": "assess", "session_id": "mcp-smoke"}))

    payload = _run(scenario())
    assert payload["ok"] is True, payload
    task = payload["task"]
    assert task["verification_status"] == "PASS_DEMO"
    assert task["data_status"] == "DEMO_ONLY"
    assert task["summary_bytes"] <= 20 * 1024
    assert task["summary"]["scenario_id"] == task["scenario"]["scenario_id"]


def test_stdio_refuses_missing_budget(mcp_env):
    async def scenario():
        async with stdio_client(_server_params(mcp_env)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return _payload(await session.call_tool(
                    "submit_task", {"task_type": "optimize", "session_id": "mcp-guard"}))

    payload = _run(scenario())
    assert payload["ok"] is True          # the tool call itself succeeded
    task = payload["task"]
    assert task["ok"] is False
    assert task["error"]["code"] == "GUARD_MISSING_PARAM"
    assert task["tool_calls"] == []
