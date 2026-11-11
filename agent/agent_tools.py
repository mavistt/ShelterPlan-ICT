"""Tool registries for the two experiment arms.

Both arms expose the SAME four tool names over the SAME Module B service. They
differ only in the reliability machinery around them:

* harness  — session state (full parameter snapshot), guards, bounded retries,
             <=20 KB projection, claim/DEMO_ONLY gates, JSONL trace.
* baseline — raw endpoints, stateless, single attempt, depth-elided output.
             The only validation is Module B's own Pydantic contract (HTTP 422).

Both arms share ONE system prompt and the same tool schemas, so the experiment
isolates the machinery, not prompt engineering.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.errors import HarnessError
from agent.service import HarnessService

BASELINE_DEFAULTS = {"population_demand_ratio": 0.2, "max_walk_time_min": 15.0}

SHARED_SYSTEM_PROMPT = (
    "你是 ShelterPlan 应急避难场所规划助手。当前已加载试点数据（zhongshan_pilot_v0.1，状态 DEMO_ONLY）。\n"
    "用户提到“东区”“试点区域”时，直接使用已加载数据调用工具，不要询问区域或数据版本。\n"
    "收到评估/优化/比较/压力测试类请求时，直接调用相应工具执行；缺少影响结论的关键决策参数"
    "（预算、扩容候选措施）时先询问用户，不要编造数值。\n"
    "submit_task 返回 202 表示已受理而非完成：用 get_job 轮询 job 状态，SUCCEEDED 后用 "
    "get_result 按 result_id 取结果。用中文回答。"
)

# Both arms use the SAME system prompt. The experiment isolates the harness
# machinery (session merge, guards, projection, gates), not prompt engineering.
BASELINE_SYSTEM_PROMPT = SHARED_SYSTEM_PROMPT
HARNESS_SYSTEM_PROMPT = SHARED_SYSTEM_PROMPT

_SUBMIT_EXAMPLES = (
    "参数示例：optimize → changes={\"budget_units\": 1} 且 improvement_options=[{"
    "\"option_id\":\"CAP_1\",\"shelter_id\":\"zs_x\",\"delta_capacity_persons\":300,"
    "\"cost_units\":1,\"eligible\":true,\"source_status\":\"hypothetical\","
    "\"hypothetical\":true,\"human_confirmed\":false,\"combination_policy\":\"standalone\"}]；"
    "compare → parameters={\"scenarios\":[{\"population_demand_ratio\":0.1},"
    "{\"population_demand_ratio\":0.3}]}；"
    "stress_test → changes={\"disabled_shelter_ids\":[\"zs_x\"]}；"
    "sensitivity → parameters={\"parameter\":\"budget_units\",\"values\":[0,1,2,3]}"
)


def elide(value: Any, *, max_list: int = 4, max_str: int = 160, depth: int = 0) -> Any:
    """Protocol-level size cap: drop deep/long content, keep the shape."""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {key: elide(child, max_list=max_list, max_str=max_str, depth=depth + 1)
                for key, child in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        head = [elide(child, max_list=max_list, max_str=max_str, depth=depth + 1)
                for child in value[:max_list]]
        if len(value) > max_list:
            head.append(f"…[{len(value) - max_list} more items elided]")
        return head
    if isinstance(value, str) and len(value) > max_str:
        return value[:max_str] + "…"
    return value


def effective_baseline_params(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the parameters Module B will actually use for a raw request.

    B rebuilds every scenario from its config defaults and applies only the
    fields present in this request - which is exactly why a stateless agent
    silently loses untouched parameters.
    """
    effective = dict(BASELINE_DEFAULTS)
    effective["budget_units"] = None
    effective["disabled_shelter_ids"] = []
    scenario = payload.get("scenario")
    if isinstance(scenario, dict):
        for key in ("population_demand_ratio", "max_walk_time_min", "budget_units"):
            if scenario.get(key) is not None:
                effective[key] = scenario[key]
        effective["disabled_shelter_ids"] = list(scenario.get("disabled_shelter_ids") or [])
    changes = payload.get("changes") or {}
    for key in ("population_demand_ratio", "max_walk_time_min", "budget_units"):
        if changes.get(key) is not None:
            effective[key] = changes[key]
    if "disabled_shelter_ids" in changes:
        effective["disabled_shelter_ids"] = list(changes["disabled_shelter_ids"])
    for key in ("budget_units", "disabled_shelter_ids"):
        if key in (payload.get("parameters") or {}) and payload["parameters"][key] is not None:
            effective[key] = payload["parameters"][key]
    return effective


class ToolRegistry:
    def __init__(self, schemas: list[dict[str, Any]], handlers: dict[str, Callable]) -> None:
        self.schemas = schemas
        self.handlers = handlers

    def names(self) -> list[str]:
        return [schema["function"]["name"] for schema in self.schemas]

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in self.handlers:
            return {"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": f"unknown tool {name}"}}
        try:
            return {"ok": True, "result": self.handlers[name](**args)}
        except Exception as exc:  # noqa: BLE001 - surfaced to the model, then traced
            if isinstance(exc, HarnessError):
                return {"ok": False, "error": exc.to_dict()}
            return {"ok": False, "error": {"code": exc.__class__.__name__, "message": str(exc)[:400]}}


