"""Check a model-written finding against the step results of the turn it ends.

A finding is the model's free text, not something the server observed. When it
claims an action worked (typed, tapped, verified) and the trace shows that kind
of step failed, or shows no such step at all, it is stored marked unverified
rather than as a fact. Generic: it reads op names and outcomes only.
"""

from __future__ import annotations

import re

UNVERIFIED_PREFIX = "[unverified: "

#: Outcomes that are a failed step. ``unknown`` is unproven, not failed.
FAILED_OUTCOMES = frozenset(
    ("missing_element", "assert_fail", "error", "failed", "route_mismatch")
)

#: claim words -> the step ops that would back them.
_FAMILIES = (
    (re.compile(r"\b(typed|types?|filled|fill|entered)\b", re.I), ("type", "fill")),
    (
        re.compile(r"\b(tapped|taps?|clicked|pressed)\b", re.I),
        ("tap", "tap_text", "tap_text_if_present", "click", "press"),
    ),
    (re.compile(r"\b(verified|asserted)\b", re.I), ("assert",)),
)


def _ops(trace: list) -> list[tuple[str, str]]:
    """``(op, outcome)`` for every step of *trace*."""
    out: list[tuple[str, str]] = []
    for item in trace:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        op = str(action.get("op") or "") if isinstance(action, dict) else ""
        out.append((op, str(item.get("outcome") or "")))
    return out


def _problem(ops: list[tuple[str, str]], family: tuple[str, ...]) -> str:
    """Why *family* is not backed by the trace, or ``""`` when it is."""
    mine = [outcome for op, outcome in ops if op in family]
    if not mine:
        return "no " + family[0] + " step ran this turn"
    if any(outcome in FAILED_OUTCOMES for outcome in mine):
        return "a " + family[0] + " step failed this turn"
    return ""


def check_finding(finding: str, trace: object) -> str:
    """*finding*, prefixed ``[unverified: why]`` when the trace contradicts it."""
    text = str(finding or "").strip()
    if not text or text.startswith(UNVERIFIED_PREFIX) or not isinstance(trace, list):
        return text
    ops = _ops(trace)
    for pattern, family in _FAMILIES:
        if pattern.search(text):
            why = _problem(ops, family)
            if why:
                return UNVERIFIED_PREFIX + why + "] " + text
    return text
