"""Generate deterministic overlays and hypothetical capacity candidates from A structures."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any

from backend.adapters.a_reader import load_a_bundle
from backend.engine import eligible_edges
from backend.models import ImprovementOption, PlanningInput, PlanningScenario

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "merge_data"
SEED = 20261113
GENERATOR_VERSION = "grounded-exp-0.1.0"
SOURCE_MANIFEST = json.loads((OUT / "source_manifest.json").read_text(encoding="utf-8"))
STRUCTURE = json.loads((OUT / "runs" / "01_structure_audit.json").read_text(encoding="utf-8"))
OVERLAP_PAIRS = STRUCTURE["site_overlap_top20"]
SITE_METRICS = {row["shelter_id"]: row for row in STRUCTURE["site_metrics"]}

CRITICAL_SITE = "zs_1b4df7920b"
EXCLUSIVE_SITE = "zs_fed820d94e"
CAPACITY_PAIR = ("zs_1a562fd2ec", "zs_54cbb64815")
TOP_OVERLAP_PAIRS = [
    ("zs_1b4df7920b", "zs_e016bde4b3"),
    ("zs_6358642b6f", "zs_f7d567aca2"),
    ("zs_1a562fd2ec", "zs_54cbb64815"),
    ("zs_58fc306a1f", "zs_740e2a0ec1"),
]


def load_data() -> PlanningInput:
    bundle = load_a_bundle(ROOT / "configs/pilot.yaml", ROOT)
    if bundle.manifest.data_version != SOURCE_MANIFEST["data_version"]:
        raise RuntimeError("A data version differs from the frozen source manifest.")
    if bundle.availability.data_status != "DEMO_ONLY":
        raise RuntimeError("A status changed; refusing to generate scenarios.")
    return PlanningInput(
        data_version=bundle.manifest.data_version,
        data_status=bundle.availability.data_status,
        demands=bundle.demands,
        shelters=bundle.shelters,
        od_edges=bundle.od_edges,
        source_manifest_hash=bundle.manifest.source_manifest_hash,
        source_label=f"module_a:{bundle.manifest.data_version}",
    )


def make_pairs(site_ids: list[str], mode: str) -> list[tuple[str, str]]:
    if len(site_ids) != 8:
        raise ValueError("Budget profiles require exactly eight existing shelter IDs.")
    if mode == "top_overlap":
        selected = set(site_ids)
        pairs = [pair for pair in TOP_OVERLAP_PAIRS if set(pair).issubset(selected)]
        used = {site for pair in pairs for site in pair}
        remaining = [site for site in site_ids if site not in used]
        # Pair remaining sites by the largest observed Jaccard overlap.
        overlap = {}
        for row in OVERLAP_PAIRS:
            overlap[frozenset((row["shelter_a"], row["shelter_b"]))] = float(row["jaccard"])
        while remaining:
            first = remaining.pop(0)
            if not remaining:
                raise ValueError("Odd shelter candidate profile.")
            second = max(
                remaining,
                key=lambda site: (overlap.get(frozenset((first, site)), 0.0), site),
            )
            remaining.remove(second)
            pairs.append((first, second))
        return pairs
    if mode in {"site_alternatives", "site_budget"}:
        return [(site, "") for site in site_ids]
    raise ValueError(f"Unknown grouping mode: {mode}")


def tier_deltas(capacity: float) -> list[float]:
    if capacity > 500:
        return [25.0, 50.0, 100.0, 200.0]
    return [max(1.0, round(capacity * fraction, 3)) for fraction in (0.10, 0.25, 0.50, 1.0)]


def build_options(
    case_id: str,
    site_ids: list[str],
    option_count: int,
    grouping_mode: str,
    profile_index: int,
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    if option_count not in {8, 16, 32} or len(site_ids) != 8:
        raise ValueError("Expected 8 candidate sites and option count in {8, 16, 32}.")
    tiers = option_count // len(site_ids)
    groups = make_pairs(site_ids, "top_overlap" if grouping_mode == "cross_shelter" else "site_alternatives")
    site_to_group: dict[str, str] = {}
    if grouping_mode == "cross_shelter":
        for index, pair in enumerate(groups, 1):
            for site in pair:
                site_to_group[site] = f"{case_id}_shared_portfolio_{index}"
    else:
        for site in site_ids:
            site_to_group[site] = f"{case_id}_site_alternative_{site}"

    rng = random.Random(SEED + profile_index * 1009 + option_count)
    option_rows: list[dict[str, Any]] = []
    for site_index, sid in enumerate(site_ids):
        metric = SITE_METRICS[sid]
        capacity = float(metric["capacity_persons_unverified"])
        deltas = tier_deltas(capacity)
        if tiers == 1:
            chosen_tiers = [(site_index + profile_index) % len(deltas)]
        elif tiers == 2:
            chosen_tiers = [0, 2]
        else:
            chosen_tiers = [0, 1, 2, 3]
        for tier_index in chosen_tiers:
            delta = deltas[tier_index]
            cost = float(max(1, math.ceil(delta / 25.0) + tier_index + rng.randint(0, 3)))
            policy = "exclusive_group" if (tiers > 1 or grouping_mode == "cross_shelter") else "standalone"
            row = ImprovementOption(
                option_id=f"HYP_{case_id}_{sid}_{tier_index + 1}",
                shelter_id=sid,
                action="abstract_capacity_increment",
                delta_capacity_persons=delta,
                cost_units=cost,
                eligible=True,
                source_status="HYPOTHETICAL_ABSTRACT_COST_AND_CAPACITY",
                hypothetical=True,
                human_confirmed=False,
                combination_policy=policy,
                exclusive_group=site_to_group[sid] if policy == "exclusive_group" else None,
            ).model_dump()
            option_rows.append(row)

    # The budget is derived from the option groups: it admits two cheapest group-minimum projects,
    # rather than reusing the former fixed [0, 1, 3, 6] curve.
    group_minimums: dict[str, float] = {}
    for row in option_rows:
        group = row["exclusive_group"] or f"single:{row['shelter_id']}"
        group_minimums[group] = min(group_minimums.get(group, float("inf")), float(row["cost_units"]))
    cheapest = sorted(group_minimums.values())
    budget = float(sum(cheapest[:2]))
    if len(option_rows) != option_count:
        raise AssertionError(f"{case_id}: expected {option_count} options, generated {len(option_rows)}")
    return option_rows, budget, {
        "grouping_mode": grouping_mode,
        "group_count": len(group_minimums),
        "group_minimum_costs": sorted(cheapest),
        "budget_rule": "sum of the two lowest exclusive-group minimum costs",
        "budget_units": budget,
        "candidate_cost_units_are_abstract": True,
    }


def case_record(
    case_id: str,
    group: str,
    description: str,
    *,
    ratio: float = 0.20,
    threshold: float = 15.0,
    budget: float | None = None,
    disabled: list[str] | None = None,
    demand_ids: list[str] | None = None,
    shelter_ids: list[str] | None = None,
    candidate_path: str | None = None,
    candidate_count: int = 0,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "group": group,
        "description": description,
        "seed": SEED,
        "generator_version": GENERATOR_VERSION,
        "data_version": SOURCE_MANIFEST["data_version"],
        "data_status": "DEMO_ONLY",
        "source_manifest_sha256": SOURCE_MANIFEST["source_manifest_sha256"],
        "overlay": {
            "scenario_id": f"scenario-{case_id}",
            "analysis_mode": "demo",
            "population_demand_ratio": ratio,
            "max_walk_time_min": threshold,
            "budget_units": budget,
            "disabled_shelter_ids": disabled or [],
            "selected_demand_ids": demand_ids,
            "selected_shelter_ids": shelter_ids,
        },
        "candidate_path": candidate_path,
        "candidate_count": candidate_count,
        "source_data_mutation": False,
        "quality_status": "PENDING_PRECHECK",
        "gt_c_status": "NOT_VALIDATED",
        "expected_behavior": expected or {},
    }


def generate() -> dict[str, Any]:
    data = load_data()
    demands = {row.demand_id: row for row in data.demands}
    shelters = {row.shelter_id: row for row in data.shelters}
    base = PlanningScenario(
        scenario_id="generator-base", data_version=data.data_version, analysis_mode="demo",
        population_demand_ratio=0.20, max_walk_time_min=15.0,
    )
    edges = eligible_edges(data, base)
    by_demand: dict[str, set[str]] = {did: set() for did in demands}
    by_site: dict[str, set[str]] = {sid: set() for sid in shelters}
    for did, sid, _time in edges:
        by_demand[did].add(sid)
        by_site[sid].add(did)

    # A01: a real 63-demand overlap between two saturated low-capacity shelters.
    shared_pair = CAPACITY_PAIR
    shared = sorted(by_site[shared_pair[0]] & by_site[shared_pair[1]])
    if len(shared) < 20:
        raise RuntimeError("Observed capacity-competition pair no longer meets the frozen criterion.")

    # A02: nodes whose only legal 15-minute destination is the source shelter; choose largest demand nodes.
    exclusive = [
        did for did, connected in by_demand.items()
        if connected == {EXCLUSIVE_SITE}
    ]
    exclusive.sort(key=lambda did: (-demands[did].pop_est, did))
    exclusive = exclusive[:120]
    if len(exclusive) < 50:
        raise RuntimeError("Insufficient observed single-destination demand for A02.")

    # D01: real demand nodes with no legal 15-minute connection to any shelter.
    no_edge = [did for did, connected in by_demand.items() if not connected]
    no_edge.sort(key=lambda did: (-demands[did].pop_est, did))
    no_edge = no_edge[:120]
    if len(no_edge) < 50:
        raise RuntimeError("Insufficient observed no-legal-edge demand for D01.")

    marginal_sites = [
        row["shelter_id"] for row in STRUCTURE["site_metrics"]
        if row["bump_25pct_served_gain"] > 0
    ]
    marginal_sites = marginal_sites[:8]
    if len(marginal_sites) != 8:
        raise RuntimeError("Need eight sites with measured positive marginal service response.")
    overlap_sites = []
    for pair in TOP_OVERLAP_PAIRS:
        for sid in pair:
            if sid not in overlap_sites:
                overlap_sites.append(sid)
    overlap_sites = overlap_sites[:8]
    critical_mix = [CRITICAL_SITE]
    for sid in marginal_sites:
        if sid not in critical_mix:
            critical_mix.append(sid)
    critical_mix = critical_mix[:8]
    site_profiles = {
        "marginal": marginal_sites,
        "overlap": overlap_sites,
        "critical_mix": critical_mix,
        "threshold_mix": marginal_sites[:6] + [sid for sid in overlap_sites if sid not in marginal_sites][:2],
    }

    definitions: list[dict[str, Any]] = []
    candidate_files: dict[str, list[dict[str, Any]]] = {}
    candidate_meta: dict[str, dict[str, Any]] = {}

    # Group A: direct observations from the A graph / solver.
    definitions.append(case_record(
        "A01", "A", "Shared-demand capacity competition on an observed two-shelter overlap.",
        demand_ids=shared, shelter_ids=list(shared_pair),
        expected={"required_behavior": "preserve original 63 demand IDs and 126 original pair OD rows; show capacity-limited allocation and PASS_DEMO"},
    ))
    definitions.append(case_record(
        "A02", "A", "Low-connectivity catchment: selected demands have exactly one legal 15-minute shelter.",
        demand_ids=exclusive, shelter_ids=sorted(shelters),
        expected={"required_behavior": f"all selected demands retain their sole 15-minute destination {EXCLUSIVE_SITE}; no alternate route may be invented"},
    ))
    definitions.append(case_record(
        "A03", "A", "Critical-site closure on the full A planning graph.",
        disabled=[CRITICAL_SITE],
        expected={"required_behavior": "compare the full-data baseline with the same OD graph and one disabled source shelter"},
    ))
    definitions.append(case_record(
        "A04", "A", "Walk-threshold sensitivity at 10, 15, and 20 minutes with every other parameter fixed.",
        expected={"required_behavior": "three same-version scenarios; report actual eligible edge counts, service deltas, solver and verifier status"},
    ))

    profiles = [
        ("diverse_marginal", "marginal", "site_alternatives", 0.20, 15.0),
        ("observed_overlap_conflicts", "overlap", "cross_shelter", 0.20, 15.0),
        ("critical_site_decoy", "critical_mix", "cross_shelter", 0.30, 15.0),
        ("tight_budget_threshold", "threshold_mix", "site_budget", 0.25, 10.0),
    ]
    profile_index = 0
    for option_count, size_name in ((8, "easy"), (16, "medium"), (32, "hard")):
        for variant, (profile_name, sites_key, grouping, ratio, threshold) in enumerate(profiles, 1):
            case_id = f"B{option_count:02d}-{variant:02d}"
            options, budget, budget_rule = build_options(
                case_id, site_profiles[sites_key], option_count, grouping, profile_index,
            )
            candidate_path = f"candidates/{case_id}.json"
            candidate_files[candidate_path] = options
            candidate_meta[case_id] = budget_rule
            definitions.append(case_record(
                case_id, "B",
                f"{size_name} budget optimization, {profile_name}, {option_count} HYPOTHETICAL candidate options.",
                ratio=ratio, threshold=threshold, budget=budget,
                shelter_ids=site_profiles[sites_key], candidate_path=candidate_path,
                candidate_count=option_count,
                expected={
                    "required_behavior": "select only eligible projects within abstract budget and explicit exclusive groups; verify assignments independently",
                    "difficulty_profile": profile_name,
                    "hypothetical_cost_capacity": True,
                    "candidate_budget_construction": budget_rule,
                },
            ))
            profile_index += 1

    # Group C: composite, sequential, comparison and stress scenarios.
    b8 = candidate_files["candidates/B08-01.json"]
    b16 = candidate_files["candidates/B16-02.json"]
    b32 = candidate_files["candidates/B32-02.json"]
    for case_id, desc, ratio, threshold, disabled, opts, source_case in [
        ("C01", "Close critical site; compare frozen selected-project plan with adaptive replanning.", 0.20, 15.0, [CRITICAL_SITE], b8, "B08-01"),
        ("C02", "Increase demand ratio while holding data version, walk threshold, options and budget fixed.", 0.35, 15.0, [], b16, "B16-02"),
        ("C03", "Tighten the walk cutoff and re-optimize the 32-option portfolio.", 0.20, 10.0, [], b32, "B32-02"),
        ("C04", "Compare 10- and 15-minute thresholds on the same full A source.", 0.20, 15.0, [], [], ""),
        ("C05", "Sequentially update demand ratio then walk threshold with parent scenario lineage.", 0.30, 20.0, [], [], ""),
        ("C06", "Compare two distinct HYPOTHETICAL portfolios under the same cost budget.", 0.20, 15.0, [], b8, "B08-01"),
    ]:
        candidate_path = None
        count = len(opts)
        budget = None
        if opts:
            src_row = next(row for row in definitions if row["case_id"] == source_case)
            budget = src_row["overlay"]["budget_units"]
            candidate_path = f"candidates/{case_id}.json"
            candidate_files[candidate_path] = opts
            candidate_meta[case_id] = {
                "source_candidate_case": source_case,
                "budget_units": budget,
                "comparison_portfolio": case_id == "C06",
            }
        same_portfolio_scenario_shelters = None
        if case_id in {"C02", "C03"}:
            same_portfolio_scenario_shelters = next(row for row in definitions if row["case_id"] == source_case)["overlay"]["selected_shelter_ids"]
        definitions.append(case_record(
            case_id, "C", desc, ratio=ratio, threshold=threshold, disabled=disabled,
            shelter_ids=same_portfolio_scenario_shelters,
            budget=budget, candidate_path=candidate_path, candidate_count=count,
            expected={
                "required_behavior": "retain source data version and DEMO_ONLY status; change only stated overlay fields; verify each branch",
                "source_candidate_case": source_case or None,
                "parent_scenario_required": case_id == "C05",
            },
        ))

    # C06 explicitly compares two different portfolios under the same fixed budget.
    c06 = next(row for row in definitions if row["case_id"] == "C06")
    candidate_files["candidates/C06_alternative.json"] = candidate_files["candidates/B08-02.json"]
    c06["comparison_candidate_path"] = "candidates/C06_alternative.json"
    c06["expected_behavior"]["second_portfolio_source_case"] = "B08-02"
    c06["expected_behavior"]["required_behavior"] = "optimize both distinct portfolios under C06's unchanged budget; preserve the same A source version and verify both"

    # Group D: a real zero-edge case, invalid input, explicitly fault-injected timeout, and verifier corruption.
    d1 = candidate_files["candidates/B08-01.json"]
    d1_row = next(row for row in definitions if row["case_id"] == "B08-01")
    candidate_files["candidates/D01.json"] = d1
    definitions.append(case_record(
        "D01", "D", "No legal 15-minute edge: capacity projects must not manufacture service.",
        demand_ids=no_edge, shelter_ids=sorted(shelters),
        ratio=0.20, threshold=15.0, budget=d1_row["overlay"]["budget_units"],
        candidate_path="candidates/D01.json", candidate_count=len(d1),
        expected={"required_behavior": "serve zero through the preserved A OD graph; select no no-benefit project; independent flow oracle is zero"},
    ))
    invalid = [dict(
        option_id="INVALID_UNKNOWN_SHELTER",
        shelter_id="synthetic_not_an_a_shelter",
        action="abstract_capacity_increment",
        delta_capacity_persons=25.0,
        cost_units=1.0,
        eligible=True,
        source_status="HYPOTHETICAL_INVALID_INPUT_TEST",
        hypothetical=True,
        human_confirmed=False,
        combination_policy="standalone",
        exclusive_group=None,
    )]
    candidate_files["candidates/D02_invalid.json"] = invalid
    definitions.append(case_record(
        "D02", "D", "Invalid candidate references a shelter ID absent from A.",
        candidate_path="candidates/D02_invalid.json", candidate_count=1, budget=1.0,
        expected={"required_behavior": "B must reject before optimization; no result is publishable; this is a deliberate invalid-input fixture"},
    ))
    candidate_files["candidates/D03.json"] = d1
    definitions.append(case_record(
        "D03", "D", "Engineering fault test: inject a time-limit incumbent into B's MIP call.",
        candidate_path="candidates/D03.json", candidate_count=len(d1),
        budget=d1_row["overlay"]["budget_units"],
        expected={"required_behavior": "status remains FEASIBLE and optimality_proven=false; never relabel injected incumbent as OPTIMAL", "run_kind": "ENGINEERING_FAULT_TEST_NOT_NATURAL_TIMEOUT"},
    ))
    definitions.append(case_record(
        "D04", "D", "Tamper with a valid assignment and require the independent verifier to reject it.",
        expected={"required_behavior": "mutated demand conservation or illegal edge must return FAIL; no output may be promoted"},
    ))

    if len(definitions) != 26:
        raise AssertionError(f"Expected 26 cases, generated {len(definitions)}")

    # Add a precise, reproducible data-selection recipe to each case.
    for row in definitions:
        row["overlay"]["demand_selection_rule"] = (
            "explicit original A demand IDs in this record"
            if row["overlay"]["selected_demand_ids"] is not None
            else "all original A demand IDs"
        )
        row["overlay"]["shelter_selection_rule"] = (
            "explicit original A shelter IDs in this record"
            if row["overlay"]["selected_shelter_ids"] is not None
            else "all original A shelter IDs"
        )
        row["ground_truth_path"] = f"gt/{row['case_id']}.json"

    return {
        "schema": "grounded-planning-scenarios/v1",
        "generator_version": GENERATOR_VERSION,
        "seed": SEED,
        "data_version": SOURCE_MANIFEST["data_version"],
        "data_status": "DEMO_ONLY",
        "source_manifest_sha256": SOURCE_MANIFEST["source_manifest_sha256"],
        "gt_c_status": "NOT_VALIDATED",
        "case_count": len(definitions),
        "cases": definitions,
        "candidate_group_budget_rules": candidate_meta,
    }, candidate_files


def main() -> int:
    manifest, candidate_files = generate()
    (OUT / "scenarios").mkdir(parents=True, exist_ok=True)
    (OUT / "candidates").mkdir(parents=True, exist_ok=True)
    (OUT / "scenarios" / "SCENARIO_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    for relative, rows in candidate_files.items():
        target = OUT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "case_count": manifest["case_count"],
        "groups": dict(Counter(row["group"] for row in manifest["cases"])),
        "candidate_cases": {row["case_id"]: row["candidate_count"] for row in manifest["cases"] if row["candidate_count"]},
        "source_data_version": manifest["data_version"],
        "gt_c_status": manifest["gt_c_status"],
        "scenarios": "merge_data/scenarios/SCENARIO_MANIFEST.json",
        "candidate_files": len(candidate_files),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
