"""The C -> D business demo, asserted in pytest.

Starts Module B and Module C as two real uvicorn processes and runs the exact
flow documented in `docs/module_c/C_TO_D_API_HANDOFF.md`:

    现状评估 -> 修改条件 -> 扩容优化 -> 方案比较

plus a clarification round-trip in a second session. The only substitute is the
model (rule extractor); planner, solver, HTTP, state and result ids are real.
"""
from __future__ import annotations

import pytest

from examples.c_to_d_business_flow import run_flows, start_servers, stop_servers


@pytest.fixture(scope="module")
def live_servers(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("c-to-d-demo")
    _b_url, c_url, procs = start_servers("rule", workdir)
    try:
        yield c_url
    finally:
        stop_servers(procs)


def test_four_step_business_flow_and_clarification(live_servers):
    flows = run_flows(live_servers)

    business = flows["business_flow"]
    assert [row["label"] for row in business] == ["现状评估", "修改条件", "扩容优化", "方案比较"]
    assert [row["status"] for row in business] == ["SUCCEEDED"] * 4
    assert [row["task_type"] for row in business] == [
        "assess", "assess", "optimize", "compare"]
    assert all(row["data_status"] == "DEMO_ONLY" for row in business)
    assert all(row["result_id"] for row in business)
    assert all(any("DEMO_ONLY" in str(w) for w in (row["warnings"] or []))
               for row in business)
    assert business[0]["metrics"]["served_demand"] > 0

    clarification = flows["clarification_flow"]
    assert [row["status"] for row in clarification] == [
        "NEEDS_CLARIFICATION", "NEEDS_CLARIFICATION", "SUCCEEDED"]
    assert clarification[0]["clarification"]["fields"] == ["budget_units"]
    assert clarification[-1]["result_id"]

    view = flows["final_session_view"]
    assert view["params"]["population_demand_ratio"] == 0.3
    assert view["params"]["max_walk_time_min"] == 20.0
    assert view["params"]["budget_units"] == 2.0
    assert len(view["result_ids"]) >= 4
    assert view["pending_clarification"] is None
