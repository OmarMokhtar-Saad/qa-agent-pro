"""Part of the quality-check split (see tools/quality_checks.py): findability."""

from __future__ import annotations

import logging

from tools.models import TestCase
from tools.quality_screens import (
    _first_step_is_off_app,
    _names_a_place,
    _names_a_screen,
)

logger = logging.getLogger("tools.quality_checks")

# The tester-facing report is a COUNT ONLY, and that is a budget decision, not
# a style one.
#
# WHAT BOUNDS THIS LINE. The reply is not bounded by one fixed number.
# handlers caps the WHOLE submit reply at _REPLY_CAP and gives
# `summary` -- the block this line lives in -- a COMPUTED allowance:
#
#   summary_budget(sections, header_len)
#       = max(_SUMMARY_FLOOR, min(_SUMMARY_CAP, _REPLY_CAP - overhead))
#       overhead = every other section + the header + _TRUNC_RESERVE
#
# so the allowance is clamped into [_SUMMARY_FLOOR, _SUMMARY_CAP] and is
# STRICTER than the old fixed ceiling whenever the reply carries notes -- never
# looser. tests/test_finalize_reply_cap.py still measures the summary against
# that fixed ceiling, which is the WORST case for this line, so its two
# fixtures remain the right yardstick.
#
# THE COUNT FITS AND THE IDS DO NOT. The id form costs 344 chars where this
# line costs 64, against a measured headroom of 325 (all-plans fixture) and 296
# (mixed). Each cost is the line PLUS the one newline the surrounding join also
# adds, which is why a 63-char line moves the total by 64. The line is 64 chars
# on the live run ("23 of 96") and 63 in the cap fixture ("1 of 96") -- its
# length tracks the DIGITS of the two numbers, 62 for "7 of 8" and 68 for
# "1234 of 5678". Nothing pins an exact length: the test bounds it at <= 80.
#
# NO EXIT CONDITION -- and that is a MEASUREMENT, not an omission. Earlier
# revisions carried one: "F03 is about that cap; when it lands, swap the count
# for the id form." F03 HAS landed, and it settles the question the other way
# -- it made the allowance computed and STRICTER, so the swap over-runs both
# fixtures rather than fitting. It becomes affordable only if the BASELINE
# drops by roughly 300 chars, not if the cap moves.
#
# THIS LINE SURVIVES TRUNCATION. `summary` is the block F03 makes yield, so
# "does a tight budget eat this line?" is a real question, and it is answered:
# the Data Quality Notes block is the FIRST section of the summary and this
# line sits about 13% into it. Truncation is a head slice, so the line is
# inside every allowance down to _SUMMARY_FLOOR -- it cannot be the thing that
# is lost. That cross-module claim is PINNED by
# test_the_budget_this_line_was_sized_against_still_works_that_way, because a
# comment cannot fail and this one already went stale once.
#
# The four measured reply forms, the exact byte offsets and the commit each
# figure was taken at are in the plan's section 2b, deliberately not here: a
# figure about another module's tree is the one thing a comment beside this
# constant cannot keep true.


FINDABILITY_MAX_LOGGED = 40


def _first_step_action(tc: TestCase) -> str:
    """The first step's action text, '' when the case has no usable first step."""
    steps = getattr(tc, "steps", None) or []
    if not steps:
        return ""
    return str(getattr(steps[0], "action", "") or "")


def _is_unanchored(tc: TestCase, action: str) -> bool:
    """True when neither the step nor the preconditions name a place, and it is on-app."""
    preconditions = str(getattr(tc, "preconditions", "") or "")
    if _names_a_place(action) or _names_a_screen(preconditions):
        return False
    return not _first_step_is_off_app(action)


# The case's PRECONDITIONS count as an anchor too -- a tester reads them before
# step 1, so "User is on the Card controls screen" makes the case findable
# however the step is phrased. They are held to the STRICTER test
# (_names_a_screen): preconditions are prose, and the step-level locative rule
# reads "logged in with push enabled" as a location. Anchored is checked BEFORE
# off-app on purpose: a step that names its screen is fine even when it also
# mentions a terminal; the other order exempts it for the wrong reason.
def find_unanchored_first_steps(cases: list[TestCase]) -> list[tuple[str, str]]:
    """Return (tc_id, action) for every case whose FIRST step names no place.

    Never raises.
    """
    try:
        out: list[tuple[str, str]] = []
        for tc in cases:
            action = _first_step_action(tc)
            if action.strip() and _is_unanchored(tc, action):
                out.append((str(getattr(tc, "tc_id", "") or ""), action))
        return out
    except Exception:
        logger.exception("find_unanchored_first_steps failed — returning empty list")
        return []


def _unanchored_lines(unanchored: list[tuple[str, str]], total_cases: int) -> list[str]:
    """The mid-screen bullet; logs the ids, empty when none are unanchored."""
    if not unanchored:
        return []
    # The ids go to the log, not the reply: see FINDABILITY_MAX_LOGGED.
    logger.info(
        "findability: %d of %d case(s) open mid-screen (first step names no screen): %s",
        len(unanchored),
        total_cases,
        ", ".join(tc_id for tc_id, _ in unanchored[:FINDABILITY_MAX_LOGGED]),
    )
    return [
        f"- {len(unanchored)} of {total_cases} case(s) open mid-screen "
        "(first step names no screen)."
    ]
