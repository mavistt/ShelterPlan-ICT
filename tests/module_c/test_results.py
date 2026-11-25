"""Result projection must stay small, faithful and paginate-able."""
from __future__ import annotations

from agent.config import HarnessConfig
from agent.harness.results import json_bytes, paginate_evidence, project


def full_result(rows: int = 300, checks: int = 5000) -> dict:
    return {
        "result_id": "r-1", "task_id": "t-1",
        "scenario_id": "c-s-t02-abcd1234", "parent_scenario_id": "c-s-t01-abcd1234",
        "scenario_changes": {"max_walk_time_min": 20},
        "scenario_hash": "a" * 64,
        "data_version": "zhongshan_pilot_v0.1", "data_status": "DEMO_ONLY",
        "analysis_mode": "demo", "solver_status": "OPTIMAL", "optimality_proven": True,
        "verification_status": "PASS_DEMO", "skill_graph_version": "module-b-skillgraph-0.1.0",
        "execution_trace_id": "trace-1",
        "warnings": ["DEMO_ONLY: not a real coverage claim."],
        "result_summary": {"served_demand": 6456.103026929056,
                           "total_demand": 110205.74056775225,
                           "weighted_walk_time_min": 9.037615807901064,
                           "optimized_served_demand": None,
                           "diagnosed_unserved_nodes": 7634},
        "certificate": {"verification_status": "PASS_DEMO", "verified_for_demo": True,
                        "verified_for_real_planning": False,
                        "checks": [{"check_id": f"C{i}", "passed": True} for i in range(checks)]},
        "result_detail": {
            "assignment": {
                "assignments": [{"demand_id": f"D{i}", "shelter_id": "S1", "persons": 1.5,
                                 "time_min": 5.0} for i in range(rows)],
                "deficits": [{"demand_id": f"D{i}", "unserved_persons": 1.0} for i in range(50)],
                "shelter_loads": {"S1": 100.0},
            },
            "diagnosis": [{"demand_id": f"D{i}", "unserved_persons": 2.0,
                           "diagnosis": "NO_LEGAL_CONNECTION"} for i in range(200)],
        },
        "provenance": {"solver_version": "SciPy 1.13.1 HiGHS", "source_hash": "b" * 64},
        "trace": [{"skill_id": "S01"}],
    }


def test_projection_stays_within_budget_and_keeps_identity():
    cfg = HarnessConfig(summary_max_bytes=20 * 1024)
    summary = project(full_result(), task_type="assess", config=cfg)
    assert json_bytes(summary) <= cfg.summary_max_bytes
    assert summary["result_id"] == "r-1"
    assert summary["scenario_id"] == "c-s-t02-abcd1234"
    assert summary["data_status"] == "DEMO_ONLY"
    assert summary["verification_status"] == "PASS_DEMO"
    assert summary["optimality_proven"] is True
    assert summary["disclosure"]["demo_only"] is True


def test_projection_numbers_match_source():
    source = full_result()
    summary = project(source, task_type="assess", config=HarnessConfig())
    assert summary["metrics"]["served_demand"] == source["result_summary"]["served_demand"]
    assert summary["metrics"]["total_demand"] == source["result_summary"]["total_demand"]
    assert summary["metrics"]["weighted_walk_time_min"] == source["result_summary"]["weighted_walk_time_min"]


def test_projection_aggregates_diagnosis_instead_of_dumping_rows():
    summary = project(full_result(), task_type="assess", config=HarnessConfig())
    diagnosis = summary.get("detail", {}).get("diagnosis", {})
    assert diagnosis.get("unserved_node_count") == 200
    assert diagnosis.get("by_cause", {}).get("NO_LEGAL_CONNECTION") == 200
    assert "demand_id" not in str(diagnosis)


def test_oversized_detail_is_dropped_before_exceeding_budget():
    cfg = HarnessConfig(summary_max_bytes=900)
    summary = project(full_result(), task_type="assess", config=cfg)
    assert json_bytes(summary) <= 900
    assert (summary.get("truncated") or summary.get("detail_omitted")
            or "detail" not in summary)


def test_evidence_pagination_is_bounded():
    source = full_result()
    page = paginate_evidence(source, section="assignments", offset=10, limit=25)
    assert page["total"] == 300
    assert len(page["items"]) == 25
    assert page["items"][0]["demand_id"] == "D10"
    assert page["has_more"] is True
    assert paginate_evidence(source, section="checks", limit=10_000)["limit"] == 200


def test_evidence_unknown_section_is_rejected():
    try:
        paginate_evidence(full_result(), section="everything")
    except ValueError as exc:
        assert "unknown evidence section" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
