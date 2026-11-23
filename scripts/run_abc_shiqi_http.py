"""Run the real Module A -> B -> C HTTP acceptance path for Shiqi.

The script deliberately talks to the running B HTTP service through C's
Streamable HTTP MCP server.  It does not replace Module B, inject a result, or
patch an in-memory dependency.  It writes only a compact acceptance record;
the detailed C JSONL trace is written by the running service under the
configured ``SHELTERPLAN_TRACE_DIR``.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


ROOT = Path(__file__).resolve().parents[1]
DATA_VERSION = "shelterplan_module_a_v2.0-demo__shiqi"
REGION_ID = "shiqi"


def _decode_tool_result(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    content = getattr(result, "content", None) or []
    text = getattr(content[0], "text", "") if content else ""
    return json.loads(text) if text else {}


async def _call(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = _decode_tool_result(await session.call_tool(name, arguments))
    if not payload.get("ok", False):
        raise RuntimeError(f"MCP {name} failed: {payload}")
    return payload


def _shelter_ids() -> list[str]:
    path = ROOT / "data" / "processed" / "regions" / REGION_ID / "shelters.geojson"
    document = json.loads(path.read_text(encoding="utf-8"))
    return [str(row["properties"]["shelter_id"]) for row in document["features"]]


def _options(shelter_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "option_id": f"HTTP-DEMO-{index}",
            "shelter_id": shelter_id,
            "action": "capacity_adjustment_demo",
            "delta_capacity_persons": 100.0,
            "cost_units": 10.0,
            "eligible": True,
            "source_status": "HYPOTHETICAL",
            "hypothetical": True,
            "human_confirmed": True,
            "combination_policy": "stackable",
            "exclusive_group": None,
        }
        for index, shelter_id in enumerate(shelter_ids[:2], 1)
    ]


async def _run(mcp_url: str, b_url: str) -> dict[str, Any]:
    shelter_ids = _shelter_ids()
    options = _options(shelter_ids)
    result: dict[str, Any] = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "region_id": REGION_ID,
        "data_version": DATA_VERSION,
        "data_status": "DEMO_ONLY",
        "http": {"module_b": b_url, "module_c_mcp": mcp_url},
        "checks": [],
    }

    async with httpx.AsyncClient(base_url=b_url, timeout=30.0, trust_env=False) as http:
        b_health = await http.get("/healthz")
        b_health.raise_for_status()
        b_caps = await http.get("/v1/capabilities")
        b_caps.raise_for_status()
        health_doc, caps_doc = b_health.json(), b_caps.json()
        assert health_doc.get("status") == "ok", health_doc
        assert caps_doc.get("data_version") == DATA_VERSION, caps_doc
        result["checks"].append({"name": "module_b_health_and_capabilities", "status": "PASS"})

    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [tool.name for tool in (await session.list_tools()).tools]
            assert {"get_capabilities", "submit_task", "get_job", "get_result"}.issubset(tools)
            caps = await _call(session, "get_capabilities", {})
            assert caps["capabilities"]["data_version"] == DATA_VERSION, caps
            result["checks"].append({"name": "module_c_capabilities", "status": "PASS"})

            assess = await _call(session, "submit_task", {
                "task_type": "assess", "session_id": "abc-shiqi-http",
            })
            assess_task = assess["task"]
            assert assess_task["verification_status"] == "PASS_DEMO", assess_task
            assert assess_task["data_version"] == DATA_VERSION, assess_task
            result["checks"].append({
                "name": "assess", "status": "PASS",
                "result_id": assess_task.get("result_id"),
                "solver_status": assess_task.get("solver_status"),
                "verification_status": assess_task.get("verification_status"),
            })

            optimize = await _call(session, "submit_task", {
                "task_type": "optimize", "session_id": "abc-shiqi-http",
                "changes": {"budget_units": 20.0},
                "improvement_options": options,
            })
            optimize_task = optimize["task"]
            assert optimize_task["verification_status"] == "PASS_DEMO", optimize_task
            assert optimize_task["data_version"] == DATA_VERSION, optimize_task
            result["checks"].append({
                "name": "optimize_with_inherited_session", "status": "PASS",
                "result_id": optimize_task.get("result_id"),
                "solver_status": optimize_task.get("solver_status"),
                "verification_status": optimize_task.get("verification_status"),
                "scenario_id": optimize_task.get("scenario_id"),
            })

            stress = await _call(session, "submit_task", {
                "task_type": "stress_test", "session_id": "abc-shiqi-http",
                "changes": {"disabled_shelter_ids": [shelter_ids[0]]},
                "parameters": {"budget_units": 20.0},
                "improvement_options": options,
            })
            stress_task = stress["task"]
            assert stress_task["verification_status"] == "PASS_DEMO", stress_task
            result["checks"].append({
                "name": "stress_test", "status": "PASS",
                "result_id": stress_task.get("result_id"),
                "solver_status": stress_task.get("solver_status"),
                "verification_status": stress_task.get("verification_status"),
            })

            explain = await _call(session, "submit_task", {
                "task_type": "explain_result", "session_id": "abc-shiqi-http",
                "result_id": optimize_task["result_id"],
            })
            explain_task = explain["task"]
            assert explain_task["verification_status"] == "PASS_DEMO", explain_task
            result["checks"].append({
                "name": "explain_result", "status": "PASS",
                "result_id": explain_task.get("result_id"),
                "solver_status": explain_task.get("solver_status"),
                "verification_status": explain_task.get("verification_status"),
            })

            evidence = await _call(session, "get_evidence", {
                "result_id": optimize_task["result_id"], "section": "trace",
                "offset": 0, "limit": 100,
            })
            result["checks"].append({
                "name": "trace_evidence", "status": "PASS",
                "rows": len(evidence.get("evidence", {}).get("items", [])),
                "total": evidence.get("evidence", {}).get("total"),
            })

    result["status"] = "PASS"
    result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8010/mcp")
    parser.add_argument("--b-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="outputs/module_c/abc_shiqi_http_run.json")
    args = parser.parse_args()
    try:
        document = asyncio.run(_run(args.mcp_url, args.b_url))
    except Exception as exc:  # Keep a machine-readable FAIL artifact.
        document = {
            "status": "FAIL", "region_id": REGION_ID, "data_version": DATA_VERSION,
            "error": f"{exc.__class__.__name__}: {exc}",
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    output = (ROOT / args.output).resolve()
    output.relative_to(ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0 if document.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