def _submit_schema(description: str) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "submit_task",
        "description": description,
        "parameters": {"type": "object", "properties": {
            "task_type": {"type": "string", "enum": ["assess", "optimize", "diagnose_intervene",
                                                     "stress_test", "compare", "sensitivity",
                                                     "explain_result"]},
            "changes": {"type": "object",
                        "description": "population_demand_ratio(0-1), max_walk_time_min(>0), "
                                       "budget_units(>=0), disabled_shelter_ids[]"},
            "parameters": {"type": "object",
                           "description": "compare: {\"scenarios\":[..2-12..]}; "
                                          "sensitivity: {\"parameter\":..., \"values\":[1-20个]}"},
            "improvement_options": {"type": "array",
                                    "description": "扩容候选（<=12）"},
            "result_id": {"type": "string",
                          "description": "仅 explain_result 需要（harness 可自动填上一条）"},
            "claim": {"type": "string", "enum": ["optimal"],
                      "description": "仅当回答将声称“已证明最优”时传入"},
        }, "required": ["task_type"]},
    }}


# --------------------------------------------------------------------------- #
# Harness arm: the same application service that backs the MCP server.
# --------------------------------------------------------------------------- #
def harness_registry(service: HarnessService, session_id: str) -> tuple[ToolRegistry, HarnessService]:
    def get_capabilities() -> dict:
        return service.capabilities()

    def submit_task(task_type: str, changes: dict | None = None, parameters: dict | None = None,
                    improvement_options: list[dict] | None = None, claim: str | None = None,
                    result_id: str | None = None) -> dict:
        if task_type == "explain_result" and not result_id:
            result_id = service.last_result_id(session_id)
        outcome = service.submit(
            task_type, session_id=session_id, changes=changes, parameters=parameters,
            improvement_options=improvement_options, result_id=result_id, claim=claim,
        )
        return {key: value for key, value in outcome.items() if key != "tool_calls"}

    def get_job(job_id: str) -> dict:
        return service.job_status(job_id)

    def get_result(result_id: str) -> dict:
        return service.result(result_id, session_id=session_id)

    schemas = [
        {"type": "function", "function": {
            "name": "get_capabilities",
            "description": "返回已注册的任务类型、求解边界和当前数据准入状态（data_status）。",
            "parameters": {"type": "object", "properties": {}},
        }},
        _submit_schema(
            "提交一个规划任务并等待完成，返回经校验的结果摘要（<=20KB）。"
            "changes 只放用户本轮明确修改的参数；未提及的参数会被自动沿用上一轮。"
            "缺预算/比例/候选时先询问用户，不要编造。" + _SUBMIT_EXAMPLES
        ),
        {"type": "function", "function": {
            "name": "get_job",
            "description": "查询异步作业状态。",
            "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}},
                           "required": ["job_id"]},
        }},
        {"type": "function", "function": {
            "name": "get_result",
            "description": "按 result_id 取回结果摘要（不是完整大文档）。",
            "parameters": {"type": "object", "properties": {"result_id": {"type": "string"}},
                           "required": ["result_id"]},
        }},
    ]
    return ToolRegistry(schemas, {
        "get_capabilities": get_capabilities,
        "submit_task": submit_task,
        "get_job": get_job,
        "get_result": get_result,
    }), service


# --------------------------------------------------------------------------- #
# Baseline arm: raw endpoints, stateless, single attempt, elided output.
# --------------------------------------------------------------------------- #
def baseline_registry(client: BClient, config: HarnessConfig, *,
                      tool_budget_chars: int = 12000
                      ) -> tuple[ToolRegistry, dict[str, Any]]:
    del tool_budget_chars  # size limiting is handled by elide()
    state: dict[str, Any] = {"requests": [], "results": {}}

    def get_capabilities() -> dict:
        return client.get_capabilities()

    def submit_task(task_type: str, changes: dict | None = None, parameters: dict | None = None,
                    improvement_options: list[dict] | None = None,
                    data_version: str | None = None,
                    scenario: dict | None = None) -> dict:
        payload: dict[str, Any] = {
            "task_type": task_type,
            "analysis_mode": "demo",
            "changes": dict(changes or {}),
            "parameters": dict(parameters or {}),
        }
        if improvement_options:
            payload["improvement_options"] = improvement_options
        if data_version:
            payload["data_version"] = data_version
        if scenario:
            payload["scenario"] = scenario
        state["requests"].append({
            "payload": payload,
            "effective_params": effective_baseline_params(payload),
            "completed": False,
        })
        return client.submit(payload)

    def get_job(job_id: str) -> dict:
        job = client.get_job(job_id)
        if job.get("status") in {"QUEUED", "RUNNING"}:
            # Protocol-level guidance so a raw client knows polling is expected.
            job = {**job, "hint": "job 尚未完成，请稍候后再次调用 get_job；SUCCEEDED 后调用 get_result。"}
        return job

    def get_result(result_id: str) -> dict:
        document = client.get_result(result_id)
        state["results"][result_id] = document
        if state["requests"]:
            state["requests"][-1]["completed"] = document.get(
                "verification_status") in {"PASS_DEMO", "PASS_VERIFIED"}
        return json.loads(json.dumps(elide(document)))  # depth-elided raw document

    schemas = [
        {"type": "function", "function": {
            "name": "get_capabilities",
            "description": "List Module B planning capabilities.",
            "parameters": {"type": "object", "properties": {}},
        }},
        _submit_schema(
            "Submit a planning task. HTTP 202 means accepted, not finished: "
            "poll get_job, then fetch get_result. " + _SUBMIT_EXAMPLES
        ),
        {"type": "function", "function": {
            "name": "get_job",
            "description": "Fetch job status by id.",
            "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}},
                           "required": ["job_id"]},
        }},
        {"type": "function", "function": {
            "name": "get_result",
            "description": "Fetch the result document by result id.",
            "parameters": {"type": "object", "properties": {"result_id": {"type": "string"}},
                           "required": ["result_id"]},
        }},
    ]
    registry = ToolRegistry(schemas, {
        "get_capabilities": get_capabilities,
        "submit_task": submit_task,
        "get_job": get_job,
        "get_result": get_result,
    })
    return registry, state
