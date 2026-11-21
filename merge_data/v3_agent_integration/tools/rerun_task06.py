from __future__ import annotations

import csv
import json
import os
import sys
import time
import uuid
from pathlib import Path

V3 = Path(__file__).resolve().parents[1]
ROOT = V3.parents[1]
C_ROOT_RAW = os.environ.get("SHELTERPLAN_MODULE_C_PATH")
if not C_ROOT_RAW:
    raise SystemExit("Set SHELTERPLAN_MODULE_C_PATH to a separate Module C checkout; no C source is bundled here.")
C_ROOT = Path(C_ROOT_RAW).resolve()
if not (C_ROOT / "agent").is_dir():
    raise SystemExit(f"Module C path does not contain agent/: {C_ROOT}")
sys.path[:0] = [str(C_ROOT), str(ROOT)]
os.chdir(V3)
RUN_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]

from starlette.testclient import TestClient
from backend.app import app as b_app
from agent.api import ConversationService
from agent.b_client import BClient
from agent.config import HarnessConfig
from agent.service import HarnessService
from tests.module_c.rule_llm import RuleLLM


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> None:
    gt_path = V3 / "TASK_GT.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    gt_task = next(task for task in gt["tasks"] if task["task_id"] == "V3-06-BUDGET-WALK-BIND")
    user_text = gt_task["turns"][0]["user_intent"]

    config = HarnessConfig(trace_dir=V3 / "traces" / "harness", job_timeout_s=180)
    b_test_client = TestClient(b_app)
    b_client = BClient(config, client=b_test_client)
    service = HarnessService(config, client=b_client, trace_dir=str(V3 / "traces" / "harness"))
    llm = RuleLLM()
    session_id = f"v3-v3-06-budget-walk-bind-{RUN_ID}"
    store_dir = V3 / "state" / RUN_ID / "V3-06-BUDGET-WALK-BIND"
    conversation = ConversationService(config, service=service, llm=llm, store_dir=str(store_dir))
    conversation.create_session(session_id)
    accepted = conversation.submit_turn(session_id, user_text, client_turn_id=f"V3-06-{RUN_ID}")
    deadline = time.monotonic() + 180
    final = conversation.get_turn(accepted["turn_id"])
    while final["status"] == "RUNNING" and time.monotonic() < deadline:
        time.sleep(0.04)
        final = conversation.get_turn(accepted["turn_id"])
    if final["status"] == "RUNNING":
        raise TimeoutError("V3-06 conversation turn did not finish")

    session_view = conversation.session_view(session_id)
    trace_file = V3 / "traces" / "harness" / f"{session_id}.jsonl"
    c_events = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()] if trace_file.exists() else []
    c_tool_calls = [event for event in c_events if event.get("event") == "tool_call"]
    observer_full = b_client.get_result(final["result_id"]) if final.get("result_id") else None
    params = session_view.get("params") or {}
    observed = (observer_full or {}).get("result_summary") or {}
    baseline = 6456.103026929056
    passed = (
        final.get("status") == "SUCCEEDED"
        and params.get("budget_units") == 30.0
        and params.get("max_walk_time_min") == 15.0
        and final.get("data_status") == "DEMO_ONLY"
        and (observer_full or {}).get("verification_status") == "PASS_DEMO"
        and abs(float(observed.get("served_demand") or 0) - baseline) <= 0.004
    )
    trace_path = V3 / "traces" / "V3-06-BUDGET-WALK-BIND.json"
    write_json(trace_path, {
        "task_id": "V3-06-BUDGET-WALK-BIND",
        "run_id": RUN_ID,
        "user_intent": user_text,
        "accepted": accepted,
        "final_turn": final,
        "session_state": session_view,
        "rule_llm_calls": llm.calls,
        "c_harness_tool_calls": c_tool_calls,
        "c_harness_tool_call_count": len(c_tool_calls),
        "observer_result_fetch_excluded_from_agent_tool_count": bool(observer_full),
        "b_result_audit": {
            "result_id": (observer_full or {}).get("result_id"),
            "solver_status": (observer_full or {}).get("solver_status"),
            "verification_status": (observer_full or {}).get("verification_status"),
            "data_status": (observer_full or {}).get("data_status"),
            "result_summary": observed,
            "graph_trace": [
                {"skill_id": row.get("skill_id"), "cache_hit": row.get("cache_hit"), "duration_ms": row.get("duration_ms")}
                for row in ((observer_full or {}).get("trace") or [])
            ],
        },
        "expected_state": {"budget_units": 30.0, "max_walk_time_min": 15.0, "data_status": "DEMO_ONLY"},
        "pass": passed,
        "path_note": "fresh unique Session and store; direct C RuleLLM application API; actual B app; no paid API",
    })

    manifest_path = V3 / "TASK_MANIFEST.csv"
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        if row["task_id"] == "V3-06-BUDGET-WALK-BIND":
            row.update(
                run_status="PASS" if passed else "FAIL",
                task_success=str(passed),
                solver_status=(observer_full or {}).get("solver_status", ""),
                verification_status=(observer_full or {}).get("verification_status", ""),
                tool_calls=str(len(c_tool_calls)),
                duration_ms=str((final.get("timings") or {}).get("run_ms") or ""),
                trace_path="traces/V3-06-BUDGET-WALK-BIND.json",
                note=f"fresh C API run; parsed budget={params.get('budget_units')}; walk={params.get('max_walk_time_min')}; RuleLLM calls={llm.calls}",
            )
    fields = list(rows[0]) if rows else []
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary_path = V3 / "tasks" / "run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["supplemental_runs"] = summary.get("supplemental_runs", []) + [{
        "task_id": "V3-06-BUDGET-WALK-BIND", "run_id": RUN_ID, "pass": passed,
        "note": "fresh-session re-run after adding the natural-language budget upper-bound token",
    }]
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    conversation.close()
    service.close()
    print(json.dumps({"task_id": "V3-06-BUDGET-WALK-BIND", "pass": passed,
                      "status": final.get("status"), "params": params,
                      "tool_calls": len(c_tool_calls), "trace": str(trace_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
