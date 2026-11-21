"""Deterministic reading of quantity phrasing.

Why this module exists
----------------------
Module B accepts numbers, not prose. Something has to turn "预算翻倍" or "步行半小时"
into an arithmetic operation, and that something must be **decidable from the text
alone** - otherwise the only options are "let the model guess" or "refuse everything".

V2.1 measured the cost of letting the model guess: "预算翻倍" became `scale: 6`
(budget 3 -> 18) and "步行阈值半小时" became `scale: 7.5` (15 -> 112.5 minutes,
which made Module B time out). Both are unambiguous in Chinese; neither needs a
language model.

Design rules
------------
* Only emit an operation when the **field and the quantity are in the same clause**.
  "预算 3，步行阈值半小时" must not attach the half-hour to the budget.
* Only emit phrasing that has a single arithmetic reading. "大概多一点" emits
  nothing, so the caller asks instead of inventing a number.
* Everything emitted carries `evidence` - the exact substring that justified it.
  The guard uses it to reject a model operation that the text does not support.
* This module never touches Module B and never decides a task type.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

# Field anchors. Order matters: the longer/more specific pattern must win when
# two anchors overlap ("需求比例" before "比例").
FIELD_ANCHORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("budget_units", re.compile(r"预算|经费|投入上限|budget", re.I)),
    ("population_demand_ratio", re.compile(r"需求比例|需求占比|人口比例|比例|需求", re.I)),
    ("max_walk_time_min", re.compile(r"步行|步距|阈值|walk", re.I)),
)

# "再" is NOT a clause boundary: "步行阈值再放宽半小时" is one clause and must
# keep its field anchor. Only explicit connectives split a sentence here.
CLAUSE_SPLIT_RE = re.compile(r"[，,。；;\n]|(?:然后|同时|并且)")

# --- multipliers ----------------------------------------------------------- #
DOUBLE_RE = re.compile(r"翻倍|翻一番|翻一翻|加倍|双倍|两倍|二倍")
HALF_RE = re.compile(r"减半|砍半|打对折|减为一半|变成一半|一半")
N_TIMES_RE = re.compile(r"([一二两三四五六七八九十\d]+(?:\.\d+)?)\s*倍")
UP_PCT_RE = re.compile(r"(?:提高|上调|增加|上涨|增长|涨|加)\s*(\d+(?:\.\d+)?)\s*(?:%|个?百分点)")
DOWN_PCT_RE = re.compile(r"(?:降低|下调|减少|下降|降|减)\s*(\d+(?:\.\d+)?)\s*(?:%|个?百分点)")
CHANGE_PCT_RE = re.compile(r"(?:变化|波动|调整)\s*(\d+(?:\.\d+)?)\s*(?:%|个百分点)")

# --- durations ------------------------------------------------------------- #
HALF_HOUR_RE = re.compile(r"半\s*个?\s*小时|半小时")
ONE_HALF_HOUR_RE = re.compile(r"一个半小时|一小时半")
QUARTER_HOUR_RE = re.compile(r"一刻钟")
HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*个?\s*小时")
MINUTES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*分钟")

# --- ratios ---------------------------------------------------------------- #
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
CHENG_RE = re.compile(r"([一二三四五六七八九十\d]+)\s*成")
# "再加 5 个百分点" is an absolute increment on a ratio (+0.05), NOT a
# multiplier (x1.05). The word 百分点 decides it; a bare "提高 5%" is a scale.
POINT_DELTA_DOWN_RE = re.compile(
    r"(?:减少|减|降低|下调|少)\s*(\d+(?:\.\d+)?)\s*个?百分点")
POINT_DELTA_UP_RE = re.compile(
    r"(?:增加|加|提高|上调|多|再)\s*(\d+(?:\.\d+)?)\s*个?百分点")

# --- delta / restore ------------------------------------------------------- #
DELTA_VERB_RE = re.compile(
    r"增加|加|减少|减|提高|降低|上调|下调|多|少|放宽|延长|缩短|收紧|调高|调低")
RESTORE_VERB_RE = re.compile(r"撤销|作废|恢复|还原|回滚|回退|回到|退回|不算|取消(?:刚才|上次|上一)")
UNDO_LAST_RE = re.compile(r"(?:撤销|作废|回退|回滚|恢复|回到)\s*(?:上一步|上一轮|上一次|刚才|上次)")
UNDO_ONE_RE = re.compile(r"撤(?:销|回)\s*(?:一下|这个|这次)")

# "按上一轮的预算" / "用刚才那个比例" is a *reference to a historical value*,
# not a request to invent one. It resolves to a field-scoped `restore previous`.
HISTORY_REF_RE = re.compile(
    r"(?:按|用|沿用|照着|照|根据|依照)\s*"
    r"(?:上一轮|上一次|上次|刚才|之前|上一步|刚才那次|上一)(?:的|那个)?\s*"
    r"(?P<field>预算|需求比例|人口比例|比例|需求|步行阈值|步行|阈值|停用场所|停用列表)?")
HISTORY_FIELD_BY_WORD = {
    "预算": "budget_units",
    "需求比例": "population_demand_ratio", "人口比例": "population_demand_ratio",
    "比例": "population_demand_ratio", "需求": "population_demand_ratio",
    "步行阈值": "max_walk_time_min", "步行": "max_walk_time_min", "阈值": "max_walk_time_min",
    "停用场所": "disabled_shelter_ids", "停用列表": "disabled_shelter_ids",
}

# An incremental duration ("减少 3 分钟", "再放宽半小时") is a delta, not a
# target value. "改成/到/为" inside the head means the number IS the target.
DURATION_DOWN_RE = re.compile(r"减少|减|缩短|降低|调低|少|收紧")
DURATION_UP_RE = re.compile(r"增加|加|延长|放宽|提高|调高|多|再给")
DURATION_ABSOLUTE_RE = re.compile(r"改成|改为|设为|设定为|调整为|调成|定为|等于|到|至|为|是")

CHINESE_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass(frozen=True)
class Quantity:
    """One arithmetic operation that the text itself justifies."""

    field: str
    op: str            # set | delta | scale | restore
    value: float | str | list[str]
    evidence: str      # exact substring from the user's sentence
    rule: str          # stable id, used in traces and tests

    def as_op(self):
        from agent.intent.schema import ParamOp
        return ParamOp(self.field, self.op, self.value)


def clauses(text: str) -> list[str]:
    return [part.strip() for part in CLAUSE_SPLIT_RE.split(text or "") if part and part.strip()]


def _field_of(clause: str) -> str | None:
    """The field this clause talks about, or None when it is ambiguous."""
    hits: list[tuple[int, str]] = []
    for field, pattern in FIELD_ANCHORS:
        match = pattern.search(clause)
        if match:
            hits.append((match.start(), field))
    if not hits:
        return None
    # Earliest anchor wins; ties are impossible because anchors are distinct
    # patterns, but "需求比例" consumes the same span as "比例" - prefer the
    # longer match by field order, which FIELD_ANCHORS already encodes.
    hits.sort(key=lambda pair: (pair[0], pair[1]))
    return hits[0][1]


def _chinese_number(token: str) -> float | None:
    token = token.strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        pass
    if token in CHINESE_DIGITS:
        return float(CHINESE_DIGITS[token])
    return None


def multiplier(clause: str) -> tuple[float, str, str] | None:
    """(factor, evidence, rule) when the clause states a multiplicative change."""
    match = DOUBLE_RE.search(clause)
    if match:
        return 2.0, match.group(0), "phrase.double"
    match = HALF_RE.search(clause)
    if match:
        return 0.5, match.group(0), "phrase.half"
    match = N_TIMES_RE.search(clause)
    if match:
        count = _chinese_number(match.group(1))
        if count is not None and count > 0:
            # "一倍" means double in Chinese usage; "0.5 倍" is a halving.
            factor = 2.0 if count == 1 else count
            return factor, match.group(0), "phrase.times"
    match = UP_PCT_RE.search(clause)
    if match:
        return 1.0 + float(match.group(1)) / 100.0, match.group(0), "phrase.up_pct"
    match = DOWN_PCT_RE.search(clause)
    if match:
        return 1.0 - float(match.group(1)) / 100.0, match.group(0), "phrase.down_pct"
    match = CHANGE_PCT_RE.search(clause)
    if match:
        return 1.0 + float(match.group(1)) / 100.0, match.group(0), "phrase.change_pct"
    return None


def duration_minutes(clause: str) -> tuple[float, str, str] | None:
    if ONE_HALF_HOUR_RE.search(clause):
        match = ONE_HALF_HOUR_RE.search(clause)
        return 90.0, match.group(0), "duration.one_and_half_hour"
    match = HALF_HOUR_RE.search(clause)
    if match:
        return 30.0, match.group(0), "duration.half_hour"
    match = QUARTER_HOUR_RE.search(clause)
    if match:
        return 15.0, match.group(0), "duration.quarter_hour"
    match = HOURS_RE.search(clause)
    if match:
        return float(match.group(1)) * 60.0, match.group(0), "duration.hours"
    match = MINUTES_RE.search(clause)
    if match:
        return float(match.group(1)), match.group(0), "duration.minutes"
    return None


def duration_delta(clause: str) -> tuple[float, str, str] | None:
    """(signed_minutes, evidence, rule) for an incremental duration phrase.

    "步行阈值减少 3 分钟" -> (-3, ..., duration.delta_down): the 3 is an
    increment on the current 15 minutes, not a new target of 3 minutes.
    "改成 / 到 / 为" in the head makes the number an absolute target instead,
    in which case this function stays silent and `derive_ops` emits `set`.
    """
    duration = duration_minutes(clause)
    if duration is None:
        return None
    minutes, evidence, _rule = duration
    head = clause[:clause.find(evidence)] if evidence in clause else clause
    if DURATION_ABSOLUTE_RE.search(head):
        return None
    if DURATION_DOWN_RE.search(head):
        return -minutes, clause.strip(), "duration.delta_down"
    if DURATION_UP_RE.search(head):
        return minutes, clause.strip(), "duration.delta_up"
    return None


def history_reference(text: str) -> tuple[str, str] | None:
    """(field, evidence) when the sentence references a previous turn's value."""
    for match in HISTORY_REF_RE.finditer(text or ""):
        field = HISTORY_FIELD_BY_WORD.get(match.group("field") or "")
        if field:
            return field, match.group(0)
    return None


