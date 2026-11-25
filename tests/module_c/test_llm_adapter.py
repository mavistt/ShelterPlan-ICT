"""LLM adapter contract tests against a stub OpenAI-compatible server."""
from __future__ import annotations

import json
from typing import Any
from typing import Any

import httpx
import pytest

from agent.config import HarnessConfig  # noqa: F401  (keeps import path stable)
from agent.errors import HarnessError
from agent.llm_adapter import LLMAdapter, LLMConfig


def stub_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    marker = body["messages"][0]["content"] if body.get("messages") else ""
    if marker == "__500__":
        return httpx.Response(500, text="boom")
    if marker == "__422__":
        return httpx.Response(422, text="invalid")
    if marker == "__junk__":
        return httpx.Response(200, content=b"not-json")
    payload = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None if body.get("tools") else "好的",
                "tool_calls": ([{
                    "id": "call_1", "type": "function",
                    "function": {"name": "submit_task",
                                 "arguments": "{\"task_type\": \"assess\"}"}
                }] if body.get("tools") else []),
            },
            "finish_reason": "tool_calls" if body.get("tools") else "stop",
        }],
        "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
    }
    return httpx.Response(200, json=payload)


@pytest.fixture()
def adapter() -> LLMAdapter:
    config = LLMConfig(base_url="http://stub/v1", model="stub-model", temperature=0.0)
    return LLMAdapter(config, transport=httpx.MockTransport(stub_handler))


def test_tool_call_response_is_parsed(adapter: LLMAdapter):
    result = adapter.chat([{"role": "user", "content": "评估"}], tools=[{"type": "function"}])
    assert result.wants_tool
    assert result.tool_calls[0]["name"] == "submit_task"
    assert result.tool_calls[0]["arguments"] == {"task_type": "assess"}
    assert result.usage["total_tokens"] == 46


def test_plain_response_and_token_accounting(adapter: LLMAdapter):
    result = adapter.chat([{"role": "user", "content": "你好"}])
    assert result.content == "好的"
    assert not result.wants_tool
    adapter.chat([{"role": "user", "content": "你好"}])
    assert adapter.total_prompt_tokens == 24
    assert adapter.total_completion_tokens == 68


def test_reasoning_effort_is_omitted_when_unset():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "x"},
                                                      "finish_reason": "stop"}]})

    config = LLMConfig(base_url="http://stub/v1", model="m", reasoning_effort=None)
    LLMAdapter(config, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "1"}])
    assert "reasoning_effort" not in captured

    config = LLMConfig(base_url="http://stub/v1", model="m", reasoning_effort="low")
    LLMAdapter(config, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "1"}])
    assert captured["reasoning_effort"] == "low"


def test_http_4xx_raises_structured_error(adapter: LLMAdapter):
    with pytest.raises(HarnessError) as excinfo:
        adapter.chat([{"role": "user", "content": "__422__"}])
    assert "HTTP 422" in excinfo.value.message


def test_non_json_body_raises(adapter: LLMAdapter):
    with pytest.raises(HarnessError):
        adapter.chat([{"role": "user", "content": "__junk__"}])


def test_server_error_is_retried_once_then_raises():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    config = LLMConfig(base_url="http://stub/v1", model="m", max_transport_retries=1,
                       retry_backoff_s=0.0)
    with pytest.raises(HarnessError):
        LLMAdapter(config, transport=httpx.MockTransport(handler)).chat([{"role": "user", "content": "__500__"}])
    assert calls["n"] == 2
