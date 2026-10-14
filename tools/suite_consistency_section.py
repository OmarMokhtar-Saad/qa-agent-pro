"""The suite-level advisory block: runs every finder and assembles the bullets."""

from __future__ import annotations

import logging

from tools.bound_consistency import find_bound_contradictions
from tools.coverage_classes import find_missing_coverage_classes
from tools.models import TestCase
from tools.oracle_grounding import find_ungrounded_ui_strings
from tools.suite_consistency_budget import _bound_advisory_block
from tools.suite_consistency_bullets import (
    _add_conditional_bullet,
    _add_conflict_bullet,
    _add_oracle_bullet,
)
from tools.suite_consistency_bullets_src import (
    _add_bounds_bullet,
    _add_classes_bullet,
    _add_ungrounded_bullet,
)
from tools.suite_consistency_conflict import find_contradictory_state_assumptions
from tools.suite_consistency_oracle import (
    find_conditional_actions,
    find_nondeterministic_oracles,
)

logger = logging.getLogger("tools.suite_consistency")


def _findings(cases: list[TestCase], grounding_text: str) -> list:
    return [
        find_nondeterministic_oracles(cases),
        find_conditional_actions(cases),
        find_contradictory_state_assumptions(cases),
        find_ungrounded_ui_strings(cases, grounding_text),
        find_bound_contradictions(cases, grounding_text),
        find_missing_coverage_classes(cases),
    ]


def _bullets(findings: list) -> tuple[list[str], list[tuple[str, int]]]:
    lines = ["\n\n## Suite Consistency (advisory)\n"]
    # (name, priority) per bullet, appended in the same order the bullets
    # are. Priority 0 == protected: the two bullets that say a case is WRONG
    # rather than missing, and so can never be the ones that yield.
    order: list[tuple[str, int]] = []
    adders = (
        _add_oracle_bullet,
        _add_conditional_bullet,
        _add_conflict_bullet,
        _add_ungrounded_bullet,
        _add_bounds_bullet,
        _add_classes_bullet,
    )
    for add, found in zip(adders, findings, strict=True):
        if found:
            add(lines, order, found)
    return lines, order


def _section_body(cases: list[TestCase], grounding_text: str) -> str:
    findings = _findings(cases, grounding_text)
    if not any(findings):
        return ""
    return _bound_advisory_block(*_bullets(findings))


def consistency_warning_section(
    cases: list[TestCase], *, grounding_text: str = ""
) -> str:
    """Markdown block for the suite-level advisories.

    ``grounding_text`` is the SOURCE the suite was generated from. It is
    keyword-only with an empty default on purpose: every caller that
    predates the invented-UI-string bullet keeps its exact behaviour, and
    an empty string makes ``find_ungrounded_ui_strings`` report nothing --
    checking assertions against a source you do not have flags all of them.

    Returns '' when the suite is clean or on any internal error. Never raises.
    """
    try:
        return _section_body(cases, grounding_text)
    except Exception:
        logger.exception("consistency_warning_section failed - returning empty string")
        return ""
