"""A model *substitute* for deterministic loop tests.

This is not an answer fixture. It performs the same job a small model does -
read the user's sentence and emit an intent envelope - by calling the project's
own conservative rule extractor. It never sees a case id, an expected value or
a grading rule, and it cannot fabricate a planning result: the planner still has
to accept the envelope and Module B still has to compute.

It is deliberately weaker than the real model, so a test that passes here tests
the loop, not the model.
"""
from __future__ import annotations

import json
from typing import Any

from agent.intent.extractor import rule_extract
from tests.module_c.scripted_llm import ScriptedResponse, ok_usage


class RuleLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.conversations: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> ScriptedResponse:
        self.calls += 1
        self.conversations.append(messages)
        text = messages[-1].get("content") or ""
        user = text.split("用户：")[-1].strip() if "用户：" in text else text
        payload = rule_extract(user).to_dict()
        return ScriptedResponse(content=json.dumps(payload, ensure_ascii=False),
                                finish_reason="stop", usage=ok_usage())

    def close(self) -> None:
        return None
