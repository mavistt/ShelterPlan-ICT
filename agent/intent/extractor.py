"""Intent extraction: one small LLM call, deterministic repair, rule fallback.

The expensive part of an agent turn is *semantic judgement*. It is needed
exactly once per user utterance: "what does the user want, and which numbers
did they name?". Everything after that is deterministic.

Failure ladder (cheapest first):

1. decode the model's JSON as-is;
2. repair it deterministically (fences, quotes, trailing commas, prose);
3. re-ask once with the validation error (a real semantic re-judgement);
4. fall back to the rule extractor so the conversation still makes progress.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any

from agent.contracts import ScenarioParams
from agent.errors import HarnessError
from agent.intent import quantities
from agent.intent.schema import (
    INTENT_SCHEMA_HINT,
    IntentParseError,
    ParsedIntent,
    ParamOp,
    parse_intent_payload,
    repair_json,
)

SYSTEM_PROMPT = (
    "你是 ShelterPlan 规划助手的意图解析器。只输出一个 JSON 对象，不要解释，不要 Markdown。\n"
    "JSON 结构：\n" + INTENT_SCHEMA_HINT + "\n"
    "示例（照此风格，字段可省略）：\n"
    '用户：评估一下现状 -> {"intent":"assess","ops":[],"options":[],"compare":null,'
    '"sensitivity":null,"confidence":0.9,"clarify":[]}\n'
    '用户：预算6，给 zs_abc 扩容300人成本1，做优化 -> {"intent":"optimize","ops":'
    '[{"field":"budget_units","op":"set","value":6}],"options":'
    '[{"shelter_id":"zs_abc","delta_capacity_persons":300,"cost_units":1}],'
    '"compare":null,"sensitivity":null,"confidence":0.9,"clarify":[]}\n'
    '用户：预算减半 -> {"intent":"modify_scenario","ops":'
    '[{"field":"budget_units","op":"scale","value":0.5}],"confidence":0.9}\n'
    '用户：步行阈值改成20分钟，其它不变 -> {"intent":"modify_scenario","ops":'
    '[{"field":"max_walk_time_min","op":"set","value":20}],"confidence":0.9}\n'
    '用户：比较需求比例0.2和0.4 -> {"intent":"compare","ops":[],"compare":'
    '{"field":"population_demand_ratio","values":[0.2,0.4]},"confidence":0.9}\n'
    '用户：想优化但没说预算 -> {"intent":"optimize","ops":[],"clarify":["budget_units"]}\n'
    "规则：\n"
    "1. 扩容候选只放 options，格式 {shelter_id, delta_capacity_persons, cost_units}；"
    "不要写成 disabled_shelter_ids。\n"
    "2. 只有“关停/停用/关闭/禁用”才用 disabled_shelter_ids。\n"
    "3. 用户本轮没提到的参数不要写进 ops；“其它不变”等于什么都不写。\n"
    "4. 改成/设为 -> set；再加/减少 -> delta；减半/翻倍/乘以 -> scale；恢复/还原 -> restore。\n"
    "5. 缺少预算、候选、停用场所等关键参数时，把字段名放进 clarify，不要编造数值。\n"
    "6. 场所 ID 原样保留。"
)

MAX_ATTEMPTS = 2


@dataclass
class ExtractionResult:
    intent: ParsedIntent
    mode: str  # llm | llm_repaired | llm_retry | rules | rules_fallback
    llm_calls: int = 0
    llm_latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None
    raw_text: str | None = None
    call_stats: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "mode": self.mode,
            "llm_calls": self.llm_calls,
            "llm_latency_ms": round(self.llm_latency_ms, 1),
            "error": self.error,
        }


class IntentExtractor:
    """Wraps any object exposing `chat(messages, ...)` (adapter or scripted LLM)."""

    def __init__(self, llm: Any, *, max_tokens: int = 900,
                 reasoning_effort: str | None = None) -> None:
        self.llm = llm
        self.max_tokens = max_tokens
        # Extraction is classification + slot filling, not open-ended reasoning.
        # Disabling hidden thinking on this single call is a 5-10x latency win
        # when the model supports it; None keeps the server default.
        self.reasoning_effort = reasoning_effort

    def extract(self, text: str, *, params: ScenarioParams,
                last_task: str | None = None,
                last_actions: list[str] | None = None) -> ExtractionResult:
        messages = self._messages(text, params, last_task, last_actions)
        total_latency = 0.0
        prompt_tokens = completion_tokens = 0
        last_error: str | None = None
        calls = 0
        call_stats: list[dict[str, Any]] = []

        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                result = self.llm.chat(messages, max_tokens=self.max_tokens,
                                       response_format={"type": "json_object"},
                                       reasoning_effort=self.reasoning_effort)
            except HarnessError as exc:
                elapsed = (time.perf_counter() - started) * 1000
                total_latency += elapsed
                last_error = f"{exc.code}: {exc.message[:160]}"
                calls += 1
                call_stats.append({"latency_ms": round(elapsed, 1),
                                   "prompt_tokens": 0, "completion_tokens": 0})
                break
            calls += 1
            elapsed = (time.perf_counter() - started) * 1000
            total_latency += elapsed
            call_prompt = int(result.usage.get("prompt_tokens") or 0)
            call_completion = int(result.usage.get("completion_tokens") or 0)
            prompt_tokens += call_prompt
            completion_tokens += call_completion
            call_stats.append({"latency_ms": round(elapsed, 1),
                               "prompt_tokens": call_prompt,
                               "completion_tokens": call_completion})
            content = result.content or ""

            payload, repaired = _decode(content)
            if payload is not None:
                try:
                    parsed = parse_intent_payload(payload)
                    parsed = _deterministic_repairs(text, parsed)
                except IntentParseError as exc:
                    last_error = f"{exc.code}: {exc.message}"
                else:
                    if attempt > 1:
                        mode = "llm_retry"
                    elif repaired:
                        mode = "llm_repaired"
                    else:
                        mode = "llm"
                    return ExtractionResult(
                        intent=parsed, mode=mode, llm_calls=calls,
                        llm_latency_ms=total_latency, prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        raw_text=content[:600], call_stats=call_stats,
                    )
            else:
                last_error = "INTENT_PARSE_ERROR: response contained no JSON object"

            if attempt < MAX_ATTEMPTS:
                messages = messages + [
                    {"role": "assistant", "content": content[:800]},
                    {"role": "user", "content": (
                        "上面的输出不是合法 JSON 或不符合结构：" + (last_error or "") +
                        "。请只输出一个 JSON 对象，字段与枚举严格遵守前面的定义。")},
                ]

        fallback = rule_extract(text)
        fallback.warnings.append(f"llm extraction failed: {last_error}")
        return ExtractionResult(
            intent=fallback, mode="rules_fallback", llm_calls=calls,
            llm_latency_ms=total_latency, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, error=last_error,
            raw_text=None, call_stats=call_stats,
        )

    # ------------------------------------------------------------------ #
    def _messages(self, text: str, params: ScenarioParams,
                  last_task: str | None, last_actions: list[str] | None) -> list[dict[str, Any]]:
        snapshot = {key: value for key, value in params.business_fields().items()}
        context = (
            "当前参数：" + _compact(snapshot) + "\n"
            f"上一轮任务：{last_task or '无'}\n"
            f"最近动作：{', '.join((last_actions or [])[-3:]) or '无'}\n"
            f"用户：{text}"
        )
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context}]


def _deterministic_repairs(text: str, intent: ParsedIntent) -> ParsedIntent:
    """Fix field-level mistakes that are 100% decidable from the text itself.

    These are not new model calls: they are the "cheap repair" layer, and they
    are also the safety layer. Three cases where trusting the model would
    silently change the plan:

    * a literal negative budget ("budget -5") can only mean `set -5`;
    * an explicitly stated ratio ("ratio 150%") is a target value, never a
      multiplier - it must reach the guard even when out of range;
    * a shelter id the user never typed must never reach Module B, whether as
      a shutdown target or as an improvement option.
    """
    mentioned_ids = set(ID_RE.findall(text))

    # 1. literal negative budget -------------------------------------------
    if intent.name not in {"compare", "sensitivity"}:
        match = LITERAL_NEGATIVE_RE.search(text)
        if match:
            value = float(match.group(1))
            intent.ops = [ParamOp("budget_units", "set", value)
                          if op.field == "budget_units" else op
                          for op in intent.ops]
            if not any(op.field == "budget_units" for op in intent.ops):
                intent.ops.append(ParamOp("budget_units", "set", value))

    # 2. explicit ratio literal -------------------------------------------
    if intent.name not in {"compare", "sensitivity"}:
        ratio = RATIO_LITERAL_RE.search(text)
        if ratio:
            raw = float(ratio.group(1))
            is_percent = "%" in text[ratio.start():ratio.end()]
            value = raw / 100.0 if is_percent else raw
            intent.ops = [op for op in intent.ops if op.field != "population_demand_ratio"]
            intent.ops.append(ParamOp("population_demand_ratio", "set", value))

    # 2a. phrasing that has exactly one arithmetic reading wins over the model.
    # "预算翻倍" is x2, "步行半小时" is 30 minutes. A model that answered
    # `scale: 6` or `scale: 7.5` was not misunderstood - it was wrong, and the
    # sentence is the authority. Every override is recorded as a warning so the
    # trace shows what changed and why.
    if intent.name not in {"compare", "sensitivity"}:
        for quantity in quantities.derive_ops(text):
            same = any(op.field == quantity.field and op.op == quantity.op
                       and _same_op_value(op.value, quantity.value) for op in intent.ops)
            if same:
                continue
            intent.ops = [op for op in intent.ops if op.field != quantity.field]
            intent.ops.append(quantity.as_op())
            intent.warnings.append(f"op from text: {quantity.rule} = {quantity.value!r}")

    # 2a2. a correction inside the same sentence, or an explicit reference to
    # a previous turn's value. Both are statements about *which value to use*,
    # not new arithmetic, so they must not depend on what the model guessed.
    if intent.name not in {"compare", "sensitivity"}:
        targets = list(TARGET_VALUE_RE.finditer(text))
        if CORRECTION_RE.search(text) and targets:
            match = targets[-1]
            value = float(match.group(1))
            unit = match.group(2) or ""
            if unit in {"%", "个百分点", "百分点"}:
                value /= 100.0
            elif unit == "成":
                value /= 10.0
            field = _nearest_field(text, match.start())
            if field:
                intent.ops = [op for op in intent.ops if op.field != field]
                intent.ops.append(ParamOp(field, "set", value))
                intent.warnings.append(f"corrected target wins: {field}={value!r}")
        reference = quantities.history_reference(text)
        if reference:
            field, evidence = reference
            intent.ops = [op for op in intent.ops if op.field != field]
            intent.ops.append(ParamOp(field, "restore", "last"))
            intent.warnings.append(
                f"history reference: {field} <- last snapshot ({evidence})")

    # 2b. "expansion" is not "shutdown" ------------------------------------
    if re.search(r"扩容|增加容量|加容量", text) and not _effective_disable(text):
        if any(op.field == "disabled_shelter_ids" for op in intent.ops):
            intent.ops = [op for op in intent.ops if op.field != "disabled_shelter_ids"]
            intent.warnings.append("dropped shutdown op from an expansion request")
    if not intent.options and re.search(r"扩容\s*\d+\s*人", text):
        repaired = _rule_options(text)
        if repaired:
            intent.options = repaired
            intent.warnings.append("options recovered by rule parser")

    # 2c. plans the model described but did not structure -------------------
    if intent.name == "sensitivity" and not intent.sensitivity:
        repaired = _rule_sensitivity(text)
        if repaired:
            intent.sensitivity = repaired
            intent.warnings.append("sensitivity spec recovered by rule parser")
    if intent.name == "compare" and not intent.compare:
        repaired = _rule_compare(text)
        if repaired:
            intent.compare = repaired
            intent.warnings.append("compare spec recovered by rule parser")

    # 2d. a "compare" without any comparison wording is a misread ----------
    if intent.name == "compare" and not re.search(r"比较|对比|差异|VS|vs", text):
        intent.compare = None
        intent.name = "assess" if re.search(r"评估|现状", text) else "clarify"
        intent.warnings.append("downgraded compare without comparison wording")

    # 2e. task slots the sentence states but the model did not structure.
    # "比较预算2和预算5" often arrives as two `budget set` ops and no compare
    # spec; "给 X 扩容…预算2，但不要停用" often arrives as a stress test.
    # Neither is a guess: the wording is explicit, so the rule layer wins.
    if (intent.name not in {"compare", "sensitivity", "optimize", "stress_test", "diagnose"}
            and COMPARE_CUE_RE.search(text)
            and not re.search(r"优化|压力测试|敏感性|诊断", text)):
        spec = _rule_compare(text)
        if spec:
            intent.name = "compare"
            intent.compare = spec
            intent.ops = [op for op in intent.ops if op.field != spec["field"]]
            intent.warnings.append("comparison intent and paired values recovered by rule parser")
    if (OPTIMIZE_CUE_RE.search(text)
            and re.search(r"预算|成本|优化", text)
            and intent.name not in {"optimize", "compare", "sensitivity"}
            and not re.search(r"压力测试|敏感性|比较|对比", text)):
        intent.name = "optimize"
        intent.warnings.append("optimization intent recovered from expansion table")

    # 2f. a stated bound that contradicts a stated target is not a plan.
    contradiction = _contradictory_bound(text)
    if contradiction and intent.name not in {"compare", "sensitivity"}:
        field, wanted, limit = contradiction
        intent.name = "clarify"
        intent.ops = []
        intent.compare = None
        intent.sensitivity = None
        intent.clarify = [field]
        intent.warnings.append(
            f"contradictory bound: target {wanted:g} exceeds limit {limit:g}")
        return intent

    # 2g. an optimisation cannot run without a budget, and the sentence stated
    # one: recover the literal rather than asking the user to repeat it. This is
    # scoped to `optimize` so other task types keep their own slot rules.
    if (intent.name == "optimize"
            and not any(op.field == "budget_units" for op in intent.ops)):
        budget_match = BUDGET_RE.search(text)
        if budget_match:
            intent.ops.append(ParamOp("budget_units", "set",
                                      float(budget_match.group(1))))
            intent.warnings.append("budget literal recovered from text")

    # 3. never accept a shelter id the user did not mention ----------------
    if any(op.field == "disabled_shelter_ids" and
           any(str(shelter) not in mentioned_ids for shelter in (op.value or []))
           for op in intent.ops):
        intent.ops = [op for op in intent.ops if op.field != "disabled_shelter_ids"]
        intent.warnings.append("dropped disabled_shelter_ids not mentioned by the user")
    if intent.options:
        kept = [row for row in intent.options
                if str(row.get("shelter_id") or "") in mentioned_ids]
        if len(kept) != len(intent.options):
            intent.warnings.append("dropped improvement options with unmentioned shelter ids")
        intent.options = kept

    # 3a. "上次那个结果怎么样？" is a request to explain a result, not an
    # under-specified new analysis. With no result in the session the correct
    # clarification is `result_id`, never a generic "which task?" question.
    if (intent.name in {"clarify", "assess", "modify_scenario"}
            and EXPLAIN_CUE_RE.search(text)
            and not re.search(r"评估|现状|优化|比较|对比|压力测试|敏感性|诊断", text)):
        intent.name = "explain"
        intent.clarify = []
        intent.warnings.append("result explanation recognised deterministically")

    # 3b. no actionable content at all -> ask, do not guess a task ---------
    # "你看着办吧" / "帮我弄一下那个" have no verb, no parameter and no id. Any
    # task chosen here is the model's invention, so the correct action is to
    # ask which analysis the user wants.
    if intent.name not in {"clarify"} and not ACTIONABLE_RE.search(text):
        intent.name = "clarify"
        if not intent.clarify:
            intent.clarify = ["task"]
        intent.warnings.append("downgraded a task intent with no actionable content")

    # 4. an explicit evaluation verb outranks a bare parameter mention -----
    if intent.name == "modify_scenario" and re.search(r"评估|看一下现状|看看现状", text) \
            and not re.search(r"优化|比较|对比|压力测试|敏感性|诊断|解释", text):
        intent.name = "assess"

    # 5. shutdown verb with no id (a negated shutdown is not one) ----------
    if (_effective_disable(text) and not mentioned_ids
            and not re.search(r"评估|现状", text)
            and intent.name not in {"stress_test", "clarify"}):
        intent.name = "stress_test"
    if _effective_disable(text) and not mentioned_ids:
        if "disabled_shelter_ids" not in intent.clarify:
            intent.clarify.append("disabled_shelter_ids")
    return intent


def _unit_scale(unit: str | None) -> float:
    if unit in {"%", "个百分点", "百分点"}:
        return 0.01
    if unit == "成":
        return 0.1
    return 1.0


def _contradictory_bound(text: str) -> tuple[str, float, float] | None:
    """(field, stated target, stated upper bound) when they cannot both hold."""
    target = TARGET_SET_RE.search(text)
    bound = BOUND_RE.search(text)
    if not target or not bound:
        return None
    wanted = float(target.group(1)) * _unit_scale(target.group(2))
    limit = float(bound.group(1)) * _unit_scale(bound.group(2))
    if _unit_scale(target.group(2)) != _unit_scale(bound.group(2)):
        return None
    if wanted <= limit + 1e-9:
        return None
    return (_nearest_field(text, target.start()) or "task"), wanted, limit


def _nearest_field(text: str, before: int) -> str | None:
    """Field anchor closest to (and before) a position in the sentence."""
    best: tuple[int, str] | None = None
    for field, pattern in quantities.FIELD_ANCHORS:
        for match in pattern.finditer(text[:before]):
            if best is None or match.start() > best[0]:
                best = (match.start(), field)
    return best[1] if best else None


def _same_op_value(left: Any, right: Any) -> bool:
    if isinstance(left, (list, tuple)) or isinstance(right, (list, tuple)):
        return list(left or []) == list(right or [])
    try:
        return abs(float(left) - float(right)) <= 1e-9
    except (TypeError, ValueError):
        return left == right


def _decode(content: str) -> tuple[dict[str, Any] | None, bool]:
    """Return (payload, repaired). `repaired` is True when the raw text was not
    already a bare JSON object (fenced block, prose wrapper, smart quotes)."""
    import json
    if not content or not content.strip():
        return None, False
    try:
        parsed = json.loads(content.strip())
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed, False
    return repair_json(content), True


def _compact(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------- #
# Rule extractor: deterministic, dependency-free, used when the model cannot
# produce a valid envelope (or when running fully offline). Deliberately
# conservative: it only fires on unambiguous lexical patterns.
# --------------------------------------------------------------------------- #
ID_RE = re.compile(r"\bzs_[0-9a-z]{6,}\b", re.I)
BUDGET_RE = re.compile(r"预算\s*(?:设为|改成|调整为|为|是|=)?\s*(-?\d+(?:\.\d+)?)")
BUDGET_HALF_RE = re.compile(r"预算[^。；，]*?(减半|翻倍|一半|两倍)")
BUDGET_DELTA_RE = re.compile(r"预算\s*(?:再|又)?\s*(增加|加|减少|减|提高|降低)\s*(\d+(?:\.\d+)?)")
RATIO_PCT_RE = re.compile(r"(?:需求)?比例[^0-9]{0,6}(\d+(?:\.\d+)?)\s*%")
RATIO_CHENG_RE = re.compile(r"([一二三四五六七八九十\d]+)\s*成")
RATIO_DECIMAL_RE = re.compile(r"(?:需求)?比例[^0-9]{0,6}(\d*\.\d+|\d+)")
WALK_RE = re.compile(r"步行[^0-9]{0,8}(\d+(?:\.\d+)?)\s*分钟")
DISABLE_RE = re.compile(r"(关停|停用|关闭|禁用)")
DISABLE_SEGMENT_RE = re.compile(r"(?:关停|停用|关闭|禁用)([^。；\n]*)")
OPTION_RE = re.compile(r"(zs_[0-9a-z]+)[^。；，,]{0,12}?(?:扩容|增加|加)\s*(-?\d+)\s*人")
LITERAL_NEGATIVE_RE = re.compile(r"预算\s*(-\d+(?:\.\d+)?)")
RATIO_LITERAL_RE = re.compile(
    r"(?:需求)?比例\s*(?:设为|改成|调整为|调成|按|为|是|=|:|：)?\s*(\d+(?:\.\d+)?)\s*%?")
# The sign matters: "-1" must reach the guard as -1 and be rejected there.
# Dropping the minus turned "cost -1" into a free expansion (V2.1 §4.4).
OPTION_COST_RE = re.compile(r"成本\s*(-?\d+(?:\.\d+)?)")
# "预算2和预算5" repeats the field name between the conjunction and the number;
# a short non-numeric gap keeps the pair in one clause without swallowing whole
# sentences.
COMPARE_NUMBERS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%|成)?\s*(?:和|、|与|vs\.?|对比)\s*[^0-9]{0,8}?\s*"
    r"(\d+(?:\.\d+)?)\s*(%|成)?")
# "不要停用任何场所" is an instruction NOT to disable, not a stress test.
NEGATED_DISABLE_RE = re.compile(
    r"(?:不要|不用|别|无需|不必|取消|撤销)\s*(?:再)?\s*(?:停用|关停|关闭|禁用)")
# The last explicitly corrected target wins: "先按30%算……不对，改成0.45".
# Only explicit "the earlier value was wrong" cues. A plain "改成 20 分钟"
# is a normal target, not a correction, and must not override other fields.
CORRECTION_RE = re.compile(r"不对|不是|算了|其实|更正|纠正|说错|写错|先按|刚才按|原本")
TARGET_VALUE_RE = re.compile(
    r"(?:改成|改为|设为|调整为|调成|定为|按|用)\s*"
    r"(-?\d+(?:\.\d+)?)\s*(%|个百分点|百分点|成)?")
EXPLAIN_CUE_RE = re.compile(
    r"结果怎么样|上次那个结果|上一条结果|这个结果|那个结果|每个数字|"
    r"结果.*(?:原因|来源|怎么样)|解释|说明一下|为什么.*(?:覆盖|结果)")
OPTIMIZE_CUE_RE = re.compile(r"扩容\s*-?\d+\s*人")
COMPARE_CUE_RE = re.compile(r"比较|对比|差异|两种|两档")
# "改成 0.4，但不超过 0.2" is contradictory. Planning on either number would
# be inventing a resolution the user never gave, so the turn becomes a question.
TARGET_SET_RE = re.compile(
    r"(?:改成|改为|设为|调整为|调成|定为|为)\s*(\d+(?:\.\d+)?)\s*(%|个百分点|百分点|成)?")
BOUND_RE = re.compile(
    r"(?:不超过|不大于|不高于|最多|上限为|上限是|上限|小于等于|≤)\s*"
    r"(\d+(?:\.\d+)?)\s*(%|个百分点|百分点|成)?")
# Any lexical trace of an actual planning request: a task verb, a parameter
# name, a shelter id, or a number the user wants planned with.
ACTIONABLE_RE = re.compile(
    r"评估|现状|优化|扩容|比较|对比|差异|压力测试|敏感性|诊断|解释|模拟|结果|"
    r"预算|比例|步行|阈值|停用|关停|关闭|覆盖|zs_|\d")


def _effective_disable(text: str) -> bool:
    """True only when the sentence really asks to disable shelters."""
    return bool(DISABLE_RE.search(NEGATED_DISABLE_RE.sub("", text or "")))


def rule_extract(text: str) -> ParsedIntent:
    """Conservative lexical extraction. It never guesses a number the user did
    not say; anything ambiguous is left out so the router asks."""
    text = text or ""
    warnings: list[str] = ["rule-based fallback"]

    disabled_ids: list[str] = []
    for match in DISABLE_SEGMENT_RE.finditer(NEGATED_DISABLE_RE.sub("", text)):
        clause = re.split(r"候选|给|然后|预算|做|进行|评估|并", match.group(1))[0]
        for found in ID_RE.findall(clause):
            if found not in disabled_ids:
                disabled_ids.append(found)

    # Intent first: a sensitivity sweep must not also "set" the first value.
    has_ops = bool(BUDGET_RE.search(text) or WALK_RE.search(text) or RATIO_PCT_RE.search(text)
                   or RATIO_CHENG_RE.search(text) or disabled_ids)
    intent = _rule_intent(text, has_ops=has_ops, ids=disabled_ids)

    ops: list[ParamOp] = []
    if intent == "sensitivity":
        sensitivity = _rule_sensitivity(text)
        compare = None
    elif intent == "compare":
        sensitivity = None
        compare = _rule_compare(text)
    else:
        for match in BUDGET_HALF_RE.finditer(text):
            token = match.group(1)
            factor = 0.5 if token in ("减半", "一半") else 2.0
            ops.append(ParamOp("budget_units", "scale", factor))
        delta_match = BUDGET_DELTA_RE.search(text)
        if delta_match:
            direction = -1.0 if delta_match.group(1) in ("减少", "减", "降低") else 1.0
            ops.append(ParamOp("budget_units", "delta",
                               direction * float(delta_match.group(2))))
        elif BUDGET_RE.search(text) and not BUDGET_HALF_RE.search(text):
            ops.append(ParamOp("budget_units", "set", float(BUDGET_RE.search(text).group(1))))

        ratio_match = RATIO_PCT_RE.search(text)
        if ratio_match:
            ops.append(ParamOp("population_demand_ratio", "set",
                               float(ratio_match.group(1)) / 100.0))
        else:
            cheng = RATIO_CHENG_RE.search(text)
            decimal = RATIO_DECIMAL_RE.search(text)
            if cheng:
                ops.append(ParamOp("population_demand_ratio", "set",
                                   _chinese_number(cheng.group(1)) / 10.0))
            elif decimal:
                ops.append(ParamOp("population_demand_ratio", "set",
                                   float(decimal.group(1))))

        walk_match = WALK_RE.search(text)
        if walk_match:
            ops.append(ParamOp("max_walk_time_min", "set", float(walk_match.group(1))))

        if disabled_ids:
            ops.append(ParamOp("disabled_shelter_ids", "set", disabled_ids))
        sensitivity = None
        compare = None

    clarify = []
    if intent == "optimize" and not any(op.field == "budget_units" for op in ops) and "预算" not in text:
        clarify.append("budget_units")
    parsed = ParsedIntent(
        name=intent, ops=ops, compare=compare, sensitivity=sensitivity,
        options=_rule_options(text), confidence=0.4, clarify=clarify,
        source="rules", warnings=warnings, reason="rule-based extraction",
    )
    # The same safety layer the model path gets. Without this, "步行半小时"
    # extracted by rules would stay a bare "no walk change" while the model path
    # repaired it - two code paths drifting apart is exactly what V2.1 measured.
    return _deterministic_repairs(text, parsed)


def _rule_intent(text: str, *, has_ops: bool, ids: list[str]) -> str:
    if re.search(r"解释|说明一下|每个数字|来源", text):
        return "explain"
    if re.search(r"比较|对比|差异", text) or (re.search(r"和|与", text) and _rule_compare(text)):
        return "compare"
    if re.search(r"敏感性|档位|不同预算|预算.*从", text):
        return "sensitivity"
    if re.search(r"诊断|瓶颈|原因|为什么.*没", text):
        return "diagnose"
    if re.search(r"优化|扩容", text) or re.search(r"预算.*(改善|提升)", text):
        return "optimize"
    if re.search(r"压力测试|模拟.*(关|停)", text) or _effective_disable(text):
        return "stress_test"
    if re.search(r"评估|现状|够不够|覆盖情况|服务情况", text):
        return "assess"
    if has_ops or re.search(r"改成|设为|调整为|减半|恢复到", text):
        return "modify_scenario"
    return "clarify"


def _rule_options(text: str) -> list[dict[str, Any]]:
    rows = []
    for index, match in enumerate(OPTION_RE.finditer(text)):
        cost_match = OPTION_COST_RE.search(text[match.end():match.end() + 24]) or OPTION_COST_RE.search(text)
        rows.append({
            "shelter_id": match.group(1),
            "delta_capacity_persons": float(match.group(2)),
            "cost_units": float(cost_match.group(1)) if cost_match else 1.0,
        })
    return rows[:12]


def _rule_compare(text: str) -> dict[str, Any] | None:
    match = COMPARE_NUMBERS_RE.search(text)
    if not match:
        return None
    left, left_unit, right, right_unit = match.groups()
    unit = left_unit or right_unit
    values = [float(left), float(right)]
    prefix = text[max(0, match.start() - 12):match.start()]
    if unit == "%":
        values = [value / 100.0 for value in values]
        field_name = "population_demand_ratio"
    elif unit == "成":
        values = [value / 10.0 for value in values]
        field_name = "population_demand_ratio"
    elif "比例" in prefix or "需求" in prefix:
        field_name = "population_demand_ratio"
    else:
        field_name = "budget_units"
    return {"field": field_name, "values": values}


def _rule_sensitivity(text: str) -> dict[str, Any] | None:
    if "预算" in text:
        parameter, marker = "budget_units", "预算"
    elif "比例" in text:
        parameter, marker = "population_demand_ratio", "比例"
    else:
        return None
    tail = text.split(marker, 1)[1][:60]
    numbers = re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", tail)
    values = [float(value) for value in numbers[:20]]
    if len(values) < 2:
        return None
    return {"parameter": parameter, "values": values}


CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _chinese_number(token: str) -> float:
    if token.isdigit():
        return float(token)
    return float(CHINESE_DIGITS.get(token, 0))
