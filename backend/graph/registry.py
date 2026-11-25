"""Versioned, finite Skill Registry and composite task templates."""
from __future__ import annotations

from backend.engine import MAX_BUDGET_OPTIONS, MAX_BUDGET_SOLVE_SECONDS

SKILLS = {
    "S01": ("DataEligibility", "Check source quality, data mode and publication eligibility."),
    "S02": ("ScenarioNormalize", "Validate explicit scenario inputs and record defaults."),
    "S03": ("ReachabilityGraph", "Build the sparse eligible OD relation for the selected threshold."),
    "S04": ("CapacityAssignment", "Maximize served demand, then minimize walk time."),
    "S05": ("BottleneckDiagnosis", "Separate connectivity barriers from capacity competition."),
    "S06": ("ExpansionCandidateAudit", "Validate cost, eligibility and combination policies."),
    "S07": ("BudgetedImprovement", "Choose a budget-feasible expansion set and assign demand."),
    "S08": ("ServiceTimeTradeoff", "Report the secondary weighted walk-time objective."),
    "S09": ("DisruptionStressTest", "Compare fixed-plan stress and adaptive replanning."),
    "S10": ("ParameterSensitivity", "Run explicit parameter sweeps and report each point."),
    "S11": ("ScenarioContrast", "Compare scenarios with matched input and data versions."),
    "S12": ("EvidencePackaging", "Package metrics, versions, trace and verifier certificate."),
}
SKILL_GRAPH_VERSION = "module-b-skillgraph-0.1.0"
SKILL_VERSION = "1.0.0"

# The dependency chain is executable and each edge is checked before dispatch.
COMPOSITES = {
    "C01": ("AssessAndExplain", ["S01", "S02", "S03", "S04", "S05", "S12"]),
    "C02": ("OptimizeUnderBudget", ["S01", "S02", "S03", "S04", "S06", "S07", "S08", "S12"]),
    "C03": ("DiagnoseThenIntervene", ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S12"]),
    "C04": ("ResilienceWhatIf", ["S01", "S02", "S03", "S04", "S09", "S12"]),
    "C05": ("CompareAndRecommend", ["S01", "S02", "S03", "S04", "S11", "S12"]),
}
TASK_COMPOSITE = {
    "assess": "C01", "optimize": "C02", "diagnose_intervene": "C03",
    "stress_test": "C04", "compare": "C05", "sensitivity": "C01",
    "explain_result": "C01",
}
PREREQUISITES = {
    "S02": {"S01"}, "S03": {"S02"}, "S04": {"S03"}, "S05": {"S04"},
    "S06": {"S04"}, "S07": {"S04", "S06"}, "S08": {"S07"},
    "S09": {"S04"}, "S10": {"S04"}, "S11": {"S04"},
    "S12": {"S04"},
}
SKILL_METADATA = {
    "S01": ("planning input", "source status known", "mode permitted or structured block", ["DATA_UNAVAILABLE", "DATA_NOT_VERIFIED"]),
    "S02": ("confirmed scenario", "values pass schema", "immutable normalized scenario", ["SCENARIO_INVALID"]),
    "S03": ("OD rows and scenario", "source/data versions match", "sparse legal edge set", ["DATA_INVALID", "VERSION_MISMATCH"]),
    "S04": ("legal edges, demand and capacity", "scenario has at least one demand node", "optimal or feasible assignment plus deficits", ["SOLVER_TIMEOUT", "MODEL_INFEASIBLE"]),
    "S05": ("assignment deficits and OD reasons", "S04 completed", "evidence-linked bottleneck categories", ["INVALID_PLAN"]),
    "S06": ("explicit improvement options", "option source and combination policy supplied", "audited candidate set", ["CANDIDATE_INVALID"]),
    "S07": ("audited options and budget", "candidate count within MIP model limit", "selected options and assignment certificate", ["CANDIDATE_INVALID", "SOLVER_TIMEOUT"]),
    "S08": ("primary-optimal assignment", "S07 completed", "secondary walk-time metric", ["SOLVER_TIMEOUT"]),
    "S09": ("baseline plan and disruptions", "fixed/adaptive semantics explicit", "separate stress branches", ["SCENARIO_INVALID"]),
    "S10": ("allowlisted parameter and values", "1–20 values", "sensitivity points", ["SCENARIO_INVALID"]),
    "S11": ("same-version scenario changes", "all changes schema-valid", "side-by-side summaries", ["VERSION_MISMATCH"]),
    "S12": ("structured result and certificate", "hard verifier passed", "evidence package with hashes", ["CONSTRAINT_VIOLATION"]),
}


def capabilities() -> dict:
    prerequisites = {
        "S02": ["S01"], "S03": ["S02"], "S04": ["S03"], "S05": ["S04"],
        "S06": ["S04"], "S07": ["S04", "S06"], "S08": ["S07"],
        "S09": ["S04"], "S10": ["S04"], "S11": ["S04"], "S12": ["S04"],
    }
    return {
        "skill_graph_version": SKILL_GRAPH_VERSION,
        "skills": [{
            "skill_id": key, "name": value[0], "version": SKILL_VERSION,
            "purpose": value[1], "dependencies": prerequisites.get(key, []),
            "required_inputs": SKILL_METADATA[key][0],
            "preconditions": SKILL_METADATA[key][1],
            "postconditions": SKILL_METADATA[key][2],
            "capability_flags": ["deterministic", "registered", "traceable"],
            "execution_policy": "registered_python_implementation_only",
            "failure_policy": {"recoverable_errors": SKILL_METADATA[key][3], "default": "fail_closed; caller may retry only retryable errors"},
            "validation_contract": "Independent hard checks run before result publication.",
        } for key, value in SKILLS.items()],
        "composites": [{
            "composite_id": key, "name": value[0], "task_dag": value[1],
            "conditions": ([{"after": "S05", "include": ["S06", "S07"], "when": "eligible_options_present"}] if key == "C03" else []),
        } for key, value in COMPOSITES.items()],
        "task_types": sorted([*TASK_COMPOSITE, "disaster_recovery"]),
        "composite_workflows": [{
            "task_type": "disaster_recovery",
            "stages": ["baseline", "damage", "diagnose", "recovery", "compare", "verify"],
            "execution_policy": "registered_finite_workflow; result-driven branches; no caller DAG",
            # Public TaskRequest/C guards allow at most 12 options. The
            # underlying budget solver supports more, but this workflow must
            # advertise its actual API contract rather than the solver limit.
            "max_recovery_options": 12,
        }],
        "data_modes": ["demo", "verified"],
        "solver": {"assignment": "SciPy HiGHS LP", "budget": "SciPy HiGHS mixed-integer optimization", "max_options": MAX_BUDGET_OPTIONS, "time_limit_seconds": MAX_BUDGET_SOLVE_SECONDS, "objective_order": ["maximize_served_demand", "minimize_weighted_walk_time", "minimize_option_cost", "minimize_option_count"]},
    }
