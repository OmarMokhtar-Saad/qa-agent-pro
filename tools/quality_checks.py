"""Cheap heuristic quality gate for generated test cases.

Flags vague step actions (e.g. "enter any value") and placeholder test_data
(e.g. "anything") so drift can't silently reach the exported files. This is a
bounded, regex-based heuristic — not a semantic check — kept intentionally
cheap so it can run on every category's output without an extra LLM call.
Never raises to callers.

Deliberately does NOT touch technical/security step *content* — it only flags
vague phrasing versus a literal value; a step that opens DevTools, inspects
headers, or uses a SQL/XSS payload is fine as long as the payload/value/field
is spelled out.
"""

from __future__ import annotations

import logging
import re

from tools.models import TestCase

logger = logging.getLogger(__name__)

# Vague/non-concrete step action phrasing — the step names an action but not
# the literal value/payload used, e.g. "enter a classic SQL injection string"
# instead of "Enter ' OR '1'='1 into the 'Username' field".
_VAGUE_ACTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\benter\s+(a|an|any|some)\s+valid\b", re.IGNORECASE),
    re.compile(r"\benter\s+any\s+value\b", re.IGNORECASE),
    re.compile(r"\benter\s+some\s+value\b", re.IGNORECASE),
    re.compile(r"\benter\s+a\s+random\b", re.IGNORECASE),
    re.compile(r"\buse\s+a\s+random\b", re.IGNORECASE),
    re.compile(r"\b(a|an)\s+classic\s+sql\s+injection\s+string\b", re.IGNORECASE),
    re.compile(r"\ba\s+sql\s+injection\s+string\b(?!\s*[:(].{0,60})", re.IGNORECASE),
    re.compile(r"\bany\s+(invalid|incorrect)\s+value\b", re.IGNORECASE),
    re.compile(r"\bsome\s+(invalid|incorrect)\s+data\b", re.IGNORECASE),
]

# Vague expected_result phrasing — asserts a qualifier ("appropriate error
# message", "works correctly") instead of the concrete observable outcome the
# tester should verify (the exact on-screen message, field/button state, or
# resulting page). These qualifiers are almost never followed by the actual
# text, so flagging them has a low false-positive rate.
_VAGUE_EXPECTED_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\b(appropriate|proper|suitable|relevant)\s+(error\s+)?(message|response|validation|feedback)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(behaves?|works?|functions?)\s+(correctly|as\s+expected)\b", re.IGNORECASE
    ),
    re.compile(
        r"\bhandled\s+(gracefully|properly|appropriately|correctly)\b", re.IGNORECASE
    ),
    re.compile(r"\bvalidat(?:es|ed|ion)\s+properly\b", re.IGNORECASE),
]

# Placeholder test_data values — a field name with no concrete example value.
_PLACEHOLDER_DATA_VALUES = {
    "anything",
    "any value",
    "any password",
    "any data",
    "some value",
    "valid data",
    "n/a",
    "na",
    "tbd",
    "todo",
}


# A concrete value present in the same text — a quoted literal, a 4+ digit run
# (phone/id/amount), or an email — means the step/result is actionable even if it
# also contains a "valid X" / "appropriate message" qualifier, so it should NOT be
# flagged as vague (e.g. "Enter a valid mobile number '01111222333'").
_CONCRETE_VALUE_RE = re.compile(r"""'[^']{2,}'|"[^"]{2,}"|\d{4,}|@\w+\.\w+""")


def _has_concrete_value(text: str) -> bool:
    try:
        return bool(_CONCRETE_VALUE_RE.search(text))
    except Exception:
        return False


def _is_vague_action(action: str) -> bool:
    try:
        if not any(p.search(action) for p in _VAGUE_ACTION_PATTERNS):
            return False
        return not _has_concrete_value(action)
    except Exception:
        return False


def _is_vague_expected(expected: str | None) -> bool:
    if not expected:
        return False
    try:
        if not any(p.search(expected) for p in _VAGUE_EXPECTED_PATTERNS):
            return False
        return not _has_concrete_value(expected)
    except Exception:
        return False


