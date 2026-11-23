"""Regression tests for ABC Shiqi v2 integration fixes."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent import mcp_server
from agent.api import ConversationService, create_app
from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.errors import HarnessError
from agent.llm_adapter import ChatResult
from agent.numeric_audit import check_citations
from agent.service import HarnessService
from agent.workflow.answer import compose_answer
from agent.workflow.controller import ControlResult
from agent.workflow.router import RoutePlan


class FailingLLM:
    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        raise HarnessError("Simulated LLM outage")


class SingleJsonLLM:
    def __init__(self, payload_text: str) -> None:
        self.payload_text = payload_text

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        return ChatResult(
            content=self.payload_text,
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            latency_ms=10.0,
        )


def _poll_turn(client: TestClient, turn_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        doc = client.get(f"/v1/turns/{turn_id}").json()
        if doc["status"] != "RUNNING":
            return doc
        time.sleep(0.05)
    raise AssertionError(f"turn {turn_id} did not settle")


def test_compose_answer_renders_monotonicity_without_numeric_audit_false_positive():
    params = ScenarioParams(0.2, 20.0, None, (), "shelterplan_module_a_v2.0-demo__shiqi", "demo", "shiqi")
    mono_pass = {
        "status": "PASS",
        "parameter": "max_walk_time_min",
        "relation": "nondecreasing",
        "reason": "pair_monotonic",
        "pairs_checked": 1,
        "violations": [],
    }
    ctrl = ControlResult(
        status="ok",
        plan=RoutePlan(action="task", task_type="assess"),
        summary={"metrics": {"served_demand": 13928.39, "total_demand": 120343.36}},
        bindings={
            "task_type": "assess",
            "result_id": "r-1",
            "scenario_id": "s-1",
            "data_status": "DEMO_ONLY",
            "solver_status": "OPTIMAL",
            "optimality_proven": True,
            "verification_status": "PASS_DEMO",
            "served_demand": 13928.39,
            "total_demand": 120343.36,
            "weighted_walk_time_min": 9.97,
            "coverage_ratio": 13928.39 / 120343.36,
            "monotonicity_check": mono_pass,
        },
    )
    ans = compose_answer(ctrl, params=params)
    assert "【单调性验证】状态=PASS" in ans
    assert "参数=max_walk_time_min" in ans
    audit = check_citations(ans, {"served_demand": 13928.39, "total_demand": 120343.36, "weighted_walk_time_min": 9.97, "ratio": 0.2, "walk": 20.0})
    assert audit["consistent"] is True, audit

    # Counterexample: FAIL renders violating interval explicitly.
    ctrl_fail = ControlResult(
        status="ok",
        plan=RoutePlan(action="task", task_type="sensitivity"),
        summary={},
        bindings={
            **ctrl.bindings,
            "task_type": "sensitivity",
            "monotonicity_check": {
                "status": "FAIL",
                "parameter": "max_walk_time_min",
                "relation": "nondecreasing",
                "reason": "monotonicity_violation_detected",
                "violations": [{"left_value": 10.0, "right_value": 20.0}],
            },
            "detail": {"sensitivity": {"parameter": "max_walk_time_min", "points": []}},
        },
    )
    ans_fail = compose_answer(ctrl_fail, params=params)
    assert "【单调性验证】状态=FAIL" in ans_fail
    assert "违规区间：10.0→20.0" in ans_fail


def test_api_envelope_surfaces_fallback_warning_and_monotonicity(
    config: HarnessConfig, b_app_client: TestClient, tmp_path: Path
):
    service = HarnessService(
        config, client=BClient(config, client=b_app_client), trace_dir=str(tmp_path / "trace")
    )
    conv = ConversationService(
        config,
        service=service,
        llm=FailingLLM(),
        store_dir=str(tmp_path / "state"),
        dialogue_enabled=True,
    )
    client = TestClient(create_app(conversation=conv))
    sid = client.post("/v1/sessions", json={}).json()["session_id"]

    t1 = _poll_turn(
        client,
        client.post(f"/v1/sessions/{sid}/turns", json={"text": "帮我评估现状"}).json()["turn_id"],
    )
    assert t1["status"] == "SUCCEEDED", (t1.get("error"), t1.get("message"))
    assert t1["llm"]["fallback_used"] is True
    assert t1["llm"]["extract_modes"] == ["rules_fallback"]
    assert any("LLM_FALLBACK" in w for w in t1["warnings"])

    t2 = _poll_turn(
        client,
        client.post(f"/v1/sessions/{sid}/turns", json={"text": "步行阈值改成 20 分钟，其它不变"}).json()["turn_id"],
    )
    assert t2["status"] == "SUCCEEDED"
    assert t2["monotonicity_check"]["status"] == "PASS"
    assert "【单调性验证】状态=PASS" in t2["message"]
    conv.close()


def test_mcp_structured_runner_configures_valid_shelter_ids_for_v2_region(
    monkeypatch, tmp_path: Path, b_app_client: TestClient
):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SHELTERPLAN_REGION_ID", "shiqi")
    monkeypatch.setenv("SHELTERPLAN_DATA_VERSION", "shelterplan_module_a_v2.0-demo__shiqi")
    monkeypatch.setenv("SHELTERPLAN_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("SHELTERPLAN_TRACE_DIR", str(tmp_path / "mcp_trace"))
    monkeypatch.setenv("SHELTERPLAN_MCP_STRUCTURED", "1")

    cfg = HarnessConfig.from_env()
    svc = HarnessService(cfg, client=BClient(cfg, client=b_app_client), trace_dir=str(tmp_path / "mcp_trace"))
    monkeypatch.setattr(mcp_server, "_service", svc)
    monkeypatch.setattr(
        mcp_server,
        "_llm_adapter",
        SingleJsonLLM('{"intent":"stress_test","ops":[{"field":"disabled_shelter_ids","op":"set","value":["zs_0000000000"]}],"options":[],"confidence":1.0,"clarify":[]}'),
    )
    monkeypatch.setattr(mcp_server, "_structured_runners", {})

    runner = mcp_server._structured_runner("mcp-v2-guard-check")
    valid_ids = svc.session_object("mcp-v2-guard-check").valid_shelter_ids
    assert valid_ids is not None and len(valid_ids) == 25
    assert "zs_a0d74bfdf1" in valid_ids
    assert "zs_0000000000" not in valid_ids

    rec = runner.run_turn("模拟停用 zs_0000000000，做压力测试")
    assert rec.status == "FAILED"
    assert rec.control is not None and rec.control.error_code == "GUARD_INVALID_PARAM"


def test_deterministic_repairs_drops_ungrounded_zero_delta_filler_only():
    from agent.intent.extractor import _deterministic_repairs
    from agent.intent.quantities import supports
    from agent.intent.schema import ParamOp, ParsedIntent

    text = "预算设为 5，给 zs_a0d74bfdf1 扩容 100 人成本 -1，做扩容优化"
    hallucinated = ParsedIntent(
        "optimize",
        ops=[
            ParamOp("budget_units", "set", 5.0),
            ParamOp("population_demand_ratio", "delta", 0.0),
        ],
        options=[{"shelter_id": "zs_a0d74bfdf1", "delta_capacity_persons": 100, "cost_units": -1}],
    )
    repaired = _deterministic_repairs(text, hallucinated)
    assert [(op.field, op.op, op.value) for op in repaired.ops] == [("budget_units", "set", 5.0)]
    assert any("zero-delta" in w for w in repaired.warnings)

    # Counterexample: explicit field mention or non-zero hallucination is preserved so guards can clarify/reject.
    explicit_text = "把需求比例增加一点，其他不变"
    explicit_intent = ParsedIntent("modify_scenario", ops=[ParamOp("population_demand_ratio", "delta", 0.0)])
    preserved_explicit = _deterministic_repairs(explicit_text, explicit_intent)
    assert len(preserved_explicit.ops) == 1
    assert supports(explicit_text, "population_demand_ratio", "delta", 0.0)[0] is False

    nonzero_intent = ParsedIntent("optimize", ops=[ParamOp("population_demand_ratio", "delta", 0.1)])
    preserved_nonzero = _deterministic_repairs(text, nonzero_intent)
    assert any(op.field == "population_demand_ratio" and op.value == 0.1 for op in preserved_nonzero.ops)


def test_deterministic_repairs_recovers_walk_compare_and_folds_repeated_ops():
    from agent.intent.extractor import _deterministic_repairs, _rule_compare
    from agent.intent.schema import ParamOp, ParsedIntent

    walk_text = "对比步行阈值 10 分钟和 20 分钟的评估结果"
    spec = _rule_compare(walk_text)
    assert spec == {"field": "max_walk_time_min", "values": [10.0, 20.0]}

    raw = ParsedIntent(
        "compare",
        ops=[
            ParamOp("max_walk_time_min", "set", 10.0),
            ParamOp("max_walk_time_min", "set", 20.0),
        ],
    )
    repaired = _deterministic_repairs(walk_text, raw)
    assert repaired.compare == {"field": "max_walk_time_min", "values": [10.0, 20.0]}
    assert not any(op.field == "max_walk_time_min" for op in repaired.ops)

    # Counterexample: folded from repeated ratio ops when wording omits explicit delimiter.
    raw_ops_only = ParsedIntent(
        "compare",
        ops=[
            ParamOp("population_demand_ratio", "set", 0.1),
            ParamOp("population_demand_ratio", "set", 0.3),
        ],
    )
    repaired_ops = _deterministic_repairs("对比不同需求比例的覆盖差异", raw_ops_only)
    assert repaired_ops.compare == {"field": "population_demand_ratio", "values": [0.1, 0.3]}
    assert not any(op.field == "population_demand_ratio" for op in repaired_ops.ops)
