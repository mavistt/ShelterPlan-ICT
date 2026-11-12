"""Stable error taxonomy for the C harness.

Every failure that can reach a caller is classified here so traces, tests and
future AgentArts nodes can branch on a code instead of parsing prose.
"""
from __future__ import annotations

from typing import Any


class HarnessError(Exception):
    """Base class. `code` is stable and is what tests assert on."""

    code = "HARNESS_ERROR"
    retryable = False
    user_action: str | None = None

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
            "user_action": self.user_action,
        }


# --------------------------------------------------------------------------- #
# Guard (pre-flight / post-flight) failures — the harness refuses to call B.
# --------------------------------------------------------------------------- #
class GuardError(HarnessError):
    code = "GUARD_ERROR"


class GuardMissingParam(GuardError):
    code = "GUARD_MISSING_PARAM"
    user_action = "Ask the user for the missing value; never infer a real budget or ratio."


class ToolArgumentError(HarnessError):
    """A required tool argument was missing or unusable. Correctable by the
    caller without asking the user anything."""

    code = "TOOL_ARGUMENT_ERROR"
    user_action = "Provide the missing tool argument and retry the same call."


class GuardInvalidParam(GuardError):
    code = "GUARD_INVALID_PARAM"
    user_action = "Correct the parameter and resubmit as a new logical task."


class GuardVersionMismatch(GuardError):
    code = "GUARD_VERSION_MISMATCH"
    user_action = "Use the data_version of the currently loaded source."


class GuardNotVerifiedSource(GuardError):
    code = "GUARD_NOT_VERIFIED_SOURCE"
    user_action = "Switch to demo mode; verified planning needs an accepted Module A release."


class GuardUnknownResult(GuardError):
    code = "GUARD_UNKNOWN_RESULT"
    user_action = "Reference a result_id produced by this session."


class GuardInvalidOption(GuardError):
    code = "GUARD_INVALID_OPTION"
    user_action = "Fix the improvement option table and resubmit."


class GuardClaimViolation(GuardError):
    code = "GUARD_CLAIM_VIOLATION"
    user_action = "Restate the answer using only numbers returned by the planning service."


# --------------------------------------------------------------------------- #
# Transport / B service failures.
# --------------------------------------------------------------------------- #
class BTransportError(HarnessError):
    code = "TRANSPORT_ERROR"
    retryable = True


class BHttpError(HarnessError):
    code = "HTTP_ERROR"


class BJobFailed(HarnessError):
    code = "JOB_FAILED"


class BJobStateLost(HarnessError):
    code = "JOB_STATE_LOST"
    retryable = True
    user_action = "The Module B process restarted and dropped its in-memory job table; resubmit as a new task."


class BJobTimeout(HarnessError):
    code = "JOB_TIMEOUT"
    retryable = True
    user_action = "Retry with the same idempotency key, or narrow the request."


class BJobMissing(GuardError):  # 404 on a job id
    code = "JOB_NOT_FOUND"


class BResultMissing(HarnessError):
    code = "RESULT_NOT_FOUND"


class ResultMismatch(HarnessError):
    code = "RESULT_MISMATCH"
    user_action = "Do not use this result; the identifier does not match the requested scenario."


# Mapping from Module B error codes to C codes. B stays the source of truth; C
# only adds a stable prefix so traces show which layer refused the request.
B_ERROR_MAP = {
    "DATA_UNAVAILABLE": "B_DATA_UNAVAILABLE",
    "DATA_INVALID": "B_DATA_INVALID",
    "SCENARIO_INVALID": "B_SCENARIO_INVALID",
    "INVALID_SKILL_DAG": "B_INVALID_SKILL_DAG",
    "CANDIDATE_INVALID": "B_CANDIDATE_INVALID",
    "SOLVER_TIMEOUT": "B_SOLVER_TIMEOUT",
    "INTERNAL_ERROR": "B_INTERNAL_ERROR",
}


class BError(HarnessError):
    """A structured error returned by Module B (HTTP body or job.error)."""

    def __init__(self, b_code: str, message: str, *, details: dict[str, Any] | None = None,
                 retryable: bool = False, user_action: str | None = None,
                 http_status: int | None = None) -> None:
        super().__init__(message, details=details)
        self.b_code = b_code
        self.code = B_ERROR_MAP.get(b_code, f"B_{b_code}")
        self.retryable = retryable
        self.user_action = user_action
        self.http_status = http_status