def ratio_point_delta(clause: str) -> tuple[float, str, str] | None:
    """(percentage points, evidence, rule) for an absolute ratio increment.

    The value is kept in *points* (5, not 0.05) because `normalizer._apply_single`
    converts a ratio delta above 1 into /100 - that keeps the model's envelope,
    the rule parser and the guard speaking the same units.
    """
    match = POINT_DELTA_DOWN_RE.search(clause)
    if match:
        return -float(match.group(1)), match.group(0), "ratio.point_delta_down"
    match = POINT_DELTA_UP_RE.search(clause)
    if match:
        return float(match.group(1)), match.group(0), "ratio.point_delta_up"
    return None


def ratio_value(clause: str) -> tuple[float, str, str] | None:
    match = PERCENT_RE.search(clause)
    if match:
        return float(match.group(1)) / 100.0, match.group(0), "ratio.percent"
    match = CHENG_RE.search(clause)
    if match:
        count = _chinese_number(match.group(1))
        if count is not None:
            return count / 10.0, match.group(0), "ratio.cheng"
    return None


def derive_ops(text: str) -> list[Quantity]:
    """Operations the sentence states unambiguously, field by field.

    A clause contributes at most one operation. When a clause carries both a
    multiplier and a duration ("预算翻倍，步行半小时"), clause splitting keeps
    them apart; when both appear in one clause with one field anchor, the
    multiplier wins only if the field is not a duration field.
    """
    derived: list[Quantity] = []
    seen: set[str] = set()

    for clause in clauses(text):
        field = _field_of(clause)
        if field is None or field in seen:
            continue

        if field == "max_walk_time_min":
            delta = duration_delta(clause)
            if delta:
                value, evidence, rule = delta
                derived.append(Quantity(field, "delta", value, evidence, rule))
                seen.add(field)
                continue
            duration = duration_minutes(clause)
            if duration:
                value, evidence, rule = duration
                derived.append(Quantity(field, "set", value, evidence, rule))
                seen.add(field)
                # "步行阈值改成半小时" is a set: the target is absolute.
                continue

        if field == "population_demand_ratio":
            point_delta = ratio_point_delta(clause)
            if point_delta:
                value, evidence, rule = point_delta
                derived.append(Quantity(field, "delta", value, evidence, rule))
                seen.add(field)
                continue
            ratio = ratio_value(clause)
            if ratio:
                value, evidence, rule = ratio
                derived.append(Quantity(field, "set", value, evidence, rule))
                seen.add(field)
                continue

        factor = multiplier(clause)
        if factor:
            value, evidence, rule = factor
            derived.append(Quantity(field, "scale", value, evidence, rule))
            seen.add(field)
            continue

        if field == "budget_units":
            amount = re.search(r"-?\d+(?:\.\d+)?", clause)
            if amount and DELTA_VERB_RE.search(clause[:amount.start()] or ""):
                sign = -1.0 if re.search(r"减少|减|降低|下调|少", clause) else 1.0
                derived.append(Quantity(field, "delta", sign * float(amount.group(0)),
                                        clause.strip(), "budget.delta"))
                seen.add(field)

    return derived


