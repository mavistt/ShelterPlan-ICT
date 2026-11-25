"""Numeric citation auditing: every planning number in an answer must come from
the authoritative result, never from the model.

Authoritative values are collected recursively from a JSON document (the
projected summary for the harness path, or the ground-truth fields for
evaluation). A cited number "matches" when it agrees with any authoritative
value within a small tolerance (models are allowed to round).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

KEYWORDS = ("覆盖", "服务", "需求", "缺口", "未满足", "预算", "成本", "步行",
            "比例", "阈值", "分钟", "人", "served", "demand", "budget", "walk")
NEGATIONS = ("没有", "不是", "未", "并非", "低于", "不超过", "不足", "不满足")
NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
SENTENCE_SPLIT_RE = re.compile(r"[。！？!?;\n]")

TOLERANCE_ABS = 1.0
TOLERANCE_REL = 0.005


def collect_numbers(value: Any) -> set[float]:
    """Every numeric leaf in a JSON document, recursively."""
    numbers: set[float] = set()
    if isinstance(value, bool):
        return numbers
    if isinstance(value, (int, float)):
        if value == value and value not in (float("inf"), float("-inf")):
            numbers.add(float(value))
    elif isinstance(value, dict):
        for child in value.values():
            numbers |= collect_numbers(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            numbers |= collect_numbers(child)
    return numbers


def extract_planning_numbers(text: str) -> list[float]:
    """Numbers from affirmative planning sentences.

    Negated statements ("覆盖率没有达到80%") are skipped: denying a wrong number
    is exactly what a trustworthy assistant should do.
    """
    cited: list[float] = []
    for sentence in SENTENCE_SPLIT_RE.split(text or ""):
        if not any(keyword in sentence for keyword in KEYWORDS):
            continue
        for match in NUMBER_RE.finditer(sentence):
            window = sentence[max(0, match.start() - 4):match.start()]
            if any(token in window for token in NEGATIONS):
                continue
            try:
                cited.append(float(match.group().replace(",", "")))
            except ValueError:  # pragma: no cover
                continue
    return cited


def _matches(cited: float, authoritative: Iterable[float]) -> bool:
    values = list(authoritative)
    for value in values:
        if abs(cited - value) <= max(TOLERANCE_ABS, abs(value) * TOLERANCE_REL):
            return True
    # Percent phrasing of a fraction: "30%" for ratio 0.3 is evidence, not
    # hallucination.
    for value in values:
        if abs(cited - value * 100.0) <= max(0.05, abs(value * 100.0) * 0.01):
            return True
        if abs(cited - value / 100.0) <= max(TOLERANCE_ABS, abs(value) * TOLERANCE_REL):
            return True
    # Coarse rounding of large counts ("约7,600" for 7,696): same hundred-bucket.
    if abs(cited) >= 1000:
        for value in values:
            if abs(value) >= 1000 and int(value // 100) == int(cited // 100):
                return True
    # Derived quotients: a percentage or ratio computed FROM tool numbers is
    # arithmetic on evidence, not hallucination (e.g. served/total*100).
    for a in values:
        for b in values:
            if abs(b) < 1e-9:
                continue
            quotient = a / b
            if abs(cited - quotient * 100.0) <= max(0.05, abs(quotient * 100.0) * 0.01):
                return True
            if abs(cited - quotient) <= max(0.005, abs(quotient) * 0.01):
                return True
    return False


def check_citations(text: str, authoritative: Any) -> dict[str, Any]:
    """Return a structured audit of the planning numbers used in `text`."""
    allowed = collect_numbers(authoritative)
    cited = extract_planning_numbers(text)
    violations = [number for number in cited if not _matches(number, allowed)]
    return {
        "cited_numbers": cited,
        "violations": violations,
        "consistent": not violations,
        "authoritative_count": len(allowed),
    }