def _is_placeholder_data(test_data: str | None) -> bool:
    if not test_data:
        return False
    try:
        normalized = test_data.strip().lower()
        if normalized in _PLACEHOLDER_DATA_VALUES:
            return True
        # "field: <placeholder>" form, e.g. "password: anything"
        for part in re.split(r"[,;\n]", normalized):
            value = part.split(":", 1)[-1].strip()
            if value in _PLACEHOLDER_DATA_VALUES:
                return True
        return False
    except Exception:
        return False


def find_vague_steps(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """Return (tc_id, step_number, action) for every step with vague phrasing. Never raises."""
    try:
        return [
            (tc.tc_id, step.step_number, step.action)
            for tc in cases
            for step in tc.steps
            if _is_vague_action(step.action)
        ]
    except Exception:
        logger.exception("find_vague_steps failed — returning empty list")
        return []


def find_vague_expected(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """Return (tc_id, step_number, expected_result) for every step whose expected
    result uses vague qualifier phrasing instead of a concrete outcome. Never raises."""
    try:
        return [
            (tc.tc_id, step.step_number, step.expected_result)
            for tc in cases
            for step in tc.steps
            if _is_vague_expected(step.expected_result)
        ]
    except Exception:
        logger.exception("find_vague_expected failed — returning empty list")
        return []


def find_placeholder_data(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """Return (tc_id, step_number, test_data) for every step with placeholder test data. Never raises."""
    try:
        return [
            (tc.tc_id, step.step_number, step.test_data)
            for tc in cases
            for step in tc.steps
            if _is_placeholder_data(step.test_data)
        ]
    except Exception:
        logger.exception("find_placeholder_data failed — returning empty list")
        return []


def quality_ratio(cases: list[TestCase]) -> float:
    """Fraction of steps that are vague and/or have placeholder test data. Never raises."""
    try:
        total_steps = sum(len(tc.steps) for tc in cases)
        if total_steps == 0:
            return 0.0
        flagged_steps: set[tuple[str, int]] = set()
        for tc_id, step_number, _ in find_vague_steps(cases):
            flagged_steps.add((tc_id, step_number))
        for tc_id, step_number, _ in find_placeholder_data(cases):
            flagged_steps.add((tc_id, step_number))
        for tc_id, step_number, _ in find_vague_expected(cases):
            flagged_steps.add((tc_id, step_number))
        return len(flagged_steps) / total_steps
    except Exception:
        logger.exception("quality_ratio failed — returning 0.0")
        return 0.0


def quality_warning_section(cases: list[TestCase], max_examples: int = 10) -> str:
    """Build a '## Data Quality Notes' markdown block flagging vague/placeholder
    content that survived generation. Returns '' when nothing is flagged or on
    any internal error. Never raises."""
    try:
        vague = find_vague_steps(cases)
        placeholder = find_placeholder_data(cases)
        vague_expected = find_vague_expected(cases)
        if not vague and not placeholder and not vague_expected:
            return ""

        lines = ["\n\n## Data Quality Notes\n"]
        if vague:
            lines.append(
                f"- {len(vague)} step(s) may use vague phrasing instead of a literal "
                "value — please review before executing:"
            )
            for tc_id, step_number, action in vague[:max_examples]:
                lines.append(f"  - {tc_id} step {step_number}: {action[:120]}")
        if placeholder:
            lines.append(
                f"- {len(placeholder)} step(s) have placeholder test data instead of "
                "a concrete example value:"
            )
            for tc_id, step_number, test_data in placeholder[:max_examples]:
                lines.append(f"  - {tc_id} step {step_number}: {test_data}")
        if vague_expected:
            lines.append(
                f"- {len(vague_expected)} step(s) have a vague expected result "
                '(e.g. "appropriate error message") instead of the concrete outcome '
                "to verify:"
            )
            for tc_id, step_number, expected in vague_expected[:max_examples]:
                lines.append(f"  - {tc_id} step {step_number}: {expected[:120]}")
        return "\n".join(lines)
    except Exception:
        logger.exception("quality_warning_section failed — returning empty string")
        return ""