def numbers(text: str) -> list[float]:
    """Every numeric literal the user actually typed."""
    found: list[float] = []
    for token in re.findall(r"-?\d+(?:\.\d+)?", text or ""):
        try:
            found.append(float(token))
        except ValueError:
            continue
    return found


OPTION_OR_DISABLE_CLAUSE_RE = re.compile(
    r"zs_[0-9a-z]+|扩容|候选|成本|停用|关停|关闭|禁用", re.I
)


def _clauses_for_field(text: str, field: str) -> list[str]:
    """Return clauses belonging to `field`.

    Subjectless continuation clauses (e.g. '把预算设为 10，不对，改成 20') inherit
    the active scalar field until a new field anchor or a shelter-option/shutdown
    clause appears.
    """
    matched: list[str] = []
    active_field: str | None = None
    for clause in clauses(text):
        detected = _field_of(clause)
        if detected is not None:
            active_field = detected
        else:
            if OPTION_OR_DISABLE_CLAUSE_RE.search(clause):
                active_field = None
            elif active_field is None:
                if re.search(r"分钟|小时|一刻钟", clause):
                    active_field = "max_walk_time_min"
                elif re.search(r"百分点|[一二三四五六七八九十\d]+\s*成", clause):
                    active_field = "population_demand_ratio"
        if active_field == field:
            matched.append(clause)
    return matched


