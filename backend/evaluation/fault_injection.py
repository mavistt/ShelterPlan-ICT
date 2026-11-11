"""Offline fault injection helpers used by verifier tests and reports."""
from __future__ import annotations

from backend.models import AssignmentResult, PlanningInput, PlanningScenario
from backend.engine import optimize_budgeted
from backend.verifier.checks import verify_assignment, verify_budgeted
from backend.verifier.outcome import verify_assumptions_disclosed, verify_result_identity, verify_summary_claims


def run_fault_campaign(data: PlanningInput, scenario: PlanningScenario, clean: AssignmentResult, options: list | None = None) -> dict:
    clean_certificate = verify_assignment(data, scenario, clean)
    cases = []
    def record(name, detected, detail):
        cases.append({"fault": name, "detected": detected, "detail": detail})

    record("clean_case", clean_certificate.verification_status != "PASS_DEMO", clean_certificate.verification_status)
    bad_rows = [dict(row) for row in clean.assignments]
    if bad_rows:
        bad_rows[0]["persons"] = float(bad_rows[0]["persons"]) + 1
    over = clean.model_copy(update={"assignments": bad_rows})
    cert = verify_assignment(data, scenario, over)
    record("assignment_over_capacity_or_demand", cert.verification_status == "FAIL", [c.check_id for c in cert.checks if not c.passed])

    if clean.assignments:
        rows = [dict(row) for row in clean.assignments]
        rows[0]["persons"] = -1
        cert = verify_assignment(data, scenario, clean.model_copy(update={"assignments": rows}))
        record("negative_assignment", cert.verification_status == "FAIL", [c.check_id for c in cert.checks if not c.passed])
        rows = [dict(row) for row in clean.assignments]
        rows[0]["shelter_id"] = "__tampered_shelter__"
        cert = verify_assignment(data, scenario, clean.model_copy(update={"assignments": rows}))
        record("tampered_shelter_id", cert.verification_status == "FAIL", [c.check_id for c in cert.checks if not c.passed])
        rows = [dict(row) for row in clean.assignments]
        first = next((row for row in rows if row["demand_id"] == "D1"), rows[0])
        first["shelter_id"] = "B" if first["shelter_id"] != "B" else "A"
        cert = verify_assignment(data, scenario, clean.model_copy(update={"assignments": rows}))
        record("illegal_od_edge", cert.verification_status == "FAIL", [c.check_id for c in cert.checks if not c.passed])
        assigned_shelters = sorted({row["shelter_id"] for row in clean.assignments})
        if assigned_shelters:
            disabled_scenario = scenario.model_copy(update={"disabled_shelter_ids": assigned_shelters[:1]})
            cert = verify_assignment(data, disabled_scenario, clean)
            record("disabled_shelter", cert.verification_status == "FAIL", [c.check_id for c in cert.checks if not c.passed])
    wrong_version = scenario.model_copy(update={"data_version": "tampered-version"})
    cert = verify_assignment(data, wrong_version, clean)
    record("wrong_data_version", cert.verification_status == "FAIL", [c.check_id for c in cert.checks if not c.passed])
    if options and scenario.budget_units is not None:
        budgeted = optimize_budgeted(data, scenario, options, scenario.budget_units)
        budget_check = verify_budgeted(data, scenario, options, budgeted, 0.0)
        record("over_budget", any(not row["passed"] for row in budget_check["checks"]), [row["check_id"] for row in budget_check["checks"] if not row["passed"]])
    identity = verify_result_identity("result-expected", "result-other")
    record("wrong_result_id", not identity["passed"], identity)
    summary = {"served_demand": 10, "total_demand": 20, "weighted_walk_time_min": 5}
    claims = {"served_demand": 11, "total_demand": 20, "weighted_walk_time_min": 5}
    claims_check = verify_summary_claims(summary, claims)
    record("tampered_summary", any(not row["passed"] for row in claims_check), claims_check)
    assumptions = verify_assumptions_disclosed(["synthetic_fixture", "walking_speed"], ["synthetic_fixture"])
    record("omitted_assumption", not assumptions["passed"], assumptions)
    return {
        "case_count": len(cases), "detected_faults": sum(1 for row in cases if row["fault"] != "clean_case" and row["detected"]),
        "fault_count": sum(1 for row in cases if row["fault"] != "clean_case"),
        "fault_detection_recall": sum(1 for row in cases if row["fault"] != "clean_case" and row["detected"]) / max(1, sum(1 for row in cases if row["fault"] != "clean_case")),
        "false_positive_clean_case": cases[0]["detected"], "cases": cases,
    }
