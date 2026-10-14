"""Binding a step to a range and reporting contradictions (``bound_consistency``)."""

from __future__ import annotations

import logging
import re

from tools.bcons_model import BoundFinding, _Bound
from tools.bcons_parse import parse_bounds
from tools.bcons_text import _tokens
from tools.bcons_value import entered_value
from tools.models import TestCase

logger = logging.getLogger("tools.bound_consistency")

_REJECT_RE = re.compile(
    r"\b(?:error|invalid|reject|declin|not saved|unchanged|retained|disabled|"
    r"must be|blocked|fail)",
    re.IGNORECASE,
)
# "not saved" and "never updated" are rejections wearing an acceptance verb;
# without the lookbehinds every negative expected result reads as both
# polarities and the check goes silent on the cases it exists for.
_ACCEPT_RE = re.compile(
    r"(?<!not )(?<!never )\b(?:saved|success|updated|accepted|applied|confirm)",
    re.IGNORECASE,
)

# How many DISTINCTIVE subject tokens a step must share with a range before it
# is bound to it. Two, not one: with one, any step mentioning "limit" binds to
# the per-transaction range, and mis-binding is precisely the mistake TC-094
# itself made.
_MIN_SUBJECT_OVERLAP = 2


def _bind(step_tokens: set[str], bounds: list[_Bound]) -> _Bound | None:
    """The one range this step is unambiguously about, or None."""
    if not bounds:
        return None
    scored = [(len(bound.distinctive & step_tokens), bound) for bound in bounds]
    best = max(score for score, _ in scored)
    if best < _MIN_SUBJECT_OVERLAP:
        return None
    if sum(1 for score, _ in scored if score == best) > 1:
        return None
    return next(bound for score, bound in scored if score == best)


def find_bound_contradictions(
    cases: list[TestCase], grounding_text: str
) -> list[BoundFinding]:
    """Steps whose entered value contradicts a numeric bound in the source.

    Returns [] when the source states no range, when nothing binds, or on any
    internal error. Never raises.
    """
    findings: list[BoundFinding] = []
    try:
        bounds = parse_bounds(grounding_text)
        if not bounds:
            return []
        for case in cases or []:
            _find_bound_contradictions_case(case, bounds, findings)
    except Exception:
        logger.exception("find_bound_contradictions failed - returning what was found")
    return findings


def _case_tokens(case: TestCase) -> set[str]:
    case_tokens = _tokens(getattr(case, "title", "") or "")
    for datum in getattr(case, "test_data", None) or []:
        case_tokens |= _tokens(str(getattr(datum, "field", "") or ""))
    return case_tokens


def _resolved_step(
    step: object, tokens: set[str], bounds: list[_Bound]
) -> tuple[_Bound, float] | None:
    action = getattr(step, "action", "") or ""
    value = entered_value(action)
    if value is None:
        return None
    bound = _bind(tokens | _tokens(action), bounds)
    if bound is None:
        return None
    return bound, value


# Both readings, or neither -- the expected result does not commit to an
# outcome, so there is nothing to contradict.
def _contradiction_kind(legal: bool, expected: str) -> str | None:
    rejected = bool(_REJECT_RE.search(expected))
    accepted = bool(_ACCEPT_RE.search(expected))
    if rejected == accepted:
        return None
    if legal and rejected:
        return "legal-rejected"
    if not legal and accepted:
        return "illegal-accepted"
    return None


def _step_finding(
    case: TestCase, step: object, resolved: tuple[_Bound, float]
) -> BoundFinding | None:
    bound, value = resolved
    expected = getattr(step, "expected_result", "") or ""
    kind = _contradiction_kind(bound.is_legal(value), expected)
    if kind is None:
        return None
    return BoundFinding(
        tc_id=getattr(case, "tc_id", "") or "",
        step_number=int(getattr(step, "step_number", 0) or 0),
        value=value,
        subject=bound.label,
        low=bound.low,
        high=bound.high,
        kind=kind,
    )


def _find_bound_contradictions_case(
    case: TestCase, bounds: list[_Bound], findings: list[BoundFinding]
) -> None:
    """Append to ``findings`` each step of ``case`` that contradicts a bound."""
    tokens = _case_tokens(case)
    for step in getattr(case, "steps", None) or []:
        resolved = _resolved_step(step, tokens, bounds)
        finding = _step_finding(case, step, resolved) if resolved else None
        if finding is not None:
            findings.append(finding)
