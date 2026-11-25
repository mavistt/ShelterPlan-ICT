"""The structured entry point is opt-in: the default MCP contract stays at four
tools, and `handle_request` appears only when explicitly enabled."""
from __future__ import annotations

import importlib
import os

import anyio
import pytest


def _tool_names(module) -> list[str]:
    return [tool.name for tool in anyio.run(module.mcp.list_tools)]


def test_structured_tool_is_opt_in(monkeypatch):
    import agent.mcp_server as server

    monkeypatch.delenv("SHELTERPLAN_MCP_STRUCTURED", raising=False)
    importlib.reload(server)
    default_names = _tool_names(server)
    assert "handle_request" not in default_names
    assert {"get_capabilities", "submit_task", "get_job", "get_result"} <= set(default_names)

    monkeypatch.setenv("SHELTERPLAN_MCP_STRUCTURED", "1")
    enabled = importlib.reload(server)
    enabled_names = _tool_names(enabled)
    assert "handle_request" in enabled_names
    assert {"get_capabilities", "submit_task", "get_job", "get_result"} <= set(enabled_names)

    monkeypatch.delenv("SHELTERPLAN_MCP_STRUCTURED", raising=False)
    importlib.reload(server)
    assert "handle_request" not in _tool_names(server)


def test_runner_cache_is_per_session(monkeypatch):
    monkeypatch.setenv("SHELTERPLAN_MCP_STRUCTURED", "1")
    import agent.mcp_server as server
    importlib.reload(server)

    first = server._structured_runner("s-a")
    again = server._structured_runner("s-a")
    other = server._structured_runner("s-b")
    assert first is again
    assert first is not other
    assert first.session_id == "s-a"

    server._structured_runners.clear()
    monkeypatch.delenv("SHELTERPLAN_MCP_STRUCTURED", raising=False)
    importlib.reload(server)
