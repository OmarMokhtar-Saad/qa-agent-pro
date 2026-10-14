"""Tautological-step detection and per-category ratios (see step_assertion.py)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools import sasrt_text as _txt
from tools.sasrt_text import _get
from tools.sasrt_types import StepAssertionFinding

logger = logging.getLogger("tools.step_assertion")

# Fraction of the ACTION's content tokens the expected result must repeat before
# the expected result counts as a restatement. 0.70 is measured, not guessed:
# see the table in the module docstring -- the observed gap runs 0.67 (highest
# legitimate step) to 1.00 (every tautology), so anything in that band gives the
# same answer on the corpus and 0.70 is its midpoint-ish, safe end.
_MIN_ACTION_OVERLAP = 0.70

# A category is refused when at least this fraction of ITS steps are flagged.
# Per-CATEGORY rather than per-suite because that is how the defect actually
# arrives: two categories were regenerated and came back tautological while the
# other six were untouched. A suite-wide ratio would have read 65/331 = 20% and
# gated nothing. Measured margin: 74% and 78% on the two bad categories, 0% on
# all six others.
_CATEGORY_REFUSE_RATIO = 0.50

# NO truncated-field-name rule. One was drafted and DROPPED: the observed cut
# name was 40 characters and the longest intact one 39, so the rule's entire
# discriminating power was a single character -- inside its own noise floor,
# and the only threshold in this module not backed by a measured gap. It also
# double-reported a field that both echoed its name and was long. A rule that
# cannot be measured is not a weak rule, it is an unmeasured one.


class _Hit(NamedTuple):
    kind: str
    overlap: float
    expected: str


def _hit(step: object) -> _Hit | None:
    """The kind/overlap of a flagged step, or None when the step is clean."""
    action = str(_get(step, "action", "") or "")
    expected = str(_get(step, "expected_result", "") or "")
    if not action or not expected:
        return None
    overlap = _txt.action_overlap(action, expected)
    if _txt.has_generic_phrase(expected):
        kind = "generic-phrase"
    elif overlap >= _MIN_ACTION_OVERLAP:
        kind = "restates-action"
    else:
        return None
    return _Hit(kind, round(overlap, 2), expected[:160])


def _finding(ids: tuple, step: object, hit: _Hit) -> StepAssertionFinding:
    """Build the finding for a flagged step; ``ids`` is (tc_id, category)."""
    return StepAssertionFinding(
        tc_id=ids[0],
        category=ids[1],
        step_number=int(_get(step, "step_number", 0) or 0),
        kind=hit.kind,
        overlap=hit.overlap,
        expected=hit.expected,
    )


def find_tautological_steps(cases: list) -> list[StepAssertionFinding]:
    """Steps whose expected_result restates the action or asserts nothing.

    Returns [] on any internal error. Never raises.
    """
    findings: list[StepAssertionFinding] = []
    try:
        for case in cases or []:
            tc_id = str(_get(case, "tc_id", "") or "")
            category = str(_get(case, "category", "") or "")
            for step in _get(case, "steps", None) or []:
                hit = _hit(step)
                if hit is not None:
                    findings.append(_finding((tc_id, category), step, hit))
    except Exception:
        logger.exception("find_tautological_steps failed - returning what was found")
    return findings


def _case_tally(case: object, flagged: set) -> tuple:
    """``(category, flagged_steps, total_steps)`` for one case."""
    category = str(_get(case, "category", "") or "") or "(uncategorised)"
    tc_id = str(_get(case, "tc_id", "") or "")
    steps = list(_get(case, "steps", None) or [])
    numbers = [int(_get(step, "step_number", 0) or 0) for step in steps]
    hit = sum(1 for number in numbers if (tc_id, number) in flagged)
    return category, hit, len(steps)


def category_flag_ratios(cases: list) -> dict:
    """``{category: (flagged_steps, total_steps)}`` for every category present.

    Grouped on the case's ``category``, which on the staged per-category route is
    server-derived rather than host self-report.
    """
    totals: dict = {}
    try:
        flagged = {(f.tc_id, f.step_number) for f in find_tautological_steps(cases)}
        for case in cases or []:
            category, hit, total = _case_tally(case, flagged)
            prev_hit, prev_total = totals.get(category, (0, 0))
            totals[category] = (prev_hit + hit, prev_total + total)
    except Exception:
        logger.exception("category_flag_ratios failed - returning what was counted")
    return totals


def categories_over_threshold(cases: list) -> list:
    """``[(category, flagged, total, ratio)]`` for categories at or over the
    refusal threshold, worst first. Empty list means nothing is refused.
    """
    over: list = []
    try:
        for category, (hit, total) in category_flag_ratios(cases).items():
            if not total:
                continue
            ratio = hit / total
            if ratio >= _CATEGORY_REFUSE_RATIO:
                over.append((category, hit, total, round(ratio, 3)))
        over.sort(key=lambda row: row[3], reverse=True)
    except Exception:
        logger.exception("categories_over_threshold failed")
    return over
