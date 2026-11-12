"""Application API tests (in-process ASGI).

These exercise the contract Module D consumes: five statuses, the identifier
set, structured metrics, clarification payloads, cancellation, session
isolation, and restart recovery. Module B runs as its real in-process app.
"""
from __future__ import annotations

import json
import time

import pytest
from starlette.testclient import TestClient

from agent.api import ConversationService, create_app
from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.service import HarnessService
from backend.app import app as b_app
from tests.module_c.rule_llm import RuleLLM

SHIELD = "zs_fed820d94e"
REQUIRED_KEYS = {"turn_id", "session_id", "status", "scenario_id", "task_id", "job_id",
                 "result_id", "data_version", "data_status", "metrics", "map",
                 "warnings", "message", "clarification", "error", "timings", "llm"}


def build(tmp_path, **kwargs):
    config = HarnessConfig()
    service = HarnessService(config, client=BClient(config, client=TestClient(b_app)),
                             trace_dir=str(tmp_path / "trace"))
    conv = ConversationService(config, service=service, llm=RuleLLM(),
                               store_dir=str(tmp_path / "state"), **kwargs)
    return conv, TestClient(create_app(conversation=conv))


def turn(client, session_id, text, *, timeout=60.0, **extra):
    response = client.post(f"/v1/sessions/{session_id}/turns",
                           json={"text": text, **extra})
    assert response.status_code == 202, response.text
    turn_id = response.json()["turn_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/v1/turns/{turn_id}").json()
        if payload["status"] != "RUNNING":
            return payload
        time.sleep(0.05)
    raise AssertionError(f"turn {turn_id} did not finish in {timeout}s")


# --------------------------------------------------------------------------- #
def test_health_and_capabilities(tmp_path):
    _conv, client = build(tmp_path)
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["data_status"] == "DEMO_ONLY"
    capabilities = client.get("/v1/capabilities").json()
    assert set(capabilities["statuses"]) == {
        "NEEDS_CLARIFICATION", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}
    assert "assess" in capabilities["task_types"]


def test_clarification_resume_and_result_envelope(tmp_path):
    _conv, client = build(tmp_path)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]

    first = turn(client, session_id, "帮我做个扩容优化。")
    assert first["status"] == "NEEDS_CLARIFICATION"
    assert first["clarification"]["fields"] == ["budget_units"]
    assert first["result_id"] is None
    assert REQUIRED_KEYS <= set(first)

    second = turn(client, session_id, "预算是2。")
    assert second["status"] == "NEEDS_CLARIFICATION"
    assert second["clarification"]["fields"] == ["improvement_options"]

    third = turn(client, session_id, f"{SHIELD} 扩容 300 人，成本 1。")
    assert third["status"] == "SUCCEEDED", third["message"]
    assert third["resumed_from_clarification"] is True
    assert third["result_id"]
    assert third["scenario_id"]
    assert third["data_version"] == "zhongshan_pilot_v0.1"
    assert third["data_status"] == "DEMO_ONLY"
    assert third["metrics"]["served_demand"] > 0
    assert third["llm"]["calls"] >= 0
    assert third["timings"]["run_ms"] is not None
    assert any("DEMO_ONLY" in warning for warning in third["warnings"])

    view = client.get(f"/v1/sessions/{session_id}").json()
    assert view["params"]["budget_units"] == 2
    assert third["result_id"] in view["result_ids"]

    layers = client.get(f"/v1/results/{third['result_id']}/layers",
                        params={"session_id": session_id})
    assert layers.status_code == 200, layers.text
    body = layers.json()
    assert body["layers"][0]["rows"]
    assert body["layers"][0]["geometry"] is None  # honest: B has no geometry


def test_cancel_by_chat_returns_cancelled(tmp_path):
    _conv, client = build(tmp_path)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    turn(client, session_id, "帮我做个扩容优化。")
    cancelled = turn(client, session_id, "算了，不做了。")
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["result_id"] is None
    assert "取消" in cancelled["message"]
    assert client.get(f"/v1/sessions/{session_id}").json()["pending_clarification"] is None


def test_cancel_running_turn_reports_too_late(tmp_path):
    _conv, client = build(tmp_path)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    # A completed turn cannot be cancelled.
    done = turn(client, session_id, "评估现状。")
    response = client.post(f"/v1/turns/{done['turn_id']}/cancel")
    assert response.status_code == 409


