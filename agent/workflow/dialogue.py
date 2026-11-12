"""Cross-turn clarification: one pending task that survives an incomplete turn.

The V2 runner only remembered the *changes* of a clarifying turn. That is not a
clarification loop: when the user answers "预算 2" the next turn, a fresh intent
is extracted and the original task has to be guessed again.

This module keeps the whole unfinished task - type, changes, options, and the
fields that are still missing - so the next turn can be *resumed* instead of
re-routed. It is deliberately deterministic:

* a bare value ("2", "预算2", "就用2吧") fills the single missing numeric slot;
* a reply that adds an unrelated condition is applied on top, not discarded;
* an explicit cancel, a task switch, or the TTL closes the pending task.

No model call is made to decide any of this, and no expected answer is ever
injected: the filler only reads what the user typed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any

from agent.contracts import ScenarioParams
from agent.intent.extractor import (
    BUDGET_RE,
    COMPARE_NUMBERS_RE,
    ID_RE,
    OPTION_COST_RE,
    OPTION_RE,
    RATIO_CHENG_RE,
    RATIO_PCT_RE,
    WALK_RE,
    _chinese_number,
    _rule_sensitivity,
)
from agent.intent.schema import ParamOp, ParsedIntent
from agent.workflow.router import RoutePlan

DEFAULT_TTL_S = 1800.0

CANCEL_RE = re.compile(r"^\s*(算了|取消|不做了|先不做了|不用了|放弃|停止|stop|cancel)\s*[。.!！]?\s*$", re.I)
CANCEL_ANY_RE = re.compile(r"取消(?:这次|刚才|这个)?(?:任务|请求|操作)|不做了|放弃这次")

TASK_TO_INTENT = {
    "assess": "assess",
    "optimize": "optimize",
    "compare": "compare",
    "stress_test": "stress_test",
    "sensitivity": "sensitivity",
    "explain_result": "explain",
    "diagnose_intervene": "diagnose",
}

TASK_WORDS = (
    ("sensitivity", re.compile(r"敏感性|档位")),
    ("compare", re.compile(r"比较|对比|差异")),
    ("stress_test", re.compile(r"压力测试|停用|关停|关闭")),
    ("optimize", re.compile(r"优化|扩容")),
    ("diagnose_intervene", re.compile(r"诊断|瓶颈")),
    ("explain_result", re.compile(r"解释|说明一下")),
    ("assess", re.compile(r"评估|现状|够不够|覆盖")),
)

BARE_NUMBER_RE = re.compile(r"^\s*[¥￥]?\s*(-?\d+(?:\.\d+)?)\s*(?:个?\s*(?:单位|人|分钟|%))?\s*[。.!！]?\s*$")
BUDGET_LOOSE_RE = re.compile(r"(?:预算|就用|用|改成|设为|给)\s*[¥￥]?\s*(-?\d+(?:\.\d+)?)")
RESULT_ID_RE = re.compile(r"\b(?:r|res|result)[-_][0-9a-zA-Z-]{4,}\b")
RATIO_VERB_RE = re.compile(r"比例|需求")


@dataclass
class PendingTask:
    task_type: str
    changes: dict[str, Any] = field(default_factory=dict)
    options: list[dict[str, Any]] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    question: str | None = None
    original_text: str = ""
    asked_at: float = 0.0
    expires_at: float = 0.0
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "changes": self.changes,
            "options": self.options,
            "parameters": self.parameters,
            "missing": list(self.missing),
            "question": self.question,
            "original_text": self.original_text,
            "asked_at": self.asked_at,
            "expires_at": self.expires_at,
            "attempts": self.attempts,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PendingTask":
        return cls(
            task_type=str(payload.get("task_type") or "assess"),
            changes=dict(payload.get("changes") or {}),
            options=list(payload.get("options") or []),
            parameters=dict(payload.get("parameters") or {}),
            missing=list(payload.get("missing") or []),
            question=payload.get("question"),
            original_text=str(payload.get("original_text") or ""),
            asked_at=float(payload.get("asked_at") or 0.0),
            expires_at=float(payload.get("expires_at") or 0.0),
            attempts=int(payload.get("attempts") or 1),
        )


@dataclass
class DialogueState:
    """Per-session clarification state. Serialisable so the API can persist it."""

    pending: PendingTask | None = None
    ttl_s: float = DEFAULT_TTL_S
    turns: int = 0
    last_closed: str | None = None  # cancelled | expired | resumed | switched

    # ------------------------------------------------------------------ #
    def open(self, plan: RoutePlan, *, params: ScenarioParams, text: str,
             now: float | None = None) -> PendingTask:
        now = time.time() if now is None else now
        missing = list(plan.clarify_fields or [])
        if self.pending and self.pending.task_type == plan.task_type:
            # Re-asking the same task: keep what was already collected.
            merged_changes = dict(self.pending.changes)
            merged_changes.update(plan.changes)
            merged_options = plan.options or self.pending.options
            attempts = self.pending.attempts + 1
        else:
            merged_changes = dict(plan.changes)
            merged_options = list(plan.options)
            attempts = 1
        pending = PendingTask(
            task_type=plan.task_type or "assess",
            changes=merged_changes,
            options=list(merged_options),
            parameters=dict(plan.parameters or {}),
            missing=missing,
            question=plan.question,
            original_text=text,
            asked_at=now,
            expires_at=now + self.ttl_s,
            attempts=attempts,
        )
        self.pending = pending
        return pending

    def close(self, reason: str) -> None:
        if self.pending is not None:
            self.last_closed = reason
        self.pending = None

    def expired(self, now: float | None = None) -> bool:
        if self.pending is None:
            return False
        now = time.time() if now is None else now
        return now > self.pending.expires_at

    def check(self, now: float | None = None) -> str:
        """Return the pending status without mutating it on the expiry path."""
        if self.pending is None:
            return "none"
        return "expired" if self.expired(now) else "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending": self.pending.to_dict() if self.pending else None,
            "ttl_s": self.ttl_s,
            "turns": self.turns,
            "last_closed": self.last_closed,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "DialogueState":
        payload = payload or {}
        pending = payload.get("pending")
        return cls(
            pending=PendingTask.from_dict(pending) if isinstance(pending, dict) else None,
            ttl_s=float(payload.get("ttl_s") or DEFAULT_TTL_S),
            turns=int(payload.get("turns") or 0),
            last_closed=payload.get("last_closed"),
        )


# --------------------------------------------------------------------------- #
# Deterministic slot filling
# --------------------------------------------------------------------------- #
@dataclass
class Filled:
    values: dict[str, Any] = field(default_factory=dict)
    remaining: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.remaining


def is_cancel(text: str) -> bool:
    text = (text or "").strip()
    return bool(CANCEL_RE.match(text) or CANCEL_ANY_RE.search(text))


def detect_task_switch(text: str, current: str) -> str | None:
    """Return a different task type when the user clearly names one."""
    for task_type, pattern in TASK_WORDS:
        if pattern.search(text) and task_type != current:
            # "停用" inside a clarification for stress is not a switch.
            if task_type == "stress_test" and current == "optimize" and "停用" in text:
                return task_type
            return task_type
    return None


def fill_pending(pending: PendingTask, text: str) -> Filled:
    """Fill missing slots from the user's reply alone.

    Only patterns that are unambiguous are used. Anything else stays missing and
    the agent asks again - it never guesses a number.
    """
    text = text or ""
    values: dict[str, Any] = {}
    remaining: list[str] = []

    for field_name in pending.missing:
        value = _FILLERS.get(field_name, lambda _text, _p: None)(text, pending)
        if value is None:
            # A reply may answer a slot that was not the one asked about, but it
            # still counts when the user states it explicitly.
            value = _secondary(field_name, text)
        if value is None:
            remaining.append(field_name)
        else:
            values[field_name] = value

    # Opportunistic extras: a resumed turn often carries one more condition.
    for field_name, value in _incidental(text, pending).items():
        values.setdefault(field_name, value)

    return Filled(values=values, remaining=remaining)


def _fill_budget(text: str, _pending: PendingTask) -> float | None:
    match = BUDGET_RE.search(text) or BUDGET_LOOSE_RE.search(text) or BARE_NUMBER_RE.match(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _fill_options(text: str, _pending: PendingTask) -> list[dict[str, Any]] | None:
    rows = []
    for index, match in enumerate(OPTION_RE.finditer(text), 1):
        cost = OPTION_COST_RE.search(text[match.end():match.end() + 24]) or OPTION_COST_RE.search(text)
        rows.append({
            "shelter_id": match.group(1),
            "delta_capacity_persons": float(match.group(2)),
            "cost_units": float(cost.group(1)) if cost else 1.0,
        })
    return rows or None


def _fill_disabled(text: str, _pending: PendingTask) -> list[str] | None:
    ids = ID_RE.findall(text)
    return list(dict.fromkeys(ids)) or None


def _fill_compare(text: str, _pending: PendingTask) -> dict[str, Any] | None:
    match = COMPARE_NUMBERS_RE.search(text)
    values: list[float] = []
    field_name = "population_demand_ratio"
    if match:
        left, left_unit, right, right_unit = match.groups()
        unit = left_unit or right_unit
        values = [float(left), float(right)]
        if unit == "%":
            values = [value / 100.0 for value in values]
        elif unit == "成":
            values = [value / 10.0 for value in values]
        elif not (RATIO_VERB_RE.search(text)):
            field_name = "budget_units"
    else:
        numbers = [float(value) for value in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", text)]
        if len(numbers) >= 2:
            values = numbers[:12]
    if len(values) < 2:
        return None
    return {"field": field_name, "values": values}


def _fill_sensitivity(text: str, _pending: PendingTask) -> dict[str, Any] | None:
    return _rule_sensitivity(text)


def _fill_result_id(text: str, _pending: PendingTask) -> str | None:
    match = RESULT_ID_RE.search(text)
    return match.group(0) if match else None


def _fill_task(text: str, _pending: PendingTask) -> str | None:
    for task_type, pattern in TASK_WORDS:
        if pattern.search(text):
            return task_type
    return None


_FILLERS = {
    "budget_units": _fill_budget,
    "improvement_options": _fill_options,
    "disabled_shelter_ids": _fill_disabled,
    "compare_values": _fill_compare,
    "sensitivity_spec": _fill_sensitivity,
    "result_id": _fill_result_id,
    "task": _fill_task,
}


def _secondary(field_name: str, text: str) -> Any:
    """A slot stated explicitly even though the agent asked about another one."""
    if field_name == "budget_units":
        match = re.search(r"预算\s*(?:设为|改成|为|=)?\s*(-?\d+(?:\.\d+)?)", text)
        return float(match.group(1)) if match else None
    return None


def _incidental(text: str, pending: PendingTask) -> dict[str, Any]:
    """Extra conditions the reply carries beyond the slot that was asked for.

    A user answering "what budget?" with "预算2，候选 zs_x 扩容300人成本1" has
    answered two questions at once. Dropping the second one would make the agent
    ask for something it was just told.
    """
    extras: dict[str, Any] = {}
    if "budget_units" not in pending.missing:
        match = BUDGET_RE.search(text) or re.search(
            r"预算\s*(?:设为|改成|调整为|为|是|=)\s*(-?\d+(?:\.\d+)?)", text)
        if match:
            extras["budget_units"] = float(match.group(1))
    if "population_demand_ratio" not in pending.missing:
        match = RATIO_PCT_RE.search(text)
        if match:
            extras["population_demand_ratio"] = float(match.group(1)) / 100.0
        else:
            cheng = RATIO_CHENG_RE.search(text)
            if cheng:
                extras["population_demand_ratio"] = _chinese_number(cheng.group(1)) / 10.0
    if "max_walk_time_min" not in pending.missing:
        match = WALK_RE.search(text)
        if match:
            extras["max_walk_time_min"] = float(match.group(1))
    if "improvement_options" not in pending.missing:
        options = _fill_options(text, pending)
        if options:
            extras["improvement_options"] = options
    if "disabled_shelter_ids" not in pending.missing and re.search(r"停用|关停|关闭|禁用", text):
        ids = ID_RE.findall(text)
        if ids:
            extras["disabled_shelter_ids"] = list(dict.fromkeys(ids))
    return extras


# --------------------------------------------------------------------------- #
# Resume: turn a filled pending task back into an executable plan
# --------------------------------------------------------------------------- #
def build_resume_ops(pending: PendingTask, values: dict[str, Any]) -> list[ParamOp]:
    """Translate the filled slots into parameter operations.

    Values the user gave *now* win over the values recorded when the task was
    opened; everything else is replayed as an explicit `set`, so the result is
    recomputed against the current snapshot rather than a stale one.
    """
    ops: list[ParamOp] = []
    for field_name in ("budget_units", "population_demand_ratio",
                       "max_walk_time_min", "disabled_shelter_ids"):
        if field_name in values:
            ops.append(ParamOp(field_name, "set", values[field_name]))

    filled_fields = {op.field for op in ops}
    for key, value in (pending.changes or {}).items():
        if key in filled_fields:
            continue
        if key == "disabled_shelter_ids":
            ops.append(ParamOp(key, "set", list(value or [])))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            ops.append(ParamOp(key, "set", float(value)))
    return ops


def resume_intent(pending: PendingTask, values: dict[str, Any]) -> ParsedIntent:
    name = TASK_TO_INTENT.get(pending.task_type, "assess")
    intent = ParsedIntent(name=name, ops=build_resume_ops(pending, values),
                          source="resume", confidence=1.0)
    if name == "compare":
        intent.compare = values.get("compare_values") or pending.parameters.get("compare_spec")
    if name == "sensitivity":
        intent.sensitivity = values.get("sensitivity_spec") or pending.parameters.get("sensitivity_spec")
    options = values.get("improvement_options") or pending.options
    if options:
        intent.options = list(options)
    intent.reason = f"resumed pending {pending.task_type} after {pending.attempts} clarification(s)"
    return intent
