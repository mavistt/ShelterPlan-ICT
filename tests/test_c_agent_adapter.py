from __future__ import annotations

import io
import unittest
from urllib.error import HTTPError

from benchmark.shelterplan_eval.c_agent_adapter import (
    build_agent_user_turns,
    normalize_agent_evidence,
    submit_real_c_agent,
)
from benchmark.shelterplan_eval.stage3_pilot import build_tasks


class CAgentAdapterTests(unittest.TestCase):
    def test_user_turns_are_natural_language_and_do_not_leak_gt(self):
        task = build_tasks()[0]
        turns = build_agent_user_turns(task)
        self.assertEqual(len(turns), 1)
        self.assertIn("shiqi", turns[0]["text"])
        self.assertNotIn("EXECUTE", turns[0]["text"])
        self.assertNotIn(task["agent_input"]["dataset_content_hash"], turns[0]["text"])
        self.assertNotIn(task["agent_input"]["source_manifest_sha256"], turns[0]["text"])
        self.assertIn("text", turns[0])

    def test_same_session_is_used_for_turn_and_snapshot(self):
        task = build_tasks()[0]
        calls = []

        def fake(method, url, payload, *, timeout_seconds):
            calls.append((method, url, payload))
            if method == "POST" and url.endswith("/v1/sessions"):
                self.assertEqual(payload["region_id"], task["region_id"])
                self.assertEqual(payload["data_version"], task["data_version"])
                return {"session_id": "s-1", "region_id": "shiqi"}
            if method == "POST" and url.endswith("/v1/sessions/s-1/turns"):
                self.assertNotIn("gt_assertions", payload)
                self.assertNotIn("b_request_payload", payload)
                return {"turn_id": "t-1", "status": "RUNNING"}
            if method == "GET" and url.endswith("/v1/turns/t-1"):
                return {"status": "SUCCEEDED", "region_id": "shiqi", "data_version": task["data_version"], "result_id": "r-1", "execution_trace_id": "trace-1", "data_status": "DEMO_ONLY"}
            if method == "GET" and url.endswith("/v1/sessions/s-1"):
                return {"session_id": "s-1", "region_id": "shiqi", "data_version": task["data_version"], "data_status": "DEMO_ONLY", "result_ids": ["r-1"]}
            raise AssertionError((method, url, payload))

        outcome = submit_real_c_agent("http://c.local", task, poll_interval_seconds=0, request_fn=fake)
        self.assertEqual(outcome.status, "SUCCEEDED")
        self.assertEqual(outcome.session_id, "s-1")
        evidence = normalize_agent_evidence(outcome)
        self.assertEqual(evidence["execution_trace_id"], "trace-1")
        self.assertEqual(evidence["turn_count"], 1)
        self.assertTrue(any(url.endswith("/v1/sessions/s-1/turns") for _, url, _ in calls))

    def test_missing_c_session_route_is_not_run(self):
        task = build_tasks()[0]

        def missing(method, url, payload, *, timeout_seconds):
            raise HTTPError(url, 404, "not found", {}, io.BytesIO(b"{}"))

        outcome = submit_real_c_agent("http://c.local", task, request_fn=missing)
        self.assertEqual(outcome.status, "NOT_RUN")
        self.assertIn("not available", outcome.reason)


if __name__ == "__main__":
    unittest.main()
