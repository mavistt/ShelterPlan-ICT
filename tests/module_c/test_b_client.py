"""B client contract tests: envelope shapes, 404s, retries, error mapping."""
from __future__ import annotations

from dataclasses import replace

import pytest

from agent.b_client import BClient
from agent.errors import BError, BJobMissing, BResultMissing, BTransportError
from tests.module_c.conftest import FlakyClient

pytestmark = pytest.mark.usefixtures("b_app_client")


def test_capabilities_expose_registered_contract(b_client: BClient):
    caps = b_client.get_capabilities()
    assert caps["skill_graph_version"] == "module-b-skillgraph-0.1.0"
    assert len(caps["skills"]) == 12
    assert set(caps["task_types"]) == {
        "assess", "optimize", "diagnose_intervene", "stress_test",
        "compare", "sensitivity", "explain_result", "disaster_recovery",
    }


def test_submit_returns_202_shape_not_a_result(b_client: BClient):
    submitted = b_client.submit({"task_type": "assess", "analysis_mode": "demo"})
    assert set(submitted) >= {"job_id", "task_id", "status", "execution_trace_id"}
    assert submitted["status"] in {"QUEUED", "RUNNING"}
    job = b_client.wait_for_job(submitted["job_id"])
    assert job["status"] == "SUCCEEDED"
    result = b_client.get_result(job["result_id"])
    assert result["verification_status"] == "PASS_DEMO"
    assert result["data_status"] == "DEMO_ONLY"


def test_unknown_job_and_result_map_to_typed_errors(b_client: BClient):
    with pytest.raises(BJobMissing):
        b_client.get_job("does-not-exist")
    with pytest.raises(BResultMissing):
        b_client.get_result("does-not-exist")


def test_business_failure_arrives_as_job_error_not_http_4xx(b_client: BClient):
    submitted = b_client.submit({"task_type": "assess", "data_version": "wrong-version"})
    assert submitted["status"] in {"QUEUED", "RUNNING"}
    with pytest.raises(BError) as excinfo:
        b_client.wait_for_job(submitted["job_id"])
    assert excinfo.value.code == "B_SCENARIO_INVALID"
    assert excinfo.value.details.get("error_code") == "VERSION_MISMATCH"
    assert excinfo.value.details.get("job_id") == submitted["job_id"]
    assert excinfo.value.details.get("execution_trace_id") == submitted["execution_trace_id"]


def test_transport_failure_is_retried_then_succeeds(config, b_app_client):
    flaky = FlakyClient(b_app_client, failures=1)
    client = BClient(replace(config, max_transport_retries=2), client=flaky)
    caps = client.get_capabilities()
    assert caps["skill_graph_version"]
    assert flaky.calls >= 2


def test_transport_failure_exhausts_retries(config, b_app_client):
    flaky = FlakyClient(b_app_client, failures=5)
    client = BClient(replace(config, max_transport_retries=1), client=flaky)
    with pytest.raises(BTransportError):
        client.get_capabilities()
