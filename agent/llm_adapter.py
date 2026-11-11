"""OpenAI-compatible LLM adapter.

Uses the Chat Completions API with native tool calling. Configured entirely
through environment variables so no provider is hard-coded and no credential is
stored in the repository:

    SHELTERPLAN_LLM_BASE_URL   e.g. http://127.0.0.1:11434/v1   (Ollama)
    SHELTERPLAN_LLM_MODEL      e.g. gemma4:e2b
    SHELTERPLAN_LLM_API_KEY    optional; some gateways require a placeholder
    SHELTERPLAN_LLM_TEMPERATURE default 0 (deterministic experiments)
    SHELTERPLAN_LLM_TIMEOUT_S  default 180
    SHELTERPLAN_LLM_MAX_TOKENS default 700

The adapter is provider-agnostic: anything that speaks the OpenAI chat
protocol works. It performs no retries on 4xx, one retry on transport errors
and 5xx, and always returns token usage when the server provides it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import time
from typing import Any

import httpx

from agent.errors import HarnessError


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class LLMConfig:
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "gemma4:e2b"
    api_key: str | None = None
    temperature: float = 0.0
    timeout_s: float = 180.0
    max_tokens: int = 2400
    max_transport_retries: int = 1
    retry_backoff_s: float = 1.0
    # Thinking models (e.g. gemma4 via Ollama) spend tokens on hidden reasoning
    # before the visible answer. Leave this unset (None) to keep the server
    # default; small models here actually NEED reasoning to call tools well.
    # Set SHELTERPLAN_LLM_REASONING_EFFORT=none to try disabling it.
    reasoning_effort: str | None = None

    @classmethod
    def from_env(cls) -> "LLMConfig":
        effort = os.environ.get("SHELTERPLAN_LLM_REASONING_EFFORT") or None
        return cls(
            base_url=_env("SHELTERPLAN_LLM_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/"),
            model=_env("SHELTERPLAN_LLM_MODEL", "gemma4:e2b"),
            api_key=os.environ.get("SHELTERPLAN_LLM_API_KEY"),
            temperature=_env_float("SHELTERPLAN_LLM_TEMPERATURE", 0.0),
            timeout_s=_env_float("SHELTERPLAN_LLM_TIMEOUT_S", 180.0),
            max_tokens=_env_int("SHELTERPLAN_LLM_MAX_TOKENS", 2400),
            reasoning_effort=(effort or None),
        )


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    raw_message: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)


class LLMAdapter:
    """Minimal OpenAI-compatible client. No SDK dependency, no hidden retries."""

    def __init__(self, config: LLMConfig | None = None, *, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config or LLMConfig.from_env()
        self._client = httpx.Client(
            base_url=self.config.base_url,
            trust_env=False,  # loopback model servers must bypass any proxy
            timeout=httpx.Timeout(self.config.timeout_s, connect=10.0),
            transport=transport,
        )
        self.call_count = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ #
    def chat(self, messages: list[dict[str, Any]], *,
             tools: list[dict[str, Any]] | None = None,
             max_tokens: int | None = None) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.config.max_transport_retries + 1):
            try:
                self.call_count += 1
                response = self._client.post("/chat/completions", json=payload, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt >= self.config.max_transport_retries:
                    raise HarnessError(
                        f"LLM endpoint unreachable: {exc.__class__.__name__}",
                        details={"base_url": self.config.base_url, "model": self.config.model},
                    ) from exc
                time.sleep(self.config.retry_backoff_s)
                continue
            if response.status_code >= 500 and attempt < self.config.max_transport_retries:
                last_error = HarnessError(f"LLM endpoint returned HTTP {response.status_code}")
                time.sleep(self.config.retry_backoff_s)
                continue
            return self._decode(response, started)

        raise HarnessError("LLM request failed after retries", details={"error": str(last_error)})

    def _decode(self, response: httpx.Response, started: float) -> ChatResult:
        latency = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            raise HarnessError(
                f"LLM endpoint returned HTTP {response.status_code}",
                details={"body": response.text[:400], "model": self.config.model},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise HarnessError("LLM endpoint returned non-JSON body") from exc
        choices = body.get("choices") or []
        if not choices:
            raise HarnessError("LLM response contained no choices", details={"body": body})
        message = choices[0].get("message") or {}
        usage = body.get("usage") or {}
        self.total_prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.total_completion_tokens += int(usage.get("completion_tokens") or 0)
        tool_calls = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            tool_calls.append({
                "id": raw.get("id") or f"call_{len(tool_calls)}",
                "name": function.get("name"),
                "arguments": _parse_arguments(function.get("arguments")),
            })
        return ChatResult(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason"),
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
            latency_ms=latency,
            raw_message=message,
        )


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Arguments arrive as a JSON string in the OpenAI protocol."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json_loads(raw)
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    except ValueError:
        return {"_raw": str(raw)}


def json_loads(text: str) -> Any:
    import json
    return json.loads(text)
