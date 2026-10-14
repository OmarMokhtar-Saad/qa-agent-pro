"""Unfalsifiable-oracle and conditional-action finders for a generated suite."""

from __future__ import annotations

import logging
import re

from tools import suite_consistency_pat_oracle as _po
from tools.models import TestCase
from tools.suite_consistency_pat_disj import (
    _DISJUNCTION_PATTERNS,
    _EQUIVALENT_RENDERING_RE,
    _ESCAPE_HATCH_PATTERNS,
    _NEGATED_DISJUNCTION_RE,
)

logger = logging.getLogger("tools.suite_consistency")


def _is_disjunctive(expected: str, stripped: str) -> bool:
    return any(p.search(expected) for p in _po.NONDETERMINISTIC_PATTERNS) and any(
        p.search(stripped) for p in _po.NONDETERMINISTIC_PATTERNS
    )


def _survives_negation_guard(expected: str, stripped: str) -> bool:
    # The 2026-08-16 rules, and ONLY those, additionally discount a
    # disjunction that sits inside a negative scope: in "no X or Y" both
    # disjuncts must hold, so it asserts one outcome. The seven rules above
    # are deliberately NOT run through this -- one of them is anchored on a
    # negation, and the guard would delete the detection it exists for.
    if not any(p.search(expected) for p in _DISJUNCTION_PATTERNS):
        return False
    neutral = _NEGATED_DISJUNCTION_RE.sub(" ", stripped)
    return any(p.search(neutral) for p in _DISJUNCTION_PATTERNS)


def _is_nondeterministic(expected: str | None) -> bool:
    if not expected:
        return False
    try:
        # A single equivalent-rendering alternation is the only "or" present ->
        # one outcome, two wordings. Not a disjunctive oracle.
        stripped = _EQUIVALENT_RENDERING_RE.sub(" ", expected)
        if _is_disjunctive(expected, stripped):
            return True
        # Escape-hatch phrasing (2026-09-26, v1.98 scope C2): independent of
        # the disjunction machinery below -- neither idiom needs an "or" at
        # all ("field updated, if applicable" has none).
        if any(p.search(expected) for p in _ESCAPE_HATCH_PATTERNS):
            return True
        return _survives_negation_guard(expected, stripped)
    except TypeError:
        return False


def find_nondeterministic_oracles(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """(tc_id, step_number, expected_result) for every unfalsifiable oracle.

    A step is reported when its expected result accepts two opposite outcomes,
    or defers to "record whatever happens" -- in both cases the step cannot
    fail, so it cannot detect a defect. Never raises.
    """
    try:
        return [
            (tc.tc_id, step.step_number, step.expected_result)
            for tc in cases
            for step in (tc.steps or [])
            if _is_nondeterministic(step.expected_result)
        ]
    except Exception:
        logger.exception("find_nondeterministic_oracles failed - returning empty list")
        return []


# --- Conditional actions ----------------------------------------------------

# An ACTION whose performance depends on what the build happens to show. This is
# a DIFFERENT defect from a soft oracle: there the tester runs the step and
# cannot fail it, here the tester cannot tell whether to run the step at all, so
# a Pass and a silent skip are indistinguishable in the report.
#
# TWO things control this, and the "if" is neither of them:
#
# 1. the ADJECTIVE VOCABULARY -- every listed word describes what the BUILD
#    exposes, so "Tap Freeze if the toggle is enabled" fires on purpose (same
#    defect, same remedy) while "Tap Save if the form is complete" stays quiet:
#    "complete" is a state the tester can establish. Add a word here only if a
#    tester could NOT establish it before running the step.
# 2. CLAUSE POSITION -- the adjective must END its clause. Without this the
#    pattern reads "if <adjective> <noun>" as a predicate and fires on "if
#    visible latency is observed", "if available balance is zero", "if the
#    endpoint is supported by this API version" -- ordinary steps whose
#    condition is about the DATA, not about whether the step can be run at all.
#
#    A locative exemption was tried and WITHDRAWN. Allowing "if present IN the
#    audit log" re-opened the predicate class twice: a bare "in|on|for"
#    terminator let every one of those three strings back in with one inserted
#    preposition ("if visible ON THE ROW latency is observed"), and requiring
#    the locative to close the clause only moved the hole to any SHORT tail
#    ("if available in SAR balance is zero."). Distinguishing the two needs a
#    finite-verb test, which is not a regex's job. The known cost is recorded
#    in test_locative_conditional_is_a_known_miss: "Note the result if present
#    in the audit log" is NOT reported. Zero of the run's 159 actions use that
#    phrasing, and a false positive here costs more than this miss.
#
#    The clause end is PUNCTUATION or the end of the string, and nothing else.
#    A word terminator re-admits the predicate reading through the very gap the
#    locative exemption was withdrawn for: "Check if available AND current
#    balance is zero, then tap Save" reads "available" as a predicate over
#    "current balance", and "and"/"or"/"then" were each one inserted word away
#    from a pinned negative. Round 4, 6, 7 and 8 each closed one instance of
#    this class; this closes the class. Its cost is one phrasing, pinned in
#    test_a_conjunction_tail_conditional_is_a_known_miss.
_CONDITIONAL_ACTION_RE = re.compile(
    r"\bif\s+(?:it\s+is\s+|its\s+|the\s+\w+\s+is\s+)?"
    r"(?:visible|available|exposed|present|shown|enabled|applicable|offered|"
    r"supported|exists)\b(?=\s*(?:[,.;)]|$))",
    re.IGNORECASE,
)


def find_conditional_actions(cases: list[TestCase]) -> list[tuple[str, int, str]]:
    """(tc_id, step_number, action) for every step whose ACTION is conditional.

    :func:`find_nondeterministic_oracles` reads only ``expected_result``, so a
    step reading "Tap 'Card controls' if visible" passed every gate in the tree
    while being unrunnable as written. Never raises.
    """
    try:
        return [
            (tc.tc_id, step.step_number, step.action)
            for tc in cases
            for step in (tc.steps or [])
            if step.action and _CONDITIONAL_ACTION_RE.search(step.action)
        ]
    except Exception:
        logger.exception("find_conditional_actions failed - returning empty list")
        return []
