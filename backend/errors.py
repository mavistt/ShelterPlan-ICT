"""Stable service error contract."""
from __future__ import annotations


class PlanningError(Exception):
    error_code = "PLANNING_ERROR"
    retryable = False
    required_user_action: str | None = None

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class DataUnavailableError(PlanningError):
    error_code = "DATA_UNAVAILABLE"
    required_user_action = "Provide the missing Module A processed outputs and manifest."


class DataValidationError(PlanningError):
    error_code = "DATA_INVALID"


class ScenarioError(PlanningError):
    error_code = "SCENARIO_INVALID"


class PlanRejectedError(PlanningError):
    error_code = "INVALID_SKILL_DAG"


class CandidateError(PlanningError):
    error_code = "CANDIDATE_INVALID"


class SolverTimeoutError(PlanningError):
    error_code = "SOLVER_TIMEOUT"
    retryable = True
