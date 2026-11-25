"""MCP server exposing Module B through four coarse tools.

Run locally:
    python -m agent.mcp_server --transport stdio
    python -m agent.mcp_server --transport http --port 8010

Tool discovery and invocation are exercised by `tests/module_c/test_mcp_stdio.py`
and `test_mcp_http.py` with the official SDK client (no hand-rolled protocol).

The twelve internal Module B skills are deliberately NOT exposed: they are
stages of one deterministic computation, not agent decisions.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent.config import HarnessConfig
from agent.errors import HarnessError
from agent.service import HarnessService

INSTRUCTIONS = (
    "ShelterPlan planning tools. Every number must come from these tools. "
    "The loaded Module A source is DEMO_ONLY: plans may be produced for demo and "
    "algorithm evaluation, but must never be described as field-verified emergency "
    "guidance. Call get_capabilities first, then submit_task and poll get_job until "
    "SUCCEEDED or FAILED. FEASIBLE is a valid result but is not proven optimal; only "
    "optimality_proven=true allows the phrase 'proven optimal'. When a parameter is "
    "missing (budget, demand ratio, walk threshold, candidate measures), ask the user "
    "instead of inventing a value."
)

mcp = FastMCP("ShelterPlan", instructions=INSTRUCTIONS)
_service: HarnessService | None = None
_llm_adapter: Any | None = None
_structured_runners: dict[str, Any] = {}
_structured_lock = threading.RLock()


def get_service() -> HarnessService:
    global _service
    if _service is None:
        _service = HarnessService(HarnessConfig.from_env())
    return _service


def _get_llm():
    from agent.llm_adapter import LLMAdapter, LLMConfig

    global _llm_adapter
    with _structured_lock:
        if _llm_adapter is None:
            _llm_adapter = LLMAdapter(LLMConfig.from_env())
        return _llm_adapter


def _error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HarnessError):
        return {"ok": False, "error": exc.to_dict()}
    return {"ok": False, "error": {
        "code": "INTERNAL_ERROR", "message": f"{exc.__class__.__name__}: {exc}",
        "details": {}, "retryable": False, "user_action": None}}


@mcp.tool()
def get_capabilities() -> dict[str, Any]:
    """Return the registered task types, solver bounds and current data admission status.

    Call this before planning so you know which task types exist and whether the
    loaded source is demo-only.
    """
    try:
        return {"ok": True, "capabilities": get_service().capabilities()}
    except Exception as exc:  # noqa: BLE001
        return _error_payload(exc)


@mcp.tool()
def submit_task(
    task_type: str,
    session_id: str = "default",
    changes: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    improvement_options: list[dict[str, Any]] | None = None,
    result_id: str | None = None,
    claim: str | None = None,
    user_goal: str | None = None,
) -> dict[str, Any]:
    """Run a registered planning task or disaster-recovery workflow.

    Args:
        task_type: assess | optimize | diagnose_intervene | stress_test | compare | sensitivity | explain_result | disaster_recovery
        session_id: conversation id; reuse it so scenario parameters carry over
        changes: only the parameters the user explicitly changed
            (population_demand_ratio, max_walk_time_min, budget_units, disabled_shelter_ids)
        parameters: task-specific inputs (compare: {"scenarios": [...]}, sensitivity:
            {"parameter": ..., "values": [...]})
        improvement_options: expansion candidates for optimize/stress tasks
        result_id: required only for explain_result
        claim: pass "optimal" only if the answer will say "proven optimal"
        user_goal: original request text for disaster_recovery traceability

    Returns a projected result (<=20 KB) including result_id, scenario_id,
    solver_status, optimality_proven, verification_status and warnings.
    """
    try:
        return {"ok": True, "task": get_service().submit(
            task_type, session_id=session_id, changes=changes, parameters=parameters,
            improvement_options=improvement_options, result_id=result_id, claim=claim,
            user_goal=user_goal)}
    except Exception as exc:  # noqa: BLE001
        return _error_payload(exc)


@mcp.tool()
def get_job(job_id: str) -> dict[str, Any]:
    """Return the current status of an asynchronous Module B job."""
    try:
        return {"ok": True, "job": get_service().job_status(job_id)}
    except Exception as exc:  # noqa: BLE001
        return _error_payload(exc)


@mcp.tool()
def get_result(result_id: str, session_id: str = "default") -> dict[str, Any]:
    """Return the projected summary of a stored result (never the 5-11 MB document)."""
    try:
        return {"ok": True, "result": get_service().result(result_id, session_id=session_id)}
    except Exception as exc:  # noqa: BLE001
        return _error_payload(exc)


def _structured_runner(session_id: str):
    """One StructuredRunner per session so multi-turn memory survives MCP calls."""
    from agent.regions import RegionCatalog
    from agent.structured_runner import StructuredRunner
    with _structured_lock:
        runner = _structured_runners.get(session_id)
        if runner is None:
            service = get_service()
            config = service.config
            region_id = service.defaults.region_id
            if region_id and service.session_object(session_id).valid_shelter_ids is None:
                try:
                    catalog = RegionCatalog(config.project_root, config=config)
                    info = catalog.get_region(region_id)
                    valid_shelters = (
                        catalog.get_shelter_ids(info.region_id)
                        if info.source_mode == "module_a_v2"
                        else None
                    )
                    service.configure_session(
                        session_id,
                        region_id=info.region_id,
                        data_version=info.data_version,
                        valid_shelter_ids=valid_shelters,
                    )
                except (KeyError, OSError, ValueError):
                    pass
            runner = StructuredRunner(
                "harness_opt", service, _get_llm(), config,
                session_id=session_id,
                trace=service.session_trace(session_id),
                dialogue_enabled=True)
            _structured_runners[session_id] = runner
        return runner


if os.environ.get("SHELTERPLAN_MCP_STRUCTURED") == "1":
    @mcp.tool()
    def handle_request(text: str, session_id: str = "default") -> dict[str, Any]:
        """Turn one natural-language request into a verified plan, deterministically.

        This is the structured entry point: intent extraction -> fixed routing ->
        Module B -> answer composed from bound result values. Parameters the user
        did not mention are inherited from this session; missing ones produce a
        clarification instead of a guess. Disabled by default (`SHELTERPLAN_MCP_STRUCTURED=1`).

        Args:
            text: the user's request in Chinese
            session_id: conversation id; reuse it for multi-turn parameter inheritance
        """
        try:
            runner = _structured_runner(session_id)
            record = runner.run_turn(text)
            run = runner.last_run
            plan = record.plan or (record.control.plan if record.control else None)
            error_info = None
            if record.status == "FAILED" and record.control and record.control.error:
                error_info = record.control.error
            elif run and run.error:
                error_info = run.error
            extract_modes = [
                row.get("mode")
                for row in (getattr(run, "intents", None) or [])
                if isinstance(row, dict) and row.get("mode")
            ]
            return {
                "ok": True,
                "status": record.status,
                "answer": record.text,
                "intent": (record.intent or {}).get("intent"),
                "task_type": plan.task_type if plan else None,
                "action": plan.action if plan else None,
                "clarification_fields": list(record.clarification_fields),
                "result_id": (record.bindings or {}).get("result_id"),
                "resumed_from_clarification": record.resumed,
                "llm_calls": len(run.llm_calls) if run else 0,
                "extract_modes": extract_modes,
                "fallback_used": "rules_fallback" in extract_modes,
                "error": error_info,
            }
        except Exception as exc:  # noqa: BLE001
            return _error_payload(exc)


if os.environ.get("SHELTERPLAN_MCP_EXPOSE_EVIDENCE") == "1":
    @mcp.tool()
    def get_evidence(result_id: str, section: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """Read a bounded page of raw evidence (assignments, deficits, checks, trace).

        Disabled by default; it exists for audits, not for model reasoning.
        """
        try:
            return {"ok": True, "evidence": get_service().evidence(
                result_id, section=section, offset=offset, limit=limit)}
        except Exception as exc:  # noqa: BLE001
            return _error_payload(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.mcp_server")
    parser.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio")
    parser.add_argument("--host", default=os.environ.get("SHELTERPLAN_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SHELTERPLAN_MCP_PORT", "8010")))
    parser.add_argument("--list-tools", action="store_true", help="Print the tool contract and exit.")
    args = parser.parse_args(argv)

    if args.list_tools:
        import anyio
        tools = anyio.run(mcp.list_tools)
        print(json.dumps([{"name": tool.name, "description": tool.description,
                           "inputSchema": tool.inputSchema} for tool in tools],
                         ensure_ascii=False, indent=2))
        return 0

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http" if args.transport == "http" else "sse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
