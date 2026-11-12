"""Intent envelope: the only shape the extractor is allowed to produce.

Design rules
------------
* The model emits a *flat* operation list. It never emits a full parameter
  snapshot: snapshots come from the session, never from the model.
* Every value is validated here, not trusted. Unknown intent -> error, unknown
  field -> dropped with a warning, unknown operator -> error.
* Aliases exist because the same idea has many spellings ("budget", "预算",
  "multiply", "减半"). Normalisation is deterministic and unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from agent.contracts import SCENARIO_CHANGE_KEYS
from agent.errors import HarnessError

INTENTS = (
    "assess",
    "optimize",
    "compare",
    "stress_test",
    "sensitivity",
    "diagnose",
    "explain",
    "modify_scenario",
    "clarify",
)

# Only task types that really exist in Module B. `modify_scenario` and
# `clarify` have no B counterpart on purpose.
TASK_BY_INTENT: dict[str, str | None] = {
    "assess": "assess",
    "optimize": "optimize",
    "compare": "compare",
    "stress_test": "stress_test",
    "sensitivity": "sensitivity",
    "diagnose": "diagnose_intervene",
    "explain": "explain_result",
    "modify_scenario": None,
    "clarify": None,
}

INTENT_ALIASES = {
    "assess": "assess", "assessment": "assess", "evaluate": "assess",
    "evaluation": "assess", "现状评估": "assess", "评估": "assess",
    "optimize": "optimize", "optimization": "optimize", "optimise": "optimize",
    "优化": "optimize", "扩容": "optimize",
    "compare": "compare", "comparison": "compare", "对比": "compare",
    "比较": "compare",
    "stress_test": "stress_test", "stress": "stress_test", "stress-test": "stress_test",
    "压力测试": "stress_test", "关停": "stress_test",
    "sensitivity": "sensitivity", "sensitivity_analysis": "sensitivity",
    "敏感性": "sensitivity", "敏感性分析": "sensitivity",
    "diagnose": "diagnose", "diagnosis": "diagnose", "diagnose_intervene": "diagnose",
    "诊断": "diagnose", "瓶颈": "diagnose",
    "explain": "explain", "explain_result": "explain", "explanation": "explain",
    "解释": "explain", "说明": "explain",
    "modify_scenario": "modify_scenario", "modify": "modify_scenario",
    "update": "modify_scenario", "param_change": "modify_scenario",
    "修改": "modify_scenario", "调整": "modify_scenario",
    "restore": "modify_scenario", "rollback": "modify_scenario", "undo": "modify_scenario",
    "revert": "modify_scenario", "恢复": "modify_scenario", "还原": "modify_scenario",
    "clarify": "clarify", "ask": "clarify", "unknown": "clarify",
    "none": "clarify", "澄清": "clarify",
}

FIELD_ALIASES = {
    "population_demand_ratio": "population_demand_ratio",
    "demand_ratio": "population_demand_ratio",
    "ratio": "population_demand_ratio",
    "population_ratio": "population_demand_ratio",
    "需求比例": "population_demand_ratio",
    "max_walk_time_min": "max_walk_time_min",
    "walk_time": "max_walk_time_min",
    "walk_time_min": "max_walk_time_min",
    "max_walk": "max_walk_time_min",
    "步行阈值": "max_walk_time_min",
    "步行时间": "max_walk_time_min",
    "budget_units": "budget_units",
    "budget": "budget_units",
    "预算": "budget_units",
    "disabled_shelter_ids": "disabled_shelter_ids",
    "disabled": "disabled_shelter_ids",
    "closed_shelter_ids": "disabled_shelter_ids",
    "停用场所": "disabled_shelter_ids",
    "关停场所": "disabled_shelter_ids",
}

OP_ALIASES = {
    "set": "set", "assign": "set", "replace": "set", "=": "set", "设为": "set",
    "delta": "delta", "add": "delta", "increase": "delta", "decrease": "delta",
    "increment": "delta", "增加": "delta", "减少": "delta",
    "scale": "scale", "multiply": "scale", "times": "scale", "percentage": "scale",
    "乘以": "scale", "减半": "scale", "翻倍": "scale",
    "restore": "restore", "reset": "restore", "undo": "restore", "revert": "restore",
    "恢复": "restore", "还原": "restore",
}

OPS = ("set", "delta", "scale", "restore")
LIST_FIELDS = ("disabled_shelter_ids",)


class IntentParseError(HarnessError):
    code = "INTENT_PARSE_ERROR"


@dataclass(frozen=True)
class ParamOp:
    field: str
    op: str
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}


@dataclass
class ParsedIntent:
    name: str
    ops: list[ParamOp] = field(default_factory=list)
    compare: dict[str, Any] | None = None
    sensitivity: dict[str, Any] | None = None
    options: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    clarify: list[str] = field(default_factory=list)
    reason: str = ""
    source: str = "llm"
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.name,
            "ops": [op.to_dict() for op in self.ops],
            "compare": self.compare,
            "sensitivity": self.sensitivity,
            "options": self.options,
            "confidence": self.confidence,
            "clarify": self.clarify,
            "source": self.source,
            "warnings": self.warnings,
        }


# The prompt fragment mirrors the parser exactly; keep them in one file so a
# schema change cannot drift away from its documentation.
INTENT_SCHEMA_HINT = """{
  "intent": "assess|optimize|compare|stress_test|sensitivity|diagnose|explain|modify_scenario|clarify",
  "ops": [
    {"field": "population_demand_ratio|max_walk_time_min|budget_units|disabled_shelter_ids",
     "op": "set|delta|scale|restore",
     "value": "数字 / 场所ID数组 / restore 目标"}
  ],
  "compare": {"field": "参数名", "values": [数字, 数字]} 或 null,
  "sensitivity": {"parameter": "参数名", "values": [数字, ...]} 或 null,
  "options": [{"shelter_id": "zs_x", "delta_capacity_persons": 300, "cost_units": 1}],
  "confidence": 0.0,
  "clarify": ["缺少的字段名"]
}"""


def canonical_intent(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise IntentParseError("Intent is missing.", details={"raw": raw})
    key = raw.strip().lower().replace(" ", "_")
    if key in INTENT_ALIASES:
        return INTENT_ALIASES[key]
    if raw.strip() in INTENT_ALIASES:
        return INTENT_ALIASES[raw.strip()]
    raise IntentParseError("Unknown intent value.", details={"raw": raw, "allowed": list(INTENTS)})


def canonical_field(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    key = raw.strip()
    if key in FIELD_ALIASES:
        return FIELD_ALIASES[key]
    lowered = key.lower()
    return FIELD_ALIASES.get(lowered)


def canonical_op(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    key = raw.strip()
    if key in OP_ALIASES:
        return OP_ALIASES[key]
    return OP_ALIASES.get(key.lower())


def parse_intent_payload(payload: dict[str, Any], *, source: str = "llm") -> ParsedIntent:
    """Validate and canonicalise a decoded JSON object.

    Raises IntentParseError only for problems that make the object unusable
    (no intent, unknown intent, ops that are not a list). Everything else is
    dropped or coerced and recorded in `warnings`, because a partially correct
    extraction is still better than a failed turn.
    """
    if not isinstance(payload, dict):
        raise IntentParseError("Intent payload must be a JSON object.",
                               details={"type": type(payload).__name__})

    name = canonical_intent(payload.get("intent") or payload.get("task") or payload.get("action"))
    result = ParsedIntent(name=name, source=source, raw=payload)

    raw_ops = payload.get("ops") or payload.get("params") or []
    if raw_ops and not isinstance(raw_ops, list):
        raise IntentParseError("`ops` must be a list.", details={"type": type(raw_ops).__name__})
    for row in raw_ops:
        if not isinstance(row, dict):
            result.warnings.append("dropped a non-object op")
            continue
        field_name = canonical_field(row.get("field") or row.get("name"))
        if field_name is None:
            result.warnings.append(f"dropped unknown field: {row.get('field')!r}")
            continue
        operator = canonical_op(row.get("op") or row.get("operation") or "set")
        if operator is None:
            # One unparseable operator must not destroy an otherwise usable
            # envelope: drop it and let the router ask about the field later.
            result.warnings.append(f"dropped unknown op: {row.get('op')!r}")
            continue
        value = _coerce_op_value(field_name, operator, row.get("value", row.get("delta")))
        result.ops.append(ParamOp(field=field_name, op=operator, value=value))

    result.compare = _parse_compare(payload.get("compare"))
    result.sensitivity = _parse_sensitivity(payload.get("sensitivity"))
    result.options = _parse_options(payload.get("options") or payload.get("improvement_options"))
    result.confidence = _clamp_confidence(payload.get("confidence"))
    result.clarify = _parse_clarify(payload.get("clarify"))
    reason = payload.get("reason")
    result.reason = str(reason)[:200] if isinstance(reason, str) else ""
    return result


def _coerce_op_value(field_name: str, operator: str, value: Any) -> Any:
    if operator == "restore":
        if value in (None, "", "previous", "prev", "上一轮", "上一步"):
            return "previous"
        # A historical *reference* ("按上一轮的预算") - distinct from an undo,
        # which rewinds to `previous`. See normalizer._resolve_restore.
        if value in ("last", "latest", "上一轮的值", "刚才的值"):
            return "last"
        if value in ("default", "默认"):
            return "default"
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.startswith(("c-", "t")):
            return value
        return "previous"
    if field_name in LIST_FIELDS:
        if isinstance(value, str):
            return [part.strip() for part in re.split(r"[,，、\s]+", value) if part.strip()]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        raise IntentParseError("disabled_shelter_ids requires a list of shelter ids.",
                               details={"value": value})
    number = _as_number(value)
    if number is None:
        raise IntentParseError("Parameter operation requires a numeric value.",
                               details={"field": field_name, "op": operator, "value": value})
    return number


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").rstrip("%")
        try:
            number = float(cleaned)
        except ValueError:
            return None
        return number / 100.0 if value.strip().endswith("%") else number
    return None


def _parse_compare(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    field_name = canonical_field(raw.get("field"))
    values = raw.get("values")
    if field_name is None or not isinstance(values, list):
        return None
    numbers = [_as_number(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return {"field": field_name, "values": numbers} if numbers else None


def _parse_sensitivity(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    parameter = canonical_field(raw.get("parameter") or raw.get("field"))
    values = raw.get("values")
    if parameter is None or not isinstance(values, list):
        return None
    numbers = [_as_number(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return {"parameter": parameter, "values": numbers} if numbers else None


def _parse_options(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [dict(row) for row in raw if isinstance(row, dict)]


def _clamp_confidence(raw: Any) -> float:
    number = _as_number(raw)
    if number is None:
        return 0.0
    return round(max(0.0, min(1.0, number)), 3)


def _parse_clarify(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    fields = []
    for item in raw:
        field_name = canonical_field(item)
        if field_name and field_name not in fields:
            fields.append(field_name)
    return fields


# --------------------------------------------------------------------------- #
# Deterministic JSON repair. A 5B model wraps JSON in prose or fenced blocks
# regularly; re-asking costs a full generation, repairing costs microseconds.
# --------------------------------------------------------------------------- #
FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def repair_json(text: str) -> dict[str, Any] | None:
    """Best-effort deterministic decode. Returns None when nothing is salvageable."""
    if not isinstance(text, str) or not text.strip():
        return None
    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)
    fence = FENCE_RE.search(stripped)
    if fence:
        candidates.append(fence.group(1).strip())
    block = _first_balanced_object(stripped)
    if block:
        candidates.append(block)

    for candidate in candidates:
        for transform in (lambda value: value,
                          _normalise_quotes,
                          lambda value: _normalise_quotes(_drop_trailing_commas(value))):
            try:
                parsed = json.loads(transform(candidate))
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _normalise_quotes(text: str) -> str:
    return (text.replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2018", "'").replace("\u2019", "'"))


def _drop_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)
