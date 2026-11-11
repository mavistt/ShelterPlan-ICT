"""Deterministic fallback planner and validator for C-proposed DAGs."""
from __future__ import annotations

from dataclasses import dataclass
import networkx as nx

from backend.errors import PlanRejectedError
from backend.graph.registry import (
    COMPOSITES, PREREQUISITES, SKILL_GRAPH_VERSION, SKILL_VERSION,
    TASK_COMPOSITE,
)
from backend.models import ProposedSkillPlan


@dataclass
class PlanDecision:
    status: str
    reason_codes: list[str]
    nodes: list[dict]
    composite_id: str


def fallback_plan(task_type: str, has_candidates: bool = True) -> PlanDecision:
    composite_id = TASK_COMPOSITE[task_type]
    skill_ids = ["S01", "S02", "S03", "S04", "S10", "S12"] if task_type == "sensitivity" else COMPOSITES[composite_id][1]
    if task_type == "diagnose_intervene" and not has_candidates:
        skill_ids = ["S01", "S02", "S03", "S04", "S05", "S12"]
    nodes = []
    prior_by_skill = {}
    for index, skill_id in enumerate(skill_ids):
        needed = [prior_by_skill[s] for s in PREREQUISITES.get(skill_id, set()) if s in prior_by_skill]
        if index > 0:
            # Preserve an explicit executable order in each composite DAG.
            previous = f"n{index}"
            if previous not in needed:
                needed.append(previous)
        node_id = f"n{index + 1}"
        condition = "eligible_options_present" if task_type == "diagnose_intervene" and skill_id in {"S06", "S07"} else "always"
        nodes.append({"node_id": node_id, "skill_id": skill_id, "depends_on": needed, "skill_version": SKILL_VERSION, "condition": condition})
        prior_by_skill[skill_id] = node_id
    return PlanDecision("fallback", ["DETERMINISTIC_FALLBACK"], nodes, composite_id)


def validate_plan(task_type: str, proposed: ProposedSkillPlan | None, has_candidates: bool = True) -> PlanDecision:
    fallback = fallback_plan(task_type, has_candidates)
    if proposed is None:
        return fallback
    nodes = [node.model_dump() for node in proposed.nodes]
    ids = [node["node_id"] for node in nodes]
    if len(ids) != len(set(ids)):
        raise PlanRejectedError("Proposed plan has duplicate node IDs", details={"reason_code": "DUPLICATE_NODE"})
    known = {node["skill_id"] for node in nodes}
    unknown = sorted(known - {f"S{i:02d}" for i in range(1, 13)})
    if unknown:
        raise PlanRejectedError("Proposed plan contains unregistered skills", details={"reason_code": "UNKNOWN_SKILL", "skills": unknown})
    if any(node["skill_version"] != SKILL_VERSION for node in nodes):
        raise PlanRejectedError("Proposed plan uses an incompatible skill version", details={"reason_code": "SKILL_VERSION_MISMATCH"})
    id_set = set(ids)
    graph = nx.DiGraph()
    graph.add_nodes_from(ids)
    for node in nodes:
        for dep in node["depends_on"]:
            if dep not in id_set:
                raise PlanRejectedError("Proposed plan references an unknown dependency", details={"reason_code": "UNKNOWN_DEPENDENCY", "node_id": node["node_id"], "dependency": dep})
            graph.add_edge(dep, node["node_id"])
    if not nx.is_directed_acyclic_graph(graph):
        raise PlanRejectedError("Proposed plan contains a cycle", details={"reason_code": "CYCLE"})
    node_for_skill = {node["skill_id"]: node for node in nodes}
    for skill_id, node in node_for_skill.items():
        for required in PREREQUISITES.get(skill_id, set()):
            if required not in node_for_skill:
                # Missing required skill is repaired with the safe deterministic template.
                return PlanDecision("rewritten", ["MISSING_REQUIRED_PREREQUISITE"], fallback.nodes, fallback.composite_id)
            ancestors = nx.ancestors(graph, node["node_id"])
            if node_for_skill[required]["node_id"] not in ancestors:
                raise PlanRejectedError("Required skill dependency is not satisfied", details={"reason_code": "DEPENDENCY_BYPASS", "skill_id": skill_id, "required_skill_id": required})
    required_skills = {node["skill_id"] for node in fallback.nodes}
    unexpected = sorted(known - required_skills)
    if unexpected:
        raise PlanRejectedError("Proposed plan contains skills outside this task contract", details={"reason_code": "TASK_GOAL_MISMATCH", "skills": unexpected})
    if not required_skills.issubset(known):
        return PlanDecision("rewritten", ["TASK_TEMPLATE_INCOMPLETE"], fallback.nodes, fallback.composite_id)
    if not set(fallback.nodes[-1]["skill_id"] for _ in [0]).issubset(known):
        return PlanDecision("rewritten", ["MISSING_EVIDENCE_PACKAGING"], fallback.nodes, fallback.composite_id)
    if [(node["skill_id"], node["condition"]) for node in nodes] != [(node["skill_id"], node["condition"]) for node in fallback.nodes]:
        return PlanDecision("rewritten", ["CANONICAL_EXECUTION_ORDER"], fallback.nodes, fallback.composite_id)
    return PlanDecision("accepted", ["PLAN_WITHIN_REGISTERED_CONTRACT"], nodes, fallback.composite_id)
