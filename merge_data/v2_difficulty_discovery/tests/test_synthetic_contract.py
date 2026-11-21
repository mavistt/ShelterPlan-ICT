"""Regression checks for the V2 isolated synthetic fixture contract."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "merge_data" / "v2_difficulty_discovery"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "merge_data" / "tools"))
sys.path.insert(0, str(OUT / "tools"))

from backend.engine import optimize_budgeted
from backend.models import ImprovementOption, PlanningInput, PlanningScenario
from backend.verifier.checks import verify_budgeted
from grounded_experiment_runner import expansion_oracle
from run_synthetic_stress import greedy_primary


def load_fixture(name: str):
    document = json.loads((OUT / "scenarios" / "synthetic_stress" / name).read_text(encoding="utf-8"))
    assert document["provenance"] == "FULLY_SYNTHETIC_STRESS"
    assert document["planning_input"]["source_label"] == "FULLY_SYNTHETIC_STRESS"
    data = PlanningInput(**document["planning_input"])
    scenario = PlanningScenario(**document["scenario"])
    options = [ImprovementOption(**row) for row in document["options"]]
    return document, data, scenario, options


def test_all_synthetic_stress_sizes_are_segregated_and_bounded():
    for kind in ("simple", "competitive"):
        for size in (8, 16, 32, 48, 64):
            _document, data, _scenario, options = load_fixture(f"{kind}_n{size}.json")
            assert data.data_status == "SYNTHETIC_FIXTURE"
            assert len(options) == size
            assert all(row.hypothetical and row.source_status == "FULLY_SYNTHETIC_STRESS" for row in options)


def test_independent_oracle_confirms_controlled_greedy_counterexample():
    _document, data, scenario, options = load_fixture("greedy_counterexample_n8.json")
    greedy = greedy_primary(data, scenario, options, scenario.budget_units)
    mip = optimize_budgeted(data, scenario, options, scenario.budget_units)
    verifier = verify_budgeted(data, scenario, options, mip, scenario.budget_units)
    oracle = expansion_oracle(data, scenario, options)
    assert mip["solver_status"] == "OPTIMAL"
    assert verifier["verification_status"] == "PASS_DEMO"
    assert oracle["oracle_status"] == "ENUMERATED_MAXFLOW_PROVEN"
    assert abs(mip["objective_value"] - oracle["objective_served_demand"]) <= oracle["rounding_error_upper_bound_persons"]
    assert oracle["objective_served_demand"] - greedy["served_demand"] > oracle["rounding_error_upper_bound_persons"]


def test_real_gis_search_kept_source_hashes_unchanged():
    result_path = OUT / "oracles" / "phase_b_grounded_search.json"
    if not result_path.exists():
        result_path = OUT / "oracles" / "phase_b_grounded_search_summary.json"
    assert result_path.exists()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["source_hashes_unchanged"] is True
    assert result["limits"]["cheap_prefilters_run"] <= 100
    assert result["limits"]["full_mips_run"] <= 30
    assert sum(row["selected_for_full_optimization"] for row in result["screening_records"]) == result["limits"]["full_mips_run"]
    assert len(result["completed_cases"]) == result["limits"]["full_mips_run"]
    assert {row["candidate_id"] for row in result["completed_cases"] if row["greedy_failure_confirmed"]} == {"REAL-000-TARGETED-TRAP", "REAL-081"}
