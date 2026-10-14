"""AC-anchor sub-task scope flagging (parent-story background drift)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.acanc_terms import _case_terms, _terms
from tools.models import TestCase

# literal name: log records stay under the pre-split module name
logger = logging.getLogger("tools.ac_anchor")

# --- Sub-task scope flagging (parent-story background) ----------------------
#
# When a Jira SUB-TASK is the target, its parent story is injected as BACKGROUND
# so the model understands the surrounding behaviour. The risk is scope drift:
# cases that test the PARENT instead of the sub-task. These helpers surface that
# drift next to the AC-anchoring advisory above. They FLAG ONLY — no case is ever
# dropped, reordered or mutated, so they are safe to run before the AC filter.

# If the heuristic would flag more than this share of the suite it is reacting to
# a vocabulary mismatch, not to real scope drift — so it suppresses itself rather
# than spamming the tester with a useless wall of tc_ids.
_MAX_FLAG_RATIO = 0.5


class _Scope(NamedTuple):
    target: set[str]
    parent_only: set[str]


def _scope_terms(target_text: str, parent_context: str) -> _Scope | None:
    target = _terms(target_text)
    if not target:
        return None
    parent_only = _terms(parent_context) - target
    if not parent_only:
        return None
    return _Scope(target, parent_only)


def _drifts(tc: TestCase, scope: _Scope) -> bool:
    terms = _case_terms(tc)
    if not terms or (terms & scope.target):
        return False
    return bool(terms & scope.parent_only)


def _flagged_ids(cases: list[TestCase], scope: _Scope) -> set[str]:
    flagged: set[str] = set()
    for tc in cases:
        if not _drifts(tc, scope):
            continue
        sid = getattr(tc, "stable_id", "") or ""
        if sid:
            flagged.add(sid)
    return flagged


def _within_ratio(flagged: set[str], total: int) -> set[str]:
    if len(flagged) <= total * _MAX_FLAG_RATIO:
        return flagged
    logger.info(
        "Scope heuristic flagged %d/%d cases — suppressing as a vocabulary "
        "mismatch rather than real scope drift",
        len(flagged),
        total,
    )
    return set()


# stable_ids of cases that look like they cover the PARENT background rather
# than the target described in `## Feature to Test`.
#
# Deliberately conservative: a case is flagged only when it shares NO content
# word with the target AND does share one with parent-ONLY material. Returns
# an empty set when that would flag more than _MAX_FLAG_RATIO of the suite.
#
# FLAGS ONLY — never drops, reorders or mutates a case. Never raises; any
# failure yields an empty set (nothing flagged).
def flag_out_of_scope_cases(
    cases: list[TestCase], target_text: str, parent_context: str
) -> set[str]:
    """stable_ids of cases that cover the PARENT background, not the target."""
    try:
        if not cases or not target_text or not parent_context:
            return set()
        scope = _scope_terms(target_text, parent_context)
        if scope is None:
            return set()
        return _within_ratio(_flagged_ids(cases, scope), len(cases))
    except Exception:
        logger.exception("flag_out_of_scope_cases failed — flagging nothing")
        return set()


def _final_ids(cases: list[TestCase], flagged_stable_ids: set[str]) -> list[str]:
    return [
        tc.tc_id
        for tc in cases
        if (getattr(tc, "stable_id", "") or "") in flagged_stable_ids
    ]


def _scope_text(tc_ids: list[str]) -> str:
    return "\n".join(
        [
            "\n\n## Scope Check (advisory)",
            "",
            "These test cases read as covering the PARENT story's background "
            "rather than the specific ticket you asked about. Nothing was "
            "removed — review them and drop any that belong to a different "
            "piece of work:",
            "- " + ", ".join(tc_ids[:20]) + (" …" if len(tc_ids) > 20 else ""),
        ]
    )


def scope_warning_section(cases: list[TestCase], flagged_stable_ids: set[str]) -> str:
    """Advisory markdown naming the flagged cases by their FINAL tc_id.

    Returns "" when nothing was flagged. Never raises.
    """
    try:
        if not cases or not flagged_stable_ids:
            return ""
        tc_ids = _final_ids(cases, flagged_stable_ids)
        if not tc_ids:
            return ""
        return _scope_text(tc_ids)
    except Exception:
        logger.exception("scope_warning_section failed — returning empty string")
        return ""
