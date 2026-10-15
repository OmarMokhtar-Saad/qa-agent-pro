"""Replay miss and ambiguity messages: the words the model reads when a target does not resolve."""

from __future__ import annotations

from typing import Any

#: Why a target did not resolve, in the words the model reads on its next turn.
#: Constants so a test asserts this module's own text instead of a copy of it,
#: and so the three cases stay distinguishable in a trace a tester reads.
MISS_PLAIN = (
    "No element on this screen matched that target, so the replay stopped here "
    "rather than tapping something else."
)
#: The LONE-selector case, and the one a model meets most often: a target
#: carrying only an `id`, after a `type` moved every element on the screen.
#: It keeps the REMEDY, which is the whole value of the message -- a generic
#: "a selector did not resolve" leaves the model to re-plan by `id` again and
#: burn both free stops and then real escapes.
MISS_STALE_ID = (
    "The `id` in that target is not on this screen. An id describes an element "
    "as it was on the screen you planned from, and this screen has changed, so "
    "nothing was tapped. Re-plan from the screen below, and prefer `rid` or the "
    "exact on-screen text -- those survive a re-layout, an id does not."
)

#: EVERY selector missed, and more than one was supplied. Its remedy cannot be
#: "prefer `rid` or the text" -- those are what just failed. Measured: three of
#: the four shapes that used to reach MISS_STALE_ID supplied a rid and/or a text
#: that also matched nothing, so the message recommended the selectors the model
#: had just tried, and the model re-planned the same way and spent its free
#: stops. Nothing it said was false, which is why only a reachability predicate
#: tight enough to separate the shapes could catch it.
MISS_STALE_ALL = (
    "None of that target's selectors matches anything on this screen, so the "
    "plan was built on a screen that has since changed and nothing was tapped. "
    "Do not retry the same selectors. Re-plan from the screen below: take the "
    "`rid` or the exact text of the control you want from THIS element list."
)

#: The CROSS-SELECTOR case: two or more selectors were supplied and at least one
#: of them resolved. Only reachable then, which
#: ``test_every_miss_message_is_reachable_and_accurate`` asserts against the
#: 63-target enumeration -- because this wording shipped on the lone-id path,
#: telling a model that two selectors disagreed when it had sent one.
MISS_STALE_SELECTOR = (
    "One selector in that target matches nothing on this screen while another "
    "one does, so the plan was built on a screen that has since changed and "
    "nothing was tapped. Re-plan from the screen below. If the stale one was "
    "the `id`, prefer `rid` or the exact on-screen text -- those survive a "
    "re-layout, an id does not."
)
MISS_CONFLICT = (
    "That target's selectors point at DIFFERENT elements on this screen, which "
    "means the plan was made against an older one. Nothing was tapped and none "
    "of them was chosen. Re-plan from the screen below."
)

#: Why a targeted swipe was handed back rather than collapsed into a press.
TOO_SMALL_TO_SWIPE = (
    "That element is too small to swipe on: a swipe confined to its visible "
    "part would not move far enough to be a swipe, and a gesture that does not "
    "move is a tap. Nothing was touched. Scroll without a target, or name the "
    "scrollable list that contains it."
)


#: The AMBIGUOUS-ROLE message. A formatter rather than a constant because the
#: count is the useful part of it, and ``test_every_miss_message_is_reachable``
#: enumerates it alongside the constants so it cannot become unreachable
#: without saying so.
AMBIGUOUS_ROLE_PREFIX = "That role matched "


# Their message, kept verbatim: the role was SEEN and not narrowed.
#
# ``count`` is coerced rather than trusted. It used to be ``str(int(count))``,
# and this module's contract is that it never raises -- a fuzz over 20 inputs
# raised on 18 of them. No caller can reach that today (the only one passes
# ``resolution["candidates"]``, already an int), so this changes no shipped
# message; it removes a way for a future caller to turn a helpful refusal
# into an unhandled exception halfway through a replay.
#
# An unusable count degrades to the word rather than to a NUMBER: "matched 0
# tappable controls" would be a false statement, and the one thing actually
# known when the count cannot be read is that there was more than one.
def ambiguous_role_detail(count: Any) -> str:
    """Their message, kept verbatim: the role was SEEN and not narrowed."""
    try:
        shown = str(int(count))
    except (TypeError, ValueError, OverflowError):
        # OverflowError is the one this batch's own fuzz caught, in the FIX
        # rather than in the original: `int(float("inf"))` raises it, and it
        # is neither a TypeError nor a ValueError, so a two-exception guard
        # written for a never-raise contract still raised. `nan` goes to
        # ValueError and was already covered, which is exactly why reading
        # the guard was not enough to judge it.
        shown = "several"
    return (
        AMBIGUOUS_ROLE_PREFIX
        + shown
        + " tappable controls on this screen, so the replay stopped rather "
        "than guessing which you meant. Name one of them by its label, its "
        "rid or its short id."
    )


#: Appended for a `type` that missed: its field is found by a name, not an id.
TYPE_HINT = (
    " For `type`: name the field by its visible label or hint (or use `fill` "
    "with that label); with exactly one empty input field on screen the "
    "replay types into it without a name."
)


# Why a target resolved to nothing, in the model's own terms.
#
# Five answers, because they need five different next moves, and a model told
# only "no element matched" re-plans the same way it planned the first time:
#
# * an AMBIGUOUS role -- the role was SEEN and could not be narrowed, so the
#   advice is to name one of them, not to look for something absent;
# * selectors that name DIFFERENT elements -- a stale plan, and picking
#   either would be guessing which half to trust;
# * a selector that matched nothing while another matched -- also a stale
#   plan, and the remedy names what to switch to;
# * an ``id`` alone that matched nothing -- the commonest miss there is, and
#   the one whose remedy (`rid`/text) is worth stating;
# * nothing matched at all -- the planner named something that is not here.
#
# The ambiguity branch comes FIRST: a role that matched two tappable controls
# has been seen, and saying "nothing matched" about it would be false.
def missing_element_detail(resolved: Any) -> str:
    """Why a target resolved to nothing, in the model's own terms."""
    body = resolved if isinstance(resolved, dict) else {}
    detail = _missing_reason(body)
    return detail + TYPE_HINT if body.get("op") == "type" else detail


def _missing_reason(body: dict) -> str:
    candidates = _candidates(body)
    if candidates > 1:
        return ambiguous_role_detail(candidates)
    if body.get("conflict"):
        return MISS_CONFLICT
    if body.get("stale"):
        return _stale_detail(body)
    return MISS_PLAIN


def _candidates(body: dict) -> int:
    try:
        return int(body.get("candidates") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _stale_detail(body: dict) -> str:
    # WHICH message depends on whether anything still matched, not on how
    # many selectors were supplied. The cross-selector wording says "one
    # selector matches nothing while another one does" -- true only when
    # something did.
    supplied = tuple(body.get("supplied") or ())
    stale_selectors = tuple(body.get("stale_selectors") or ())
    if len(supplied) > len(stale_selectors):
        return MISS_STALE_SELECTOR
    # Nothing matched. The id-specific remedy ("prefer `rid` or the exact
    # on-screen text") is only USEFUL when the id was the only selector
    # supplied; where a rid or a text missed too, it names what just failed.
    if len(supplied) == 1:
        return MISS_STALE_ID
    return MISS_STALE_ALL
