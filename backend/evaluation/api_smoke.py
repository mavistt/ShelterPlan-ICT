"""Sequential API smoke tests against the configured Module A source."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from fastapi.testclient import TestClient

from backend.app import app
from backend.graph.executor import load_task_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _submit_and_wait(client: TestClient, endpoint: str, payload: dict, timeout_seconds: int = 180) -> dict:
    response = client.post(endpoint, json=payload)
    if response.status_code != 202:
        raise RuntimeError(f"{endpoint} returned HTTP {response.status_code}: {response.text[:500]}")
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job_response = client.get(f"/planning/jobs/{job_id}")
        if job_response.status_code != 200:
            raise RuntimeError(f"Job poll returned HTTP {job_response.status_code}: {job_response.text[:300]}")
        job = job_response.json()
        if job["status"] in {"SUCCEEDED", "FAILED"}:
            if job["status"] != "SUCCEEDED":
                raise RuntimeError(f"{endpoint} job failed: {job.get('error')}")
            result = client.get(f"/planning/results/{job['result_id']}")
            if result.status_code != 200:
                raise RuntimeError(f"Result read returned HTTP {result.status_code}: {result.text[:300]}")
            return {"job": job, "result": result.json()}
        time.sleep(0.1)
    raise TimeoutError(f"{endpoint} job {job_id} did not finish within {timeout_seconds}s")


def run_api_smoke(output_dir: str | Path = "outputs/module_b/real_data") -> dict:
    data, _scenario, _options = load_task_source(None)
    shelters = [row.shelter_id for row in data.shelters]
    if not shelters:
        raise RuntimeError("Configured Module A source has no shelters")
    run_id = str(uuid.uuid4())
    base = {"analysis_mode": "demo"}
    tasks = []
    with TestClient(app) as client:
        capabilities = client.get("/planning/capabilities")
        if capabilities.status_code != 200:
            raise RuntimeError(f"Capabilities returned HTTP {capabilities.status_code}")

        task = _submit_and_wait(client, "/planning/tasks", {
            **base, "task_id": f"real-api-assess-{run_id}", "task_type": "assess",
            "idempotency_key": f"{run_id}:assess",
        })
        tasks.append({"task_type": "assess", "endpoint": "/planning/tasks", "status": task["job"]["status"],
                      "result_id": task["job"]["result_id"], "verification_status": task["job"]["verification_status"],
                      "data_status": task["result"]["data_status"], "data_version": task["result"]["data_version"]})

        stress = _submit_and_wait(client, "/planning/stress-tests", {
            **base, "task_id": f"real-api-stress-{run_id}", "task_type": "stress_test",
            "parameters": {"disabled_shelter_ids": [shelters[0]]}, "idempotency_key": f"{run_id}:stress",
        })
        stress_detail = stress["result"].get("result_detail", {}).get("stress", {})
        tasks.append({"task_type": "stress_test", "endpoint": "/planning/stress-tests", "status": stress["job"]["status"],
                      "result_id": stress["job"]["result_id"], "verification_status": stress["job"]["verification_status"],
                      "stressed_scenario_id": stress_detail.get("scenario_id"),
                      "disabled_shelter_ids": stress_detail.get("changes", {}).get("disabled_shelter_ids")})

        comparison = _submit_and_wait(client, "/planning/comparisons", {
            **base, "task_id": f"real-api-compare-{run_id}", "task_type": "compare",
            "parameters": {"scenarios": [
                {"population_demand_ratio": 0.1},
                {"population_demand_ratio": 0.3},
            ]}, "idempotency_key": f"{run_id}:compare",
        })
        compare_rows = comparison["result"].get("result_detail", {}).get("comparison", {}).get("scenarios", [])
        tasks.append({"task_type": "compare", "endpoint": "/planning/comparisons", "status": comparison["job"]["status"],
                      "result_id": comparison["job"]["result_id"], "verification_status": comparison["job"]["verification_status"],
                      "scenario_count": len(compare_rows),
                      "scenario_verification_statuses": [row.get("verification_status") for row in compare_rows]})

    if any(row["status"] != "SUCCEEDED" for row in tasks):
        raise RuntimeError("At least one API task failed")
    if any(row.get("verification_status") != "PASS_DEMO" for row in tasks):
        raise RuntimeError("Expected each API task to retain PASS_DEMO status")
    report = {
        "run_id": run_id, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "label": "DEMO_ONLY configured Module A API smoke; not real-pilot acceptance",
        "source_data_status": data.data_status,
        "data_version": data.data_version,
        "demand_nodes": len(data.demands), "shelters": len(data.shelters), "od_edges": len(data.od_edges),
        "capabilities_status": "PASS", "tasks": tasks,
    }
    output = (PROJECT_ROOT / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"api_smoke_{run_id}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": target.relative_to(PROJECT_ROOT).as_posix(), **report}


def main() -> int:
    print(json.dumps(run_api_smoke(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
