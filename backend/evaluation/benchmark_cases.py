"""Deterministic, family-separated synthetic evaluation cases for Module B."""
from __future__ import annotations

from dataclasses import dataclass

from backend.models import DemandNode, ImprovementOption, ODEdge, PlanningInput, PlanningScenario, Shelter


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    template_family: str
    split: str
    difficulty: str
    data: PlanningInput
    scenario: PlanningScenario
    options: tuple[ImprovementOption, ...]
    expected_behavior: str
    invalid_payload: dict | None = None


FAMILY_SPLITS = {
    "capacity_competition": "dev",
    "network_disconnection": "dev",
    "walk_threshold_cutoff": "dev",
    "demand_ratio": "dev",
    "budget_expansion": "validation",
    "exclusive_group": "validation",
    "disabled_shelter": "validation",
    "scenario_version": "test",
    "invalid_input": "test",
    "verifier_fault": "test",
}


def _option(option_id, shelter_id, delta, cost, policy="standalone", group=None):
    return ImprovementOption(
        option_id=option_id, shelter_id=shelter_id, action="hypothetical_capacity_increase",
        delta_capacity_persons=float(delta), cost_units=float(cost), eligible=True,
        source_status="synthetic_fixture", hypothetical=True, human_confirmed=False,
        combination_policy=policy, exclusive_group=group,
    )


def _case(family: str, variant: int) -> BenchmarkCase:
    case_id = f"{family}_{variant:02d}"
    population_scale = variant
    populations = [40 + 4 * population_scale, 32 + 4 * population_scale, 24 + 4 * population_scale]
    shelters = [
        Shelter(shelter_id="S1", capacity_persons=25 + 2 * population_scale, verified_status="synthetic_fixture"),
        Shelter(shelter_id="S2", capacity_persons=20 + 2 * population_scale, verified_status="synthetic_fixture"),
        Shelter(shelter_id="S3", capacity_persons=16 + population_scale, verified_status="synthetic_fixture"),
    ]
    demands = [
        DemandNode(demand_id=f"D{index}", pop_est=population, population_status="synthetic_fixture")
        for index, population in enumerate(populations, start=1)
    ]
    edge_specs = [
        ("D1", "S1", 5.0), ("D1", "S2", 9.0),
        ("D2", "S1", 6.0), ("D2", "S2", 7.0), ("D2", "S3", 12.0),
        ("D3", "S2", 8.0), ("D3", "S3", 10.0),
    ]
    ratio = 1.0
    threshold = 15.0
    disabled = []
    budget = None
    options: list[ImprovementOption] = []
    expected_behavior = "integer primary objective equals independent max-flow oracle"
    invalid_payload = None

    if family == "capacity_competition":
        shelters[0] = shelters[0].model_copy(update={"capacity_persons": 18 + 2 * population_scale})
        shelters[1] = shelters[1].model_copy(update={"capacity_persons": 14 + population_scale})
        expected_behavior = "finite shelter capacities constrain shared demand"
    elif family == "network_disconnection":
        edge_specs = [row for row in edge_specs if row[:2] not in {("D3", "S2"), ("D3", "S3")}]
        edge_specs.extend([("D3", "S2", None), ("D3", "S3", None)])
        expected_behavior = "D3 remains unreachable even with unused capacity"
    elif family == "walk_threshold_cutoff":
        threshold = [5.0, 8.0, 10.0, 12.0][variant - 1]
        edge_specs = [
            (did, sid, threshold if (did, sid) == ("D1", "S1") else threshold + 0.001 if (did, sid) == ("D1", "S2") else value)
            for did, sid, value in edge_specs
        ]
        expected_behavior = "an edge exactly on threshold is eligible; a just-over edge is excluded"
    elif family == "demand_ratio":
        ratio = [0.25, 0.5, 0.75, 1.0][variant - 1]
        expected_behavior = "integer demand scaling preserves max-flow oracle comparability"
    elif family == "budget_expansion":
        budget = float(variant)
        options = [
            _option("CAP_S1", "S1", 12 + variant, 1),
            _option("CAP_S2", "S2", 10 + variant, 2),
            _option("CAP_S3", "S3", 8 + variant, 1),
        ]
        expected_behavior = "selected hypothetical options maximize served demand within budget"
    elif family == "exclusive_group":
        budget = 2.0
        options = [
            _option("GROUP_S1", "S1", 18 + variant, 1, "exclusive_group", "mutually-exclusive-sites"),
            _option("GROUP_S2", "S2", 18 + variant, 1, "exclusive_group", "mutually-exclusive-sites"),
            _option("EXTRA_S3", "S3", 5 + variant, 1),
        ]
        expected_behavior = "at most one option from a named exclusive group can be selected"
    elif family == "disabled_shelter":
        disabled = [["S1"], ["S2"], ["S3"], ["S1", "S2"]][variant - 1]
        expected_behavior = "disabled shelters receive no assigned flow"
    elif family == "scenario_version":
        expected_behavior = "a mismatched data version is safely rejected"
    elif family == "invalid_input":
        invalid_payload = {"population_demand_ratio": 1.0 + variant / 10.0}
        expected_behavior = "out-of-range scenario input is rejected before planning"
    elif family == "verifier_fault":
        expected_behavior = "independent verifier detects injected assignment and identity faults"
    else:
        raise ValueError(f"Unknown benchmark family: {family}")

    edges = []
    for did, sid, minutes in edge_specs:
        if minutes is None:
            edges.append(ODEdge(demand_id=did, shelter_id=sid, time_min=None, distance_m=None, reachable=False, reason="no_path"))
        else:
            edges.append(ODEdge(demand_id=did, shelter_id=sid, time_min=minutes, distance_m=minutes * 66, reachable=True, reason="ok"))
    data = PlanningInput(
        data_version=f"benchmark_{family}_v1", data_status="SYNTHETIC_FIXTURE",
        demands=demands, shelters=shelters, od_edges=edges,
        source_label=f"benchmark:{family}:variant-{variant}",
    )
    scenario = PlanningScenario(
        scenario_id=case_id, data_version=data.data_version, analysis_mode="demo",
        population_demand_ratio=ratio, max_walk_time_min=threshold,
        budget_units=budget, disabled_shelter_ids=disabled,
    )
    return BenchmarkCase(
        case_id=case_id, template_family=family, split=FAMILY_SPLITS[family],
        difficulty="easy" if variant <= 2 else "medium", data=data, scenario=scenario,
        options=tuple(options), expected_behavior=expected_behavior, invalid_payload=invalid_payload,
    )


def build_cases() -> list[BenchmarkCase]:
    """Return 40 frozen-by-code cases; a template family belongs to one split."""
    return [_case(family, variant) for family in FAMILY_SPLITS for variant in range(1, 5)]