def supports(text: str, field: str, op: str, value) -> tuple[bool, str, float | None]:
    """Is this operation supported by the sentence?

    Returns (ok, reason, override). `override` is set when the text states a
    *different but equally unambiguous* value - the caller then uses the text,
    because the sentence is the authority, not the extraction.
    """
    text = text or ""
    field_clauses = _clauses_for_field(text, field)
    typed = [number for clause in field_clauses for number in numbers(clause)]

    if op == "restore":
        if RESTORE_VERB_RE.search(text) or history_reference(text):
            return True, "", None
        return False, "撤销/恢复/历史引用类操作在句子里没有依据", None

    if op == "scale":
        found = next((found for clause in field_clauses
                      if (found := multiplier(clause)) is not None), None)
        if found is None:
            return False, "倍数类操作在句子里没有依据（没有“翻倍/减半/N倍/X%”）", None
        factor, _evidence, _rule = found
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False, "倍数不是数字", None
        if abs(numeric - factor) > 1e-9:
            return True, "按句中的倍数改写", factor
        return True, "", None

    if op == "delta":
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False, "增量不是数字", None
        # "需求比例再提高 5 个百分点" reaches the normaliser as 0.05 while the
        # sentence literally contains 5. A ratio delta is *stated* in percentage
        # points, so both spellings are legitimate evidence for the same op.
        candidates = ((abs(numeric), abs(numeric) * 100.0)
                      if field == "population_demand_ratio" else (abs(numeric),))
        base = min((abs(number) for number in typed),
                   key=lambda n: min(abs(n - candidate) for candidate in candidates),
                   default=None)
        # "再放宽半小时" contains no literal 30, but the phrase unambiguously
        # means 30 minutes. The derived duration is therefore valid evidence
        # for a delta of the same magnitude.
        duration_ok = any(
            duration is not None
            and abs(duration[0] - abs(numeric)) < 1e-9
            and duration_delta(clause) is not None
            for clause in field_clauses
            for duration in [duration_minutes(clause)]
        )
        literal_ok = (base is not None
                      and min(abs(base - candidate) for candidate in candidates) <= 1e-9)
        if not (literal_ok or duration_ok):
            return False, "增量数值在句子里找不到", None
        if not any(DELTA_VERB_RE.search(clause) for clause in field_clauses) and not duration_ok:
            return False, "句子里没有增加/减少类动词", None
        return True, "", None

    if op == "set":
        if field == "disabled_shelter_ids":
            return True, "", None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return True, "", None
        candidates = {round(numeric, 6)}
        if field == "population_demand_ratio":
            candidates.add(round(numeric * 100.0, 6))
        elif field == "max_walk_time_min":
            candidates.add(round(numeric * 60.0, 6))
        if any(abs(number - candidate) < 1e-6 for number in typed for candidate in candidates):
            return True, "", None
        # A value derived from a percentage/成 phrase is supported even though the
        # literal "0.3" never appears ("30%" and "三成" both produce it).
        ratios = [ratio_value(clause) for clause in field_clauses]
        if field == "population_demand_ratio" and any(
                ratio and abs(ratio[0] - numeric) < 1e-9 for ratio in ratios):
            return True, "", None
        durations = [duration_minutes(clause) for clause in field_clauses]
        if field == "max_walk_time_min" and any(
                duration and abs(duration[0] - numeric) < 1e-9 for duration in durations):
            return True, "", None
        return False, f"数值 {numeric:g} 在句子里找不到", None

    return True, "", None
