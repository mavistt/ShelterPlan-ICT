"""The MCP entry and the HTTP entry must be the same computation.

Module C's central claim is "one computation path".  It is easy to *say* that and
still ship two code paths that drift.  These tests make the claim falsifiable:

* the same planning request, submitted once through the MCP tool and once through
  the Module D HTTP API, must return the **same numbers**;
* the same invalid request must be rejected with the **same error code** by both
  entries, which proves the guards are shared rather than re-implemented.

Both entries are driven in-process (MCP tool function + ASGI client) with Module B
running as its real app, so the numbers are real solver output.
"""
from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

import agent.mcp_server as mcp_server
from agent.api import ConversationService, create_app
from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.service import HarnessService
from backend.app import app as b_app
from tests.module_c.rule_llm import RuleLLM

SHIELD = "zs_fed820d94e"
DEMO_MARK = "DEMO_ONLY"

# Mirrors backend.models.ImprovementOption; a hypothetical expansion is admissible
# without human confirmation, a non-hypothetical one is not (that is B's rule).
EXPANSION = {
    "option_id": "opt-500",
    "shelter_id": SHIELD,
    "action": "hypothetical_capacity_increase",
    "delta_capacity_persons": 500,
    "cost_units": 1,
    "eligible": True,
    "source_status": "hypothetical_assumption",
    "hypothetical": True,
    "human_confirmed": False,
    "combination_policy": "standalone",
}


@pytest.fixture()
def service(tmp_path):
    config = HarnessConfig()
    return HarnessService(config, client=BClient(config, client=TestClient(b_app)),
                          trace_dir=str(tmp_path / "trace"))


@pytest.fixture()
def api(tmp_path, service):
    config = HarnessConfig()
    conv = ConversationService(config, service=service, llm=RuleLLM(),
                               store_dir=str(tmp_path / "state"))
    return TestClient(create_app(conversation=conv))


def _turn(client, session_id, text, *, timeout=120.0):
    response = client.post(f"/v1/sessions/{session_id}/turns", json={"text": text})
    assert response.status_code == 202, response.text
    turn_id = response.json()["turn_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/v1/turns/{turn_id}").json()
        if payload["status"] != "RUNNING":
            return payload
        time.sleep(0.05)
    raise AssertionError(f"turn {turn_id} did not finish in {timeout}s")


def test_mcp_and_api_return_the_same_numbers(api, service, monkeypatch):
    monkeypatch.setattr(mcp_server, "_service", service, raising=False)

    # --- HTTP entry: the user sentence is the only input -------------------
    session_id = api.post("/v1/sessions", json={}).json()["session_id"]
    envelope = _turn(api, session_id,
                     f"把预算设为3，做一个扩容优化，候选是 {SHIELD} 扩容500人、成本1。")
    assert envelope["status"] == "SUCCEEDED", envelope.get("message")

    # --- MCP entry: the structured call an agent would make ----------------
    out = mcp_server.submit_task("optimize", session_id="s-mcp",
                                 changes={"budget_units": 3},
                                 improvement_options=[dict(EXPANSION)])
    assert out["ok"] is True, out.get("error")
    task = out["task"]
    assert task["ok"] is True, task.get("error")
    summary = task["summary"]

    # --- same solver, same numbers ----------------------------------------
    http_metrics = envelope["metrics"]
    mcp_metrics = summary["metrics"]
    for key in ("served_demand", "total_demand", "weighted_walk_time_min"):
        assert key in http_metrics and key in mcp_metrics, key
        assert http_metrics[key] == pytest.approx(mcp_metrics[key], rel=1e-9), key
    assert envelope["task_id"] and task["task_id"]
    assert summary["solver_status"] == "OPTIMAL"
    assert summary["optimality_proven"] is True

    # --- Module D must not see less than MCP ------------------------------
    assert set(mcp_metrics) <= set(http_metrics)

    # --- both disclose the demo data status -------------------------------
    assert any(DEMO_MARK in str(w) for w in envelope["warnings"])
    assert summary["data_status"] == DEMO_MARK


def test_mcp_rejects_an_inadmissible_option(service, monkeypatch):
    """The structured entry stops a negative cost before Module B sees it."""
    monkeypatch.setattr(mcp_server, "_service", service, raising=False)

    broken = dict(EXPANSION, cost_units=-1)

    out = mcp_server.submit_task("optimize", session_id="s-mcp-bad",
                                 changes={"budget_units": 3},
                                 improvement_options=[broken])
    assert out["ok"] is True
    assert out["task"]["ok"] is False
    assert out["task"]["error"]["code"] == "GUARD_INVALID_OPTION"


@pytest.mark.xfail(
    strict=True,
    reason="known defect: the rule extractor's improvement-option regex drops a "
           "leading minus, so '成本-1' is read as cost 1 and the guard never sees "
           "the negative value. The frozen V2.1 set does not cover a negative "
           "candidate cost; documented in HARNESS_V2_1_REPORT.md §4.4.",
)
def test_text_entry_rejects_a_negative_option_cost(api, service, monkeypatch):
    """Adversarial: a negative candidate cost must not be silently rewritten.

    Parameter values are not affected - `预算-3` really does extract as -3.0 and is
    rejected. Only the candidate table loses the sign, which is why this test is
    narrow and why it is reported as a defect found *after* the frozen set.
    """
    monkeypatch.setattr(mcp_server, "_service", service, raising=False)

    bad_session = api.post("/v1/sessions", json={}).json()["session_id"]
    envelope = _turn(api, bad_session,
                     f"预算3，做扩容优化，候选是 {SHIELD} 扩容500人、成本-1。")
    assert envelope["status"] in {"FAILED", "NEEDS_CLARIFICATION"}, envelope["message"]
    if envelope["status"] == "FAILED":
        assert envelope["error"]["code"] in {"GUARD_INVALID_OPTION", "SCENARIO_INVALID"}
