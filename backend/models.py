"""Typed contracts shared by the Module B adapter, planner and API."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ADataAvailability(StrictModel):
    data_status: Literal["MISSING", "DEMO_ONLY", "VERIFIED"]
    expected_paths: dict[str, str]
    existing_paths: list[str]
    missing_paths: list[str]
    notes: list[str] = Field(default_factory=list)


class ADataManifest(StrictModel):
    data_version: str
    source_manifest_hash: str | None = None
    release_status: str | None = None
    manifest_path: str | None = None
    validated_artifacts: list[dict[str, str | int | bool]] = Field(default_factory=list)


class DemandNode(StrictModel):
    demand_id: str
    pop_est: float = Field(ge=0, allow_inf_nan=False)
    population_status: str


class Shelter(StrictModel):
    shelter_id: str
    capacity_persons: float = Field(ge=0, allow_inf_nan=False)
    verified_status: str
    active: bool = True


class ODEdge(StrictModel):
    demand_id: str
    shelter_id: str
    time_min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reachable: bool
    reason: str


class ADataBundle(StrictModel):
    availability: ADataAvailability
    manifest: ADataManifest
    demands: list[DemandNode] = Field(default_factory=list)
    shelters: list[Shelter] = Field(default_factory=list)
    od_edges: list[ODEdge] = Field(default_factory=list)


class PlanningScenario(StrictModel):
    scenario_id: str = "scenario-default"
    parent_scenario_id: str | None = None
    data_version: str
    analysis_mode: Literal["demo", "verified"] = "demo"
    population_demand_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    max_walk_time_min: float = Field(gt=0, allow_inf_nan=False)
    budget_units: float | None = Field(default=None, ge=0, le=1_000_000_000, allow_inf_nan=False)
    disabled_shelter_ids: list[str] = Field(default_factory=list, max_length=500)
    changes: dict[str, object] = Field(default_factory=dict)


class ImprovementOption(StrictModel):
    option_id: str
    shelter_id: str
    action: str
    delta_capacity_persons: float = Field(ge=0, allow_inf_nan=False)
    cost_units: float = Field(ge=0, allow_inf_nan=False)
    eligible: bool
    source_status: str
    hypothetical: bool
    human_confirmed: bool
    combination_policy: Literal["standalone", "stackable", "exclusive_group"]
    exclusive_group: str | None = None

    @field_validator("exclusive_group")
    @classmethod
    def group_required_when_exclusive(cls, value: str | None, info):
        # The cross-field check is performed by candidate_audit to keep the
        # request model's error envelope stable across Pydantic versions.
        return value


class PlanningInput(StrictModel):
    data_version: str
    data_status: Literal["MISSING", "DEMO_ONLY", "VERIFIED", "SYNTHETIC_FIXTURE"]
    demands: list[DemandNode]
    shelters: list[Shelter]
    od_edges: list[ODEdge]
    source_manifest_hash: str | None = None
    source_label: str = "Module A"

    @model_validator(mode="after")
    def validate_relations(self):
        demand_ids = [row.demand_id for row in self.demands]
        shelter_ids = [row.shelter_id for row in self.shelters]
        if len(demand_ids) != len(set(demand_ids)):
            raise ValueError("duplicate demand_id")
        if len(shelter_ids) != len(set(shelter_ids)):
            raise ValueError("duplicate shelter_id")
        if len({(row.demand_id, row.shelter_id) for row in self.od_edges}) != len(self.od_edges):
            raise ValueError("duplicate OD key")
        demand_id_set = set(demand_ids)
        shelter_id_set = set(shelter_ids)
        for edge in self.od_edges:
            if edge.demand_id not in demand_id_set or edge.shelter_id not in shelter_id_set:
                raise ValueError("OD edge has a dangling foreign key")
            if edge.reachable and (edge.time_min is None or edge.reason != "ok"):
                raise ValueError("reachable OD edge requires time_min and reason='ok'")
            if not edge.reachable and edge.reason == "ok":
                raise ValueError("unreachable OD edge cannot use reason='ok'")
        return self


TaskType = Literal[
    "assess", "optimize", "diagnose_intervene", "stress_test", "compare",
    "sensitivity", "explain_result",
]


class PlanNode(StrictModel):
    node_id: str
    skill_id: str
    depends_on: list[str] = Field(default_factory=list)
    skill_version: str
    condition: Literal["always", "eligible_options_present"] = "always"


class ProposedSkillPlan(StrictModel):
    nodes: list[PlanNode] = Field(max_length=32)


class TaskRequest(StrictModel):
    task_id: str | None = Field(default=None, max_length=128)
    result_id: str | None = Field(default=None, max_length=64)
    scenario_id: str | None = Field(default=None, max_length=128)
    scenario: PlanningScenario | None = None
    data_version: str | None = None
    analysis_mode: Literal["demo", "verified"] = "demo"
    task_type: TaskType
    fixture_id: str | None = None
    improvement_options: list[ImprovementOption] = Field(default_factory=list, max_length=12)
    proposed_skill_plan: ProposedSkillPlan | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    parent_scenario_id: str | None = Field(default=None, max_length=128)
    changes: dict[str, object] = Field(default_factory=dict)
    parameters: dict[str, object] = Field(default_factory=dict)

    @field_validator("changes", "parameters")
    @classmethod
    def bounded_json_payload(cls, value: dict[str, object]):
        seen = 0
        def walk(item, depth=0):
            nonlocal seen
            seen += 1
            if seen > 1000 or depth > 8:
                raise ValueError("nested JSON exceeds the supported size/depth")
            if isinstance(item, dict):
                if len(item) > 32:
                    raise ValueError("JSON object contains too many keys")
                for child in item.values(): walk(child, depth + 1)
            elif isinstance(item, list):
                if len(item) > 1000:
                    raise ValueError("JSON list contains too many items")
                for child in item: walk(child, depth + 1)
            elif isinstance(item, str) and len(item) > 4096:
                raise ValueError("JSON string exceeds the supported length")
        walk(value)
        return value


class CheckRecord(StrictModel):
    check_id: str
    object_id: str
    passed: bool
    observed: object = None
    limit: object = None
    tolerance: float | None = None
    evidence_ref: str
    message: str


class VerificationCertificate(StrictModel):
    verification_status: Literal["PASS_DEMO", "PASS_VERIFIED", "BLOCKED", "FAIL"]
    verified_for_demo: bool
    verified_for_real_planning: bool
    checks: list[CheckRecord]


class AssignmentResult(StrictModel):
    solver_status: str
    optimality_proven: bool
    served_demand: float
    total_demand: float
    weighted_walk_time_min: float | None
    assignments: list[dict[str, object]]
    deficits: list[dict[str, object]]
    shelter_loads: dict[str, float]
    shelter_utilization: dict[str, float | None]
    objective_value: float
