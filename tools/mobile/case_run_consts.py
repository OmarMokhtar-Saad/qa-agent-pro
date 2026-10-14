"""Constants and the one logger of the case runner."""

from __future__ import annotations

import logging

logger = logging.getLogger("tools.mobile.case_runner")

MAX_ESCAPES = 3
#: The whole case's trace, across every submit. It is capped because a case may
#: be handed back many times -- three escapes plus eight budget stops -- and an
#: unbounded list would grow on disk and in the report.
MAX_CASE_TRACE = 240
#: How many times ONE case may be stopped by the submit budget before it is
#: ended anyway. A budget stop is not an escape -- the script was legal and the
#: model is being asked to continue rather than to re-plan -- but exempting it
#: outright removed the ONLY bound on the model/server loop for a case: nothing
#: else caps submits, so a model re-sending the same over-budget script never
#: terminates and the run never finishes. Cheap, then, but not free.
MAX_BUDGET_STOPS = 8

BUDGET_CAP_REASON = (
    "This case was stopped by the per-submit time budget "
    + str(MAX_BUDGET_STOPS)
    + " times without finishing, so it is recorded as blocked rather than "
    "continued again. Each script it was given needed more device time than "
    "one submit allows; the trace shows how far it got."
)

NEEDS_MODEL = "needs_model"
NEEDS_TESTER = "needs_tester"


#: Boomerangs that may be spent WITHOUT charging an escape, per case, when the
#: stop was caused by one of our own selectors going stale and nothing had
#: touched the device yet.
#:
#: The precedent is this module's own, one branch below: "A refused script is
#: NOT an escape: nothing was replayed, so the planner gets the same screen
#: back and one of its escapes is not spent on our own validation." A stale id
#: is the same category of stop -- ``perception`` mints the id, and the app
#: moving the element is not the tester's case failing.
#:
#: It is CAPPED rather than free, because an uncapped uncharged stop is a loop
#: with the tester's tokens in it. After this many, a stale-id stop is charged
#: like any other boomerang, so ``MAX_ESCAPES`` still terminates the case.
#:
#: It does NOT cover the post-``type`` case, and that is measured rather than
#: assumed: a ``type`` actuates the device, so a stale-id stop after one has
#: ``actuated=True`` and IS charged. The answer there is the packet's own
#: instruction to target by ``rid``/text after a ``type`` -- see
#: ``tests/mobile/test_mobile_escape_budget.py``, which measures both.
MAX_FREE_STOPS = 2

#: The two reasons a stop is not charged, and the cap on each. ONE counter with
#: a bound PER REASON: a single bound cannot be both "not laxer than 2 for a
#: stale selector" and "not stricter than 8 for a budget stop", and each number
#: has its own evidence behind it.
REASON_BUDGET = "budget"
REASON_SELECTOR = "selector"
#: A ``visual`` assert stop: the plan did what it said and asked for the
#: picture, so it is not an escape. Capped so a model cannot use it to loop.
REASON_VISUAL = "visual"
MAX_VISUAL_STOPS = 3
UNCHARGED_CAPS = {
    REASON_BUDGET: MAX_BUDGET_STOPS,
    REASON_SELECTOR: MAX_FREE_STOPS,
    REASON_VISUAL: MAX_VISUAL_STOPS,
}
ESCAPE_CAP_REASON = (
    "This case was handed back to the planner "
    + str(MAX_ESCAPES)
    + " times and still could not be completed, so it is recorded as blocked "
    "rather than retried again. The trace shows how far it got."
)
#: Ops that put text on the screen through the QA keyboard.
#:
#: `clear` is here with `type`: it is an IME broadcast like the others, and a
#: CLEAR sent to a keyboard that is not active is the same silence a TYPE would
#: be. Stated as a set rather than an `op == "type"` test so a fourth text op
#: added later is caught by the same reader.
TEXT_OPS = frozenset({"type", "fill", "clear"})
