"""Apply structured operations to the authoritative session snapshot.

The model never sees or produces a full snapshot. It produces operations; this
module resolves them against `Session.current`, so an unmentioned parameter is
mathematically incapable of changing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from agent.contracts import SCENARIO_CHANGE_KEYS, ScenarioParams, ScenarioState
from agent.errors import GuardInvalidParam, GuardMissingParam, GuardUnclearOperation
from agent.harness import guards
from agent.intent import quantities
from agent.intent.schema import ParamOp

LIST_FIELDS = ("disabled_shelter_ids",)
RATIO_FIELDS = ("population_demand_ratio",)

# Whether the sentence mentions a field at all. A model sometimes "helpfully"
# restates the current defaults as `set` ops for fields the user never named.
# Those are hallucinations, not unfounded user requests: dropping the single op
# (with a warning) keeps the rest of the turn executable, while a field the
# user DID name but did not quantify must still become a clarification.
FIELD_MENTIONS = {
    "budget_units": re.compile(r"预算|budget", re.I),
    "population_demand_ratio": re.compile(r"需求|比例|百分点|%|[一二三四五六七八九十\d]成"),
    "max_walk_time_min": re.compile(r"步行|阈值|分钟|小时|walk", re.I),
    "disabled_shelter_ids": re.compile(r"停用|关停|关闭|禁用|zs_", re.I),
}


@dataclass
class NormalizedTurn:
    params: ScenarioParams
    changes: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    restored_from: str | None = None

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


def apply_ops(current: ScenarioParams, ops: Iterable[ParamOp], *,
              history: Iterable[ScenarioState] | None = None,
              defaults: ScenarioParams | None = None,
              text: str | None = None) -> NormalizedTurn:
    """Resolve ops into an explicit change set relative to `current`.

    When `text` is supplied, every operation must be **supported by the sentence**
    (see `quantities.supports`). This is the "no unfounded arithmetic" rule: a
    `scale` with no multiplier in the text, or a `set` whose number the user never
    said, raises `GuardUnclearOperation` so the caller can ask instead of
    submitting an invented number to Module B. If the text states a different but
    equally unambiguous value, the text wins and the override is recorded.
    """
    ops = list(ops or [])
    if not ops:
        return NormalizedTurn(params=current, changes={})

    working = current.to_dict()
    warnings: list[str] = []
    restored_from: str | None = None

    if text is not None:
        ops = _ground_ops_in_text(text, ops, warnings)

    for op in ops:
        if op.field not in SCENARIO_CHANGE_KEYS:
            raise GuardInvalidParam(f"Unknown scenario field: {op.field}",
                                    details={"field": op.field})
        if op.op == "restore":
            target, label = _resolve_restore(op.value, current, history or [], defaults)
            restored_from = label
            # Restore is field-scoped: "restore the budget" must not silently
            # rewind the walk threshold or the disabled-shelter list that the
            # same turn just changed.
            if op.field in target:
                working[op.field] = target[op.field]
            continue
        if op.op not in {"set", "delta", "scale"}:
            raise GuardInvalidParam(f"Unsupported operator: {op.op}",
                                    details={"op": op.op, "allowed": ["set", "delta", "scale", "restore"]})
        working[op.field] = _apply_single(op, working.get(op.field), warnings)

    changes: dict[str, Any] = {}
    for key in SCENARIO_CHANGE_KEYS:
        value = working.get(key)
        if key in LIST_FIELDS:
            value = list(value or [])
            if list(current.disabled_shelter_ids) == value:
                continue
        elif _same_number(value, getattr(current, key)):
            continue
        changes[key] = value

    # Range/type validation is Module C's existing guard, reused - not a second
    # copy of the business rules.
    changes = guards.validate_changes(changes)
    params = current.merged(changes) if changes else current
    return NormalizedTurn(params=params, changes=changes, warnings=warnings,
                          restored_from=restored_from)


def _ground_ops_in_text(text: str, ops: list[ParamOp], warnings: list[str]) -> list[ParamOp]:
    """Reject (or correct) operations the sentence does not justify."""
    grounded: list[ParamOp] = []
    for op in ops:
        ok, reason, override = quantities.supports(text, op.field, op.op, op.value)
        if not ok:
            mentioned = FIELD_MENTIONS.get(op.field)
            hallucinated = (op.op in {"set", "scale"} and mentioned is not None
                            and not mentioned.search(text))
            if hallucinated:
                # The user never named this field, so this op cannot be their
                # instruction. Record it in the trace and keep the other ops;
                # an all-or-nothing refusal would let a model restatement cancel
                # an otherwise complete request.
                warnings.append(f"dropped unfounded {op.field} {op.op} "
                                f"{op.value!r} (field not mentioned in the sentence)")
                continue
            raise GuardUnclearOperation(
                f"无法从原句推出 {op.field} 的 {op.op} 操作：{reason}",
                details={"field": op.field, "op": op.op, "value": op.value,
                         "reason": reason, "text": text})
        if override is not None:
            warnings.append(f"{op.field}:{op.op} {op.value!r} -> {override!r} ({reason})")
            grounded.append(ParamOp(op.field, op.op, override))
            continue
        grounded.append(op)
    return grounded


def _apply_single(op: ParamOp, current_value: Any, warnings: list[str]) -> Any:
    if op.field in LIST_FIELDS:
        if op.op != "set":
            raise GuardInvalidParam(f"{op.field} only supports `set` or `restore`.",
                                    details={"field": op.field, "op": op.op})
        return list(op.value or [])

    value = float(op.value)
    if op.op == "set":
        return value

    if op.op == "delta" and op.field in RATIO_FIELDS and abs(value) > 1.0:
        # "再加 5 个百分点" arrives as 5, not 0.05. Recording the conversion is
        # safer than silently planning a 500% demand increase.
        warnings.append(f"interpreted delta {value:g} on {op.field} as percentage points")
        value = value / 100.0

    if current_value is None:
        raise GuardMissingParam(
            f"{op.field} has no current value to modify; ask the user for a baseline.",
            details={"field": op.field, "op": op.op},
        )
    base = float(current_value)
    if op.op == "delta":
        return base + value
    if op.op == "scale":
        return base * value
    raise GuardInvalidParam(f"Unsupported operator: {op.op}", details={"op": op.op})


def _resolve_restore(value: Any, current: ScenarioParams,
                     history: list[ScenarioState],
                     defaults: ScenarioParams | None) -> tuple[dict[str, Any], str]:
    if value == "default":
        if defaults is None:
            raise GuardMissingParam("No default snapshot is available to restore.",
                                    details={"field": "restore"})
        return defaults.to_dict(), "default"
    if value == "last":
        # "按上一轮的预算再评估" is a *reference* to the most recent committed
        # snapshot, not an undo. It must read history[-1], whereas "撤销上一步"
        # (`previous`) rewinds one step further to history[-2].
        if history:
            target = history[-1]
            return target.params.to_dict(), f"turn:{target.turn}"
        if defaults is not None:
            return defaults.to_dict(), "default"
        return current.to_dict(), "current"
    if value == "previous" or value is None:
        if len(history) >= 2:
            target = history[-2]
            return target.params.to_dict(), f"turn:{target.turn}"
        if defaults is not None:
            return defaults.to_dict(), "default"
        return current.to_dict(), "current"
    if isinstance(value, int):
        for state in history:
            if state.turn == value:
                return state.params.to_dict(), f"turn:{value}"
        raise GuardInvalidParam(f"No scenario at turn {value}.", details={"turn": value})
    if isinstance(value, str):
        for state in history:
            if state.scenario_id == value:
                return state.params.to_dict(), f"scenario:{value}"
    raise GuardInvalidParam("Cannot resolve the restore target.",
                            details={"value": value})


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    try:
        return abs(float(left) - float(right)) <= 1e-12
    except (TypeError, ValueError):
        return left == right


# --------------------------------------------------------------------------- #
# Comparison / sensitivity plan builders. Both are pure parameter maths; they
# never call Module B and never invent a task type.
# --------------------------------------------------------------------------- #
def build_compare_rows(*, params: ScenarioParams,
                       changes: dict[str, Any],
                       spec: dict[str, Any] | None,
                       max_rows: int = 12) -> list[dict[str, Any]]:
    """Rows are per-scenario overrides applied on top of the base scenario."""
    if spec and spec.get("field") in SCENARIO_CHANGE_KEYS:
        field_name = spec["field"]
        values = list(spec.get("values") or [])[:max_rows]
        if len(values) >= 2:
            return [{field_name: value} for value in values]
    if changes:
        # No explicit axe given: compare the snapshot before and after this
        # turn's modification. This is the only defensible pair we can build
        # without inventing numbers.
        base = params.to_dict()
        after = dict(base)
        after.update(changes)
        rows = []
        for candidate in (base, after):
            rows.append({key: candidate[key] for key in changes})
        if rows[0] != rows[1]:
            return rows
    return []


def build_sensitivity(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not spec:
        return None
    parameter = spec.get("parameter")
    values = [value for value in (spec.get("values") or []) if isinstance(value, (int, float))]
    if parameter not in ("budget_units", "population_demand_ratio", "max_walk_time_min"):
        return None
    if not 1 <= len(values) <= 20:
        return None
    return {"parameter": parameter, "values": values}


# --------------------------------------------------------------------------- #
# Improvement options: the model emits a *compact* row (shelter, capacity,
# cost). C expands it into Module B's full contract with fixed, safe defaults.
# This keeps the schema small enough for a 5B model and stops it from inventing
# eligibility flags.
# --------------------------------------------------------------------------- #
OPTION_DEFAULTS = {
    "action": "hypothetical_capacity_increase",
    "source_status": "hypothetical_assumption",
    "hypothetical": True,
    "human_confirmed": False,
    "eligible": True,
    "combination_policy": "standalone",
}


def expand_options(raw: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(raw or [], 1):
        if not isinstance(row, dict):
            continue
        shelter_id = str(row.get("shelter_id") or row.get("shelter") or "").strip()
        capacity = row.get("delta_capacity_persons", row.get("capacity"))
        cost = row.get("cost_units", row.get("cost", 1))
        try:
            capacity_value = float(capacity)
            cost_value = float(cost if cost is not None else 1)
        except (TypeError, ValueError):
            continue
        # Only candidates that cannot mean anything are dropped here (missing id,
        # zero capacity, unparseable number) - the router then asks for a usable
        # table. A *negative* cost or capacity is kept so the guard can reject it
        # by name: silently deleting it turned "cost -1" into a free expansion,
        # which is the V2.2 P0 defect.
        if not shelter_id or capacity_value == 0:
            continue
        option = dict(OPTION_DEFAULTS)
        option.update({
            "option_id": str(row.get("option_id") or f"CAP_{index}_{shelter_id[-6:].upper()}"),
            "shelter_id": shelter_id,
            "delta_capacity_persons": capacity_value,
            "cost_units": cost_value,
        })
        if row.get("combination_policy") in {"standalone", "stackable", "exclusive_group"}:
            option["combination_policy"] = row["combination_policy"]
        if option["combination_policy"] == "exclusive_group":
            option["exclusive_group"] = row.get("exclusive_group")
        rows.append(option)
    return rows[:12]