def test_idempotent_client_turn_id(tmp_path):
    _conv, client = build(tmp_path)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    first = client.post(f"/v1/sessions/{session_id}/turns",
                        json={"text": "评估现状。", "client_turn_id": "abc"}).json()
    second = client.post(f"/v1/sessions/{session_id}/turns",
                         json={"text": "评估现状。", "client_turn_id": "abc"}).json()
    assert first["turn_id"] == second["turn_id"]


def test_unknown_ids_are_404(tmp_path):
    _conv, client = build(tmp_path)
    assert client.get("/v1/sessions/nope").status_code == 404
    assert client.get("/v1/turns/nope").status_code == 404
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    assert client.get("/v1/turns/nope").status_code == 404
    assert client.post("/v1/sessions/nope/turns", json={"text": "hi"}).status_code == 404
    assert session_id


def test_foreign_result_is_refused(tmp_path):
    _conv, client = build(tmp_path)
    a = client.post("/v1/sessions", json={}).json()["session_id"]
    b = client.post("/v1/sessions", json={}).json()["session_id"]
    done = turn(client, a, "评估现状。")
    response = client.get(f"/v1/results/{done['result_id']}/layers",
                          params={"session_id": b})
    assert response.status_code == 403


def test_reset_clears_session_state(tmp_path):
    _conv, client = build(tmp_path)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    done = turn(client, session_id, "需求比例0.3，评估现状。")
    assert done["status"] == "SUCCEEDED"
    assert client.get(f"/v1/sessions/{session_id}").json()["params"]["population_demand_ratio"] == 0.3

    view = client.post(f"/v1/sessions/{session_id}/reset").json()
    assert view["params"]["population_demand_ratio"] == 0.2
    assert view["result_ids"] == []
    assert view["lineage"] and len(view["lineage"]) == 1


def test_restart_restores_params_results_and_turns(tmp_path):
    conv, client = build(tmp_path)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    done = turn(client, session_id, "需求比例0.3，评估现状。")
    assert done["status"] == "SUCCEEDED"
    conv.close()

    config = HarnessConfig()
    service = HarnessService(config, client=BClient(config, client=TestClient(b_app)),
                             trace_dir=str(tmp_path / "trace2"))
    revived = ConversationService(config, service=service, llm=RuleLLM(),
                                  store_dir=str(tmp_path / "state"))
    client2 = TestClient(create_app(conversation=revived))
    revived.create_session(session_id)

    view = client2.get(f"/v1/sessions/{session_id}").json()
    assert view["params"]["population_demand_ratio"] == 0.3
    assert done["result_id"] in view["result_ids"]
    # The turn envelope survives too, so a late poll is not a 404.
    replayed = client2.get(f"/v1/turns/{done['turn_id']}").json()
    assert replayed["status"] == "SUCCEEDED"
    assert replayed["result_id"] == done["result_id"]


def test_running_turn_is_marked_failed_after_restart(tmp_path):
    conv, client = build(tmp_path)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    conv.close()

    state_dir = tmp_path / "state" / "turns"
    zombie = {
        "turn_id": "t-zombie", "session_id": session_id, "status": "RUNNING",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "user_text": "评估现状。", "metrics": {}, "warnings": [], "timings": {},
    }
    (state_dir / "t-zombie.json").write_text(json.dumps(zombie), encoding="utf-8")

    config = HarnessConfig()
    service = HarnessService(config, client=BClient(config, client=TestClient(b_app)),
                             trace_dir=str(tmp_path / "trace2"))
    revived = ConversationService(config, service=service, llm=RuleLLM(),
                                  store_dir=str(tmp_path / "state"))
    assert revived.interrupted >= 1
    client2 = TestClient(create_app(conversation=revived))
    payload = client2.get("/v1/turns/t-zombie").json()
    assert payload["status"] == "FAILED"
    assert payload["error"]["code"] == "INTERRUPTED_BY_RESTART"


def test_failed_turn_exposes_error_envelope(tmp_path):
    _conv, client = build(tmp_path)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    # Unknown shelter: Module B rejects it, the API must explain, not invent.
    failed = turn(client, session_id, "停用 zs_deadbeef99，做压力测试。")
    assert failed["status"] == "FAILED"
    assert failed["error"]["code"]
    assert failed["result_id"] is None
    assert "未执行" in failed["message"] or "不能" in failed["message"]
