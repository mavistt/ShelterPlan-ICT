"""Deterministic scripted LLM for offline agent-loop tests.

It implements the same surface as agent.llm_adapter.LLMAdapter, so
AgentRunner runs unchanged against it. No model is involved: these tests pin
down the LOOP's behaviour (session merge, gates, tool protocol), never model
quality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScriptedResponse:
    content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 1.0

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)

    @property
    def raw_message(self) -> dict[str, Any]:
        """OpenAI wire format, identical to what LLMAdapter returns."""
        import json
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [{
                "id": call["id"], "type": "function",
                "function": {"name": call["name"],
                             "arguments": json.dumps(call["arguments"], ensure_ascii=False)},
            } for call in self.tool_calls]
        return message


def tool_call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"id": f"call_{name}", "name": name, "arguments": dict(arguments)}


def json_response(payload: str, **kwargs: Any) -> ScriptedResponse:
    return ScriptedResponse(content=payload, finish_reason="stop", usage=ok_usage(), **kwargs)


def ok_usage(prompt: int = 10, completion: int = 5) -> dict[str, int]:
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


class ScriptedLLM:
    """Replays a queue of responses; records every conversation it saw."""

    def __init__(self, responses: list[ScriptedResponse]) -> None:
        self.responses = list(responses)
        self.conversations: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], *, tools: Any = None,
             max_tokens: int | None = None,
             response_format: dict[str, Any] | None = None,
             reasoning_effort: str | None = None) -> ScriptedResponse:
        self.conversations.append(json_copy(messages))
        if not self.responses:
            raise AssertionError("ScriptedLLM ran out of responses")
        return self.responses.pop(0)


def json_copy(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import json
    return json.loads(json.dumps(messages, ensure_ascii=False, default=str))
