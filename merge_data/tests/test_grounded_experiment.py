from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "merge_data" / "tools"))

from backend.errors import CandidateError
from backend.engine import solve_assignment
from backend.models import DemandNode, ImprovementOption, ODEdge, PlanningInput, PlanningScenario, Shelter
from grounded_scenario_generator import generate
from grounded_experiment_runner import (
    expansion_oracle, load_source, maxflow_service, source_hashes, subset_input,
)


def test_generation_is_deterministic_and_preserves_a_hashes():
    before = source_hashes()
    first_manifest, first_candidates = generate()
    second_manifest, second_candidates = generate()
    after = source_hashes()
    assert first_manifest == second_manifest
    assert first_candidates == second_candidates
    assert before == after
    assert first_manifest["case_count"] == 26
    assert first_manifest["data_status"] == "DEMO_ONLY"
    assert first_manifest["gt_c_status"] == "NOT_VALIDATED"
    assert [sum(row["group"] == group for row in first_manifest["cases"]) for group in "ABCD"] == [4, 12, 6, 4]
    assert all(option["hypothetical"] for rows in first_candidates.values() for option in rows if option["option_id"].startswith("HYP_"))


def test_overlay_keeps_source_objects_and_only_selects_existing_a_ids():
    source = load_source()
    manifest = json.loads((ROOT / "merge_data" / "scenarios" / "SCENARIO_MANIFEST.json").read_text(encoding="utf-8"))
    row = next(case for case in manifest["cases"] if case["case_id"] == "A01")
    original_demands = tuple(source.demands)
    original_shelters = tuple(source.shelters)
    original_edges = tuple(source.od_edges)
    subset = subset_input(source, row["overlay"])
    assert source.data_status == subset.data_status == "DEMO_ONLY"
    assert tuple(source.demands) == original_demands
    assert tuple(source.shelters) == original_shelters
    assert tuple(source.od_edges) == original_edges
    assert len(subset.demands) < len(source.demands)
    assert len(subset.shelters) == 2
    assert {edge.demand_id for edge in subset.od_edges} <= {node.demand_id for node in subset.demands}


def test_no_legal_od_cannot_gain_service_from_capacity_oracle():
    data = PlanningInput(
        data_version="unit-v1", data_status="DEMO_ONLY",
        demands=[DemandNode(demand_id="d1", pop_est=100, population_status="demo")],
        shelters=[Shelter(shelter_id="s1", capacity_persons=10, verified_status="unverified")],
        od_edges=[ODEdge(demand_id="d1", shelter_id="s1", time_min=None, reachable=False, reason="no_path")],
    )
    scenario = PlanningScenario(scenario_id="no-edge", data_version="unit-v1", population_demand_ratio=0.2, max_walk_time_min=15)
    assert maxflow_service(data, scenario)["served_demand"] == 0
    assert solve_assignment(data, scenario, capacity_deltas={"s1": 1000}).served_demand == 0


def test_independent_oracle_rejects_unknown_shelter_candidate():
    data = PlanningInput(
        data_version="unit-v1", data_status="DEMO_ONLY",
        demands=[DemandNode(demand_id="d1", pop_est=10, population_status="demo")],
        shelters=[Shelter(shelter_id="s1", capacity_persons=1, verified_status="unverified")],
        od_edges=[ODEdge(demand_id="d1", shelter_id="s1", time_min=5, reachable=True, reason="ok")],
    )
    scenario = PlanningScenario(scenario_id="bad-option", data_version="unit-v1", population_demand_ratio=0.2, max_walk_time_min=15, budget_units=1)
    option = ImprovementOption(option_id="bad", shelter_id="missing", action="expand", delta_capacity_persons=1, cost_units=1, eligible=True, source_status="HYPOTHETICAL", hypothetical=True, human_confirmed=False, combination_policy="standalone")
    with pytest.raises(CandidateError):
        expansion_oracle(data, scenario, [option])
