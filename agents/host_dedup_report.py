"""The bounded duplicate-review block prepended to the submit reply."""

from __future__ import annotations

from agents.host_dedup_consts import _UNTRUSTED_ERRORS, logger
from agents.host_dedup_lines import (
    DupReviewOutcome,
    DupSuites,
    _dup_notes_lines,
    _dup_review_lines,
    _member_renderer,
)


def _resolve_outcome(
    outcome: DupReviewOutcome | None, fields: dict
) -> DupReviewOutcome:
    """The outcome object, built from the keywords ``removed``/``applied``/``notes``/
    ``agreements`` when none was passed (an unknown keyword is a TypeError, as it
    was for the old keyword-only signature)."""
    if outcome is None:
        return DupReviewOutcome(**fields)
    if fields:
        raise TypeError("pass either outcome or keyword fields, not both")
    return outcome


def _normalize(groups: list, outcome: DupReviewOutcome) -> tuple:
    """``(groups, notes, removed, agreements)`` as lists, each None or empty becoming ``[]``."""
    return tuple(
        list(seq or [])
        for seq in (groups, outcome.notes, outcome.removed, outcome.agreements)
    )


def _duplicate_lines(view: tuple, outcome: DupReviewOutcome, suites: tuple) -> str:
    """The section text for a non-empty normalized ``view``; ``suites`` is ``(submitted, final)``."""
    groups, notes, removed, agreements = view
    lines: list = []
    if groups or removed:
        render = _member_renderer(suites[0], suites[1], removed)
        lines += _dup_review_lines(
            groups, (removed, outcome.applied, agreements), render
        )
    if notes:
        lines += _dup_notes_lines(notes)
    return "\n".join(lines) + "\n"


# The bounded, deterministic "duplicate review" block prepended to the submit
# reply, AHEAD of the variable-length generated summary (the same ordering rule
# that moved ``quality_section`` in front of ``checklist_section``).
#
# Each submitted tc_id is resolved to the tc_id the tester will actually SEE:
# ``_finalize_generation`` renumbers ids, so the mapping goes through the case's
# content ``stable_id``, which survives both dedup and the renumber (the renumber
# uses ``model_copy``, which does not re-derive it). A member that is not in the
# final suite is NAMED as such instead of being silently dropped.
#
# ``agreements[i]`` is the ADVISORY server-measured text agreement for
# ``groups[i]``; a group below ``_low_text_ratio()`` is LABELLED so the tester can
# see which grouping the text does not support. It is a label, never a veto -- see
# ``dup_agreements`` for why.
#
# Bounded by CHARACTERS (``_MAX_DUP_SECTION_CHARS``), not by group count, so the
# section cannot grow to tens of KB. Truncation never hides a deletion: whenever
# the group list is cut AND cases were removed, every removed tc_id is listed
# (itself bounded by the proportional cap). Pure and synchronous. Never raises.
def build_duplicate_section(
    groups: list,
    suites: DupSuites,
    outcome: DupReviewOutcome | None = None,
    **fields: object,
) -> str:
    """The bounded, deterministic "duplicate review" block prepended to the submit reply."""
    outcome = _resolve_outcome(outcome, fields)
    try:
        view = _normalize(groups, outcome)
        if not view[0] and not view[1] and not view[2]:
            return ""
        return _duplicate_lines(view, outcome, suites)
    except _UNTRUSTED_ERRORS:
        logger.warning(
            "build_duplicate_section failed -- omitting the section", exc_info=True
        )
        return ""
