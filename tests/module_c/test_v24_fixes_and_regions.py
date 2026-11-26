"""Regressions for V2.4 fixes, multi-region A v2 catalog and API hardening."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import time
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient
import yaml

from agent.api import ConversationService, create_app
from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.errors import BJobTimeout, BResultMissing, BTransportError
from agent.intent.extractor import rule_extract
from agent.llm_adapter import LLMAdapter, LLMConfig
from agent.regions import RegionCatalog, translate_a_v2_scenario
from agent.service import HarnessService
from agent.structured_runner import StructuredRunner
from agent.workflow.dialogue import PendingTask, fill_pending
from backend.app import app as b_app
from tests.module_c.rule_llm import RuleLLM

SHIELD = "zs_fed820d94e"
SCARCE_SHELTERS = [
    "zs_1b4df7920b", "zs_60213118ec", "zs_e016bde4b3",
    "zs_737caba8e8", "zs_6358642b6f",
]


def _turn(client: TestClient, session_id: str, text: str, *,
          timeout: float = 60.0, **extra: Any) -> dict[str, Any]:
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


def _build_api(tmp_path: Path, *, client_shim: Any | None = None,
               config: HarnessConfig | None = None,
               regions: RegionCatalog | None = None):
    config = config or HarnessConfig()
    b_client = BClient(config, client=client_shim or TestClient(b_app))
    service = HarnessService(config, client=b_client, trace_dir=str(tmp_path / "trace"))
    conv = ConversationService(config, service=service, llm=RuleLLM(),
                               store_dir=str(tmp_path / "state"), regions=regions)
    return conv, TestClient(create_app(conversation=conv)), b_client


# --------------------------------------------------------------------------- #
# 1. Chinese no-space ID regexes
# --------------------------------------------------------------------------- #
def test_chinese_no_space_shelter_id_extraction_and_undo(tmp_path: Path):
    stress_intent = rule_extract(f"停用{SHIELD}，做压力测试。")
    assert any(
        op.field == "disabled_shelter_ids" and op.value == [SHIELD]
        for op in stress_intent.ops
    )

    opt_intent = rule_extract(f"给{SHIELD}扩容300人，成本1，预算2，做优化。")
    assert opt_intent.options
    assert opt_intent.options[0]["shelter_id"] == SHIELD
    assert opt_intent.options[0]["delta_capacity_persons"] == 300.0

    config = HarnessConfig()
    service = HarnessService(config, client=BClient(config, client=TestClient(b_app)),
                             trace_dir=str(tmp_path / "trace"))
    runner = StructuredRunner("harness_opt", service, RuleLLM(), config,
                              session_id="no-space-id", dialogue_enabled=True)
    first = runner.run_turn(f"停用{SHIELD}，做压力测试。")
    assert first.status == "SUCCEEDED", first.text
    assert service.session_state("no-space-id")["current"]["params"]["disabled_shelter_ids"] == [SHIELD]

    second = runner.run_turn(f"不要停用{SHIELD}，评估现状。")
    assert second.status == "SUCCEEDED", second.text
    assert service.session_state("no-space-id")["current"]["params"]["disabled_shelter_ids"] == []


def test_chinese_no_space_slot_filling_for_shelter_and_result_id():
    pending_result = PendingTask(task_type="explain_result", missing=["result_id"])
    filled_result = fill_pending(pending_result, "用result-abcd1234那个")
    assert filled_result.complete
    assert filled_result.values["result_id"] == "result-abcd1234"

    pending_stress = PendingTask(task_type="stress_test", missing=["disabled_shelter_ids"])
    filled_stress = fill_pending(pending_stress, f"停用{SHIELD}吧")
    assert filled_stress.complete
    assert filled_stress.values["disabled_shelter_ids"] == [SHIELD]


# --------------------------------------------------------------------------- #
# 2. Optimize / stress_test layers, KPI metrics & full-result cache
# --------------------------------------------------------------------------- #
def test_optimize_layers_metrics_and_full_result_cache(tmp_path: Path, monkeypatch):
    conv, client, b_client = _build_api(tmp_path)
    get_result_calls = 0
    original_get_result = b_client.get_result

    def counted_get_result(result_id: str) -> dict[str, Any]:
        nonlocal get_result_calls
        get_result_calls += 1
        return original_get_result(result_id)

    monkeypatch.setattr(b_client, "get_result", counted_get_result)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]

    # Disable scarce shelters first so expansion creates real capacity gain.
    scarce = " ".join(SCARCE_SHELTERS)
    first = _turn(client, session_id, f"停用 {scarce}，评估现状。")
    assert first["status"] == "SUCCEEDED", first["message"]

    second = _turn(client, session_id, f"预算1，给{SHIELD}扩容300人、成本1，做优化。")
    assert second["status"] == "SUCCEEDED", second["message"]
    metrics = second["metrics"]
    assert metrics["optimized_served_demand"] > metrics["served_demand"]
    assert metrics["optimized_coverage_ratio"] == pytest.approx(
        metrics["optimized_served_demand"] / metrics["total_demand"]
    )
    assert metrics["coverage_delta_persons"] == pytest.approx(
        metrics["optimized_served_demand"] - metrics["served_demand"]
    )
    assert "优化前基线覆盖率" in second["message"]
    assert "优化后已服务" in second["message"]

    rid = second["result_id"]
    calls_after_turn = get_result_calls

    auto_layers = client.get(f"/v1/results/{rid}/layers", params={"session_id": session_id})
    assert auto_layers.status_code == 200, auto_layers.text
    calls_after_first_layer = get_result_calls
    assert calls_after_first_layer == calls_after_turn + 1
    body = auto_layers.json()
    assert body["variant"] == "optimized"
    assert [row["layer_id"] for row in body["layers"]] == [
        "shelter_utilization",
        "shelter_utilization_baseline",
        "shelter_utilization_delta",
    ]
    assert body["selected_option_ids"]

    opt_rows = {row["shelter_id"]: row for row in body["layers"][0]["rows"]}
    base_rows = {row["shelter_id"]: row for row in body["layers"][1]["rows"]}
    delta_rows = {row["shelter_id"]: row for row in body["layers"][2]["rows"]}
    assert opt_rows[SHIELD]["load_persons"] > base_rows[SHIELD]["load_persons"]
    assert delta_rows[SHIELD]["delta_load_persons"] > 0

    baseline_layers = client.get(
        f"/v1/results/{rid}/layers",
        params={"session_id": session_id, "variant": "baseline"},
    )
    assert baseline_layers.status_code == 200
    assert baseline_layers.json()["variant"] == "baseline"
    assert len(baseline_layers.json()["layers"]) == 1

    optimized_layers = client.get(
        f"/v1/results/{rid}/layers",
        params={"session_id": session_id, "variant": "optimized"},
    )
    assert optimized_layers.status_code == 200
    assert optimized_layers.json()["variant"] == "optimized"

    ev = client.get(
        f"/v1/results/{rid}/evidence/diagnosis",
        params={"session_id": session_id},
    )
    assert ev.status_code == 200
    assert get_result_calls == calls_after_first_layer, "layers & evidence must reuse cached full result"
    conv.close()


def test_stress_test_layers_returns_fixed_and_adaptive(tmp_path: Path):
    conv, client, _ = _build_api(tmp_path)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]

    set_budget = _turn(client, session_id, "预算设为1，评估现状。")
    assert set_budget["status"] == "SUCCEEDED"

    stressed = _turn(
        client,
        session_id,
        f"停用 zs_1b4df7920b，给{SHIELD}扩容300人、成本1，做压力测试。",
    )
    assert stressed["status"] == "SUCCEEDED", stressed["message"]
    rid = stressed["result_id"]

    layers = client.get(f"/v1/results/{rid}/layers", params={"session_id": session_id}).json()
    assert layers["variant"] == "fixed_plan_stress"
    layer_ids = [row["layer_id"] for row in layers["layers"]]
    assert "shelter_utilization_adaptive" in layer_ids
    assert "shelter_utilization_baseline" in layer_ids
    assert "shelter_utilization_delta" in layer_ids

    adaptive = client.get(
        f"/v1/results/{rid}/layers",
        params={"session_id": session_id, "variant": "adaptive"},
    ).json()
    assert adaptive["variant"] == "adaptive_replan"
    assert any(row["layer_id"] == "shelter_utilization_fixed" for row in adaptive["layers"])
    conv.close()


# --------------------------------------------------------------------------- #
# 3. Restart recovery (memory, client_turns, lazy handle) & reset
# --------------------------------------------------------------------------- #
def test_restart_restores_memory_client_turns_and_lazy_loads(tmp_path: Path):
    conv1, client1, _ = _build_api(tmp_path)
    session_id = client1.post("/v1/sessions", json={}).json()["session_id"]
    first = _turn(client1, session_id, "需求比例0.3，评估现状。", client_turn_id="idem-1")
    assert first["status"] == "SUCCEEDED"
    conv1.close()

    conv2, client2, _ = _build_api(tmp_path)
    # No create_session call after restart: lazy-loaded from disk.
    view_resp = client2.get(f"/v1/sessions/{session_id}")
    assert view_resp.status_code == 200
    assert first["result_id"] in view_resp.json()["result_ids"]

    replay = client2.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": "任意文本", "client_turn_id": "idem-1"},
    )
    assert replay.status_code == 202
    assert replay.json()["turn_id"] == first["turn_id"]

    explained = _turn(client2, session_id, "解释一下上次那个结果。")
    assert explained["status"] == "SUCCEEDED", explained["message"]
    assert explained["task_type"] == "explain_result"
    conv2.close()


def test_reset_clears_client_turns_and_disk_turns(tmp_path: Path):
    conv1, client1, _ = _build_api(tmp_path)
    session_id = client1.post("/v1/sessions", json={}).json()["session_id"]
    first = _turn(client1, session_id, "需求比例0.3，评估现状。", client_turn_id="dup-key")
    assert first["status"] == "SUCCEEDED"

    reset_resp = client1.post(f"/v1/sessions/{session_id}/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["turns"] == []

    second = _turn(client1, session_id, "评估现状。", client_turn_id="dup-key")
    assert second["status"] == "SUCCEEDED"
    assert second["turn_id"] != first["turn_id"]
    conv1.close()

    conv2, client2, _ = _build_api(tmp_path)
    view = client2.get(f"/v1/sessions/{session_id}").json()
    turn_ids = [row["turn_id"] for row in view["turns"]]
    assert turn_ids == [second["turn_id"]]
    assert first["turn_id"] not in turn_ids
    conv2.close()


# --------------------------------------------------------------------------- #
# 4. Result error code mapping
# --------------------------------------------------------------------------- #
def test_result_endpoints_preserve_real_http_status_codes(tmp_path: Path, monkeypatch):
    conv, client, _ = _build_api(tmp_path)
    session_a = client.post("/v1/sessions", json={}).json()["session_id"]
    session_b = client.post("/v1/sessions", json={}).json()["session_id"]
    done = _turn(client, session_a, "评估现状。")
    rid = done["result_id"]

    assert client.get(f"/v1/results/{rid}/layers", params={"session_id": "missing"}).status_code == 404
    assert client.get(f"/v1/results/{rid}/layers", params={"session_id": session_b}).status_code == 403
    assert client.get(
        f"/v1/results/{rid}/layers",
        params={"session_id": session_a, "variant": "bogus"},
    ).status_code == 422
    assert client.get(
        f"/v1/results/{rid}/evidence/bogus_section",
        params={"session_id": session_a},
    ).status_code == 422

    monkeypatch.setattr(
        conv.service,
        "get_full_result",
        lambda _rid: (_ for _ in ()).throw(BResultMissing("gone")),
    )
    assert client.get(f"/v1/results/{rid}/layers", params={"session_id": session_a}).status_code == 404

    monkeypatch.setattr(
        conv.service,
        "get_full_result",
        lambda _rid: (_ for _ in ()).throw(BTransportError("down")),
    )
    assert client.get(f"/v1/results/{rid}/layers", params={"session_id": session_a}).status_code == 503

    monkeypatch.setattr(
        conv.service,
        "get_full_result",
        lambda _rid: (_ for _ in ()).throw(BJobTimeout("slow")),
    )
    assert client.get(
        f"/v1/results/{rid}/evidence/diagnosis",
        params={"session_id": session_a},
    ).status_code == 503
    conv.close()


# --------------------------------------------------------------------------- #
# 5. BClient timeout & LLMAdapter proxy / reasoning_effort compatibility
# --------------------------------------------------------------------------- #
def test_b_client_uses_read_timeout_for_get_result():
    captured_timeouts: list[float] = []

    class DummyClient:
        def request(self, method: str, path: str, json: Any = None, timeout: Any = None):
            if timeout is not None:
                captured_timeouts.append(float(timeout.read))
            if method == "POST":
                return httpx.Response(202, json={"job_id": "j-1", "task_id": "t-1", "status": "QUEUED"})
            return httpx.Response(200, json={"result_id": "r-1"})

    cfg = HarnessConfig(submit_timeout_s=7.0, read_timeout_s=45.0)
    client = BClient(cfg, client=DummyClient())
    client.submit({"task_type": "assess"})
    client.get_result("r-1")
    assert captured_timeouts == [7.0, 45.0]


def test_llm_adapter_trust_env_local_vs_remote():
    local_adapter = LLMAdapter(LLMConfig(base_url="http://127.0.0.1:11434/v1", model="m"))
    remote_adapter = LLMAdapter(LLMConfig(base_url="https://api.example.com/v1", model="m"))
    try:
        assert local_adapter._client.trust_env is False  # noqa: SLF001
        assert remote_adapter._client.trust_env is True  # noqa: SLF001
    finally:
        local_adapter.close()
        remote_adapter.close()


def test_llm_adapter_downgrades_unsupported_reasoning_effort():
    requests_seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests_seen.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(
                400,
                json={"error": {"message": "Unknown parameter: 'reasoning_effort'"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )

    adapter = LLMAdapter(
        LLMConfig(base_url="https://api.example.com/v1", model="m"),
        transport=httpx.MockTransport(handler),
    )
    first = adapter.chat([{"role": "user", "content": "hi"}], reasoning_effort="none")
    assert first.content == "ok"
    assert len(requests_seen) == 2
    assert "reasoning_effort" in requests_seen[0]
    assert "reasoning_effort" not in requests_seen[1]

    second = adapter.chat([{"role": "user", "content": "hi2"}], reasoning_effort="none")
    assert second.content == "ok"
    assert len(requests_seen) == 3
    assert "reasoning_effort" not in requests_seen[2]
    adapter.close()


# --------------------------------------------------------------------------- #
# 6. MCP structured entry supports multi-turn clarification & shared LLM
# --------------------------------------------------------------------------- #
def test_mcp_structured_supports_multi_turn_clarification_and_shared_llm(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SHELTERPLAN_MCP_STRUCTURED", "1")
    import agent.mcp_server as mcp_server

    importlib.reload(mcp_server)
    config = HarnessConfig()
    service = HarnessService(config, client=BClient(config, client=TestClient(b_app)),
                             trace_dir=str(tmp_path / "mcp-trace"))
    shared_llm = RuleLLM()
    monkeypatch.setattr(mcp_server, "_service", service, raising=False)
    monkeypatch.setattr(mcp_server, "_llm_adapter", shared_llm, raising=False)
    mcp_server._structured_runners.clear()  # noqa: SLF001

    try:
        r1 = mcp_server._structured_runner("s-one")  # noqa: SLF001
        r2 = mcp_server._structured_runner("s-two")  # noqa: SLF001
        assert r1.extractor.llm is shared_llm
        assert r2.extractor.llm is shared_llm

        turn1 = mcp_server.handle_request("帮我做个扩容优化。", session_id="mcp-dlg")
        assert turn1["ok"] is True
        assert turn1["status"] == "NEEDS_CLARIFICATION"
        assert turn1["clarification_fields"] == ["budget_units"]

        turn2 = mcp_server.handle_request("预算2。", session_id="mcp-dlg")
        assert turn2["status"] == "NEEDS_CLARIFICATION"
        assert turn2["clarification_fields"] == ["improvement_options"]

        turn3 = mcp_server.handle_request(f"给{SHIELD}扩容300人，成本1。", session_id="mcp-dlg")
        assert turn3["status"] == "SUCCEEDED", turn3
        assert turn3["resumed_from_clarification"] is True
        assert turn3["result_id"]
    finally:
        mcp_server._structured_runners.clear()  # noqa: SLF001
        monkeypatch.delenv("SHELTERPLAN_MCP_STRUCTURED", raising=False)
        importlib.reload(mcp_server)


# --------------------------------------------------------------------------- #
# 7. RegionCatalog & multi-region API (legacy + Module A v2)
# --------------------------------------------------------------------------- #
def test_legacy_region_catalog_and_endpoints(tmp_path: Path):
    conv, client, _ = _build_api(tmp_path)
    regions_resp = client.get("/v1/regions")
    assert regions_resp.status_code == 200
    assert client.get("/api/regions").status_code == 200
    body = regions_resp.json()
    assert body["source_mode"] == "legacy_pilot"
    assert len(body["regions"]) == 1
    assert body["regions"][0]["region_id"] == "east_district"

    layers_resp = client.get("/v1/regions/east_district/layers")
    assert layers_resp.status_code == 200
    assert layers_resp.json()["layers"]
    scenarios_resp = client.get("/v1/regions/east_district/scenarios")
    assert scenarios_resp.status_code == 200
    assert scenarios_resp.json()["scenarios"]
    assert client.get("/v1/regions/nope/layers").status_code == 404
    conv.close()


def _write_v2_workspace(root: Path) -> None:
    (root / "configs").mkdir(parents=True, exist_ok=True)
    regions_yaml = {
        "project": {
            "name": "ShelterPlan",
            "data_version": "shelterplan_module_a_v2.0-demo",
        },
        "regions": {
            "east_district": {"town": "东区街道", "role": "primary"},
            "shiqi": {"town": "石岐街道", "role": "supplementary"},
            "xiaolan": {"town": "小榄镇", "role": "supplementary"},
        },
    }
    (root / "configs" / "regions.yaml").write_text(
        yaml.safe_dump(regions_yaml, allow_unicode=True), encoding="utf-8"
    )
    planning_yaml = {
        "data_version": "shelterplan_module_a_v2.0-demo",
        "scenarios": [
            {
                "scenario_id": "cross_region_comparison__base",
                "region_ids": ["east_district", "shiqi", "xiaolan"],
                "scenario_type": "cross_region_comparison",
                "population_demand_ratio": 0.2,
                "max_walk_time_min": 15,
                "closed_shelter_ids": [],
                "capacity_adjustments": {},
                "data_version": None,
                "region_data_versions": {
                    "east_district": "shelterplan_module_a_v2.0-demo__east_district",
                    "shiqi": "shelterplan_module_a_v2.0-demo__shiqi",
                    "xiaolan": "shelterplan_module_a_v2.0-demo__xiaolan",
                },
            }
        ],
    }
    (root / "configs" / "planning_scenarios.yaml").write_text(
        yaml.safe_dump(planning_yaml, allow_unicode=True), encoding="utf-8"
    )

    for region_id, shelter_id, has_od in (
        ("east_district", "zs_east0001", True),
        ("shiqi", "zs_shiqi0001", True),
        ("xiaolan", "zs_xiaolan0001", False),
    ):
        rdir = root / "data" / "processed" / "regions" / region_id
        (rdir / "map_layers").mkdir(parents=True, exist_ok=True)
        version = f"shelterplan_module_a_v2.0-demo__{region_id}"
        (rdir / "shelters.geojson").write_text(
            json.dumps({
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [113.38, 22.52]},
                    "properties": {"shelter_id": shelter_id, "region_id": region_id},
                }],
            }),
            encoding="utf-8",
        )
        (rdir / "boundary_metadata.json").write_text(
            json.dumps({"boundary_status": "demo_analysis_boundary"}), encoding="utf-8"
        )
        (rdir / "boundary.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
        )
        (rdir / "map_layers" / "road_edges.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
        )
        files_table = {
            f"data/processed/regions/{region_id}/boundary.geojson": {
                "bytes": 40, "sha256": "abc"
            },
            f"data/processed/regions/{region_id}/map_layers/road_edges.geojson": {
                "bytes": 40, "sha256": "def"
            },
        }
        quality: dict[str, Any] = {
            "region_id": region_id,
            "data_version": version,
            "release_status": "PASS_DEMO" if has_od else "PARTIAL",
            "verdict": "DEMO_ONLY",
            "data_status": "DEMO_ONLY",
            "spatial_shelters": 1,
            "population": {"positive_cells": 100},
            "blockers": ["Demo coordinates are approximate."],
        }
        if has_od:
            (rdir / "travel_matrix.parquet").write_bytes(b"PAR1")
            files_table[f"data/processed/regions/{region_id}/travel_matrix.parquet"] = {
                "bytes": 4, "sha256": "123"
            }
            quality["travel_matrix"] = {"rows": 100}
        (rdir / "manifest.json").write_text(
            json.dumps({
                "region_id": region_id,
                "data_version": version,
                "release_status": quality["release_status"],
                "files": files_table,
            }),
            encoding="utf-8",
        )
        (rdir / "quality_summary.json").write_text(json.dumps(quality), encoding="utf-8")
        # Intentionally omit road_edges and boundary from layers_manifest.json.
        (rdir / "layers_manifest.json").write_text(
            json.dumps({
                "schema_version": 1,
                "region_id": region_id,
                "layers": [{
                    "layer_id": "shelter_capacity",
                    "layer_name": "避难场所容量分布",
                    "path": f"data/processed/regions/{region_id}/map_layers/shelter_capacity.geojson",
                    "format": "GeoJSON",
                    "crs": "EPSG:4326",
                    "data_status": "DEMO_ONLY",
                }],
            }),
            encoding="utf-8",
        )
        (rdir / "scenario_catalog.json").write_text(
            json.dumps({
                "region_id": region_id,
                "data_version": version,
                "scenarios": [
                    {
                        "scenario_id": f"{region_id}__facility_disruption",
                        "region_id": region_id,
                        "scenario_type": "facility_disruption",
                        "population_demand_ratio": 0.2,
                        "max_walk_time_min": 15,
                        "closed_shelter_ids": [shelter_id],
                        "capacity_adjustments": {},
                        "data_version": version,
                    },
                    {
                        "scenario_id": f"{region_id}__capacity_expansion",
                        "region_id": region_id,
                        "scenario_type": "capacity_expansion",
                        "population_demand_ratio": 0.2,
                        "max_walk_time_min": 15,
                        "closed_shelter_ids": [],
                        "capacity_adjustments": {shelter_id: {"delta_persons": 50.0}},
                        "capacity_adjustment_status": "parameter_only_not_optimization",
                        "data_version": version,
                    },
                ],
            }),
            encoding="utf-8",
        )


class _MultiRegionStubB:
    """In-memory stub simulating a region-aware Module B server."""

    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.submitted_payloads: list[dict[str, Any]] = []

    def request(self, method: str, path: str, json: Any = None):
        if method == "GET" and path == "/planning/capabilities":
            return httpx.Response(200, json={
                "skill_graph_version": "0.1.0",
                "task_types": ["assess", "stress_test", "optimize"],
                "skills": [],
                "composites": [],
            })
        if method == "POST" and path in {"/planning/tasks", "/planning/stress-tests"}:
            self.submitted_payloads.append(json)
            job_id = f"job-{len(self.jobs) + 1}"
            res_id = f"res-{len(self.results) + 1}"
            scenario = json.get("scenario") or {}
            sid = "zs_shiqi0001"
            self.results[res_id] = {
                "result_id": res_id,
                "task_id": json.get("task_id"),
                "scenario_id": json.get("scenario_id"),
                "parent_scenario_id": json.get("parent_scenario_id"),
                "scenario_changes": json.get("changes") or {},
                "data_version": scenario.get("data_version"),
                "data_status": "DEMO_ONLY",
                "analysis_mode": "demo",
                "solver_status": "OPTIMAL",
                "verification_status": "PASS_DEMO",
                "warnings": ["DEMO_ONLY"],
                "certificate": {"checks": [{"check_id": "C1", "passed": True}]},
                "result_summary": {
                    "served_demand": 500.0,
                    "total_demand": 1000.0,
                    "weighted_walk_time_min": 8.5,
                },
                "result_detail": {
                    "assignment": {
                        "optimality_proven": True,
                        "shelter_loads": {sid: 500.0},
                        "shelter_utilization": {sid: 0.5},
                        "deficits": [],
                    }
                },
            }
            self.jobs[job_id] = {"job_id": job_id, "status": "SUCCEEDED", "result_id": res_id}
            return httpx.Response(202, json={"job_id": job_id, "status": "QUEUED"})
        if method == "GET" and path.startswith("/planning/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self.jobs[job_id])
        if method == "GET" and path.startswith("/planning/results/"):
            res_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self.results[res_id])
        return httpx.Response(404, json={"error_code": "NOT_FOUND"})


def test_translate_a_v2_scenario_does_not_fabricate_cost():
    translated = translate_a_v2_scenario({
        "scenario_id": "shiqi__capacity_expansion",
        "region_id": "shiqi",
        "scenario_type": "capacity_expansion",
        "population_demand_ratio": 0.25,
        "max_walk_time_min": 20,
        "closed_shelter_ids": ["zs_1"],
        "capacity_adjustments": {"zs_1": {"delta_persons": 50}},
        "data_version": "shelterplan_module_a_v2.0-demo__shiqi",
    })
    assert translated["c_changes"] == {
        "population_demand_ratio": 0.25,
        "max_walk_time_min": 20.0,
        "disabled_shelter_ids": ["zs_1"],
    }
    assert translated["requires_cost_confirmation"] is True
    assert "cost_units" not in str(translated["c_changes"])


def test_module_a_v2_region_catalog_isolation_and_binding(tmp_path: Path):
    v2_root = tmp_path / "v2_repo"
    _write_v2_workspace(v2_root)
    config = HarnessConfig(project_root=v2_root, send_region_id_to_b=True)
    catalog = RegionCatalog(v2_root, config=config)
    stub_b = _MultiRegionStubB()

    conv, client, _ = _build_api(tmp_path, client_shim=stub_b, config=config, regions=catalog)

    regions = client.get("/v1/regions").json()
    assert regions["source_mode"] == "module_a_v2"
    by_id = {row["region_id"]: row for row in regions["regions"]}
    assert set(by_id) == {"east_district", "shiqi", "xiaolan"}
    assert by_id["east_district"]["od_available"] is True
    assert by_id["shiqi"]["od_available"] is True
    assert by_id["xiaolan"]["od_available"] is False
    assert by_id["xiaolan"]["release_status"] == "PARTIAL"
    assert "严禁合并" in regions["cross_region_scenarios"][0]["isolation_note"]

    layers = client.get("/api/regions/east_district/layers").json()
    layer_ids = {row["layer_id"]: row for row in layers["layers"]}
    assert {"shelter_capacity", "road_edges", "boundary"} <= set(layer_ids)
    assert layer_ids["road_edges"]["supplemented_from_manifest"] is True

    scenarios = client.get("/api/regions/east_district/scenarios").json()
    disruption = next(
        row for row in scenarios["scenarios"]
        if row["scenario_type"] == "facility_disruption"
    )
    assert disruption["normalized_changes"]["disabled_shelter_ids"] == ["zs_east0001"]
    expansion = next(
        row for row in scenarios["scenarios"]
        if row["scenario_type"] == "capacity_expansion"
    )
    assert expansion["c_translation"]["requires_cost_confirmation"] is True

    # Partial region without OD must be refused on session creation (409), unknown region -> 404.
    assert client.post("/v1/sessions", json={"region_id": "xiaolan"}).status_code == 409
    assert client.post("/v1/sessions", json={"region_id": "unknown"}).status_code == 404

    created = client.post(
        "/v1/sessions",
        json={
            "region_id": "shiqi",
            "data_version": "shelterplan_module_a_v2.0-demo__shiqi",
        },
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    assert created.json()["region_id"] == "shiqi"
    assert created.json()["data_version"] == "shelterplan_module_a_v2.0-demo__shiqi"

    mismatch = client.post(
        "/v1/sessions",
        json={"region_id": "shiqi", "data_version": "wrong-version"},
    )
    assert mismatch.status_code == 409
    assert "data_version" in mismatch.json()["detail"]

    # Cross-region shelter ID from east_district must be blocked before calling B.
    cross_region_turn = _turn(client, sid, "停用 zs_east0001，做压力测试。")
    assert cross_region_turn["status"] == "FAILED"
    assert cross_region_turn["error"]["code"] == "GUARD_INVALID_PARAM"
    assert stub_b.submitted_payloads == []

    # Valid shelter ID for shiqi runs and sends region_id when enabled.
    valid_turn = _turn(client, sid, "停用 zs_shiqi0001，做压力测试。")
    assert valid_turn["status"] == "SUCCEEDED", valid_turn["message"]
    assert valid_turn["region_id"] == "shiqi"
    assert valid_turn["data_version"] == "shelterplan_module_a_v2.0-demo__shiqi"
    assert stub_b.submitted_payloads[0]["region_id"] == "shiqi"

    # Reset preserves bound region & version.
    reset_view = client.post(f"/v1/sessions/{sid}/reset").json()
    assert reset_view["region_id"] == "shiqi"
    assert reset_view["data_version"] == "shelterplan_module_a_v2.0-demo__shiqi"
    conv.close()

    # Restart restores bound region & version.
    conv2, client2, _ = _build_api(tmp_path, client_shim=stub_b, config=config, regions=catalog)
    view2 = client2.get(f"/v1/sessions/{sid}").json()
    assert view2["region_id"] == "shiqi"
    assert view2["data_version"] == "shelterplan_module_a_v2.0-demo__shiqi"
    conv2.close()
