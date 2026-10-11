"""Replay engine: budget, route and assert verdicts. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import logging

from tools.mobile import actions as actions_mod
from tools.mobile.replay.context import SUBMIT_BUDGET_S, Context
from tools.mobile.replay.elements import ACTUATING_OPS, missing_element_detail
from tools.mobile.replay.screen import (
    _digit_shape,
    _is_furniture,
    _screen_has,
    _screen_id,
    _screen_package,
    _texts,
    _typed_by_script,
)

logger = logging.getLogger("tools.mobile.executor")


def _budget_seconds(ctx: Context) -> float:
    """This replay's wall-clock bound, always positive.

    A zero or negative budget would stop the FIRST action, so the replay would
    make no progress and every submit would spend an escape for nothing. An
    unusable value falls back to the module default rather than deadlocking the
    case.
    """
    try:
        value = float(getattr(ctx, "budget_s", None) or SUBMIT_BUDGET_S)
    except (TypeError, ValueError, OverflowError):
        value = SUBMIT_BUDGET_S
    return value if value > 0 else SUBMIT_BUDGET_S


#: What the model is told when a TARGETED scroll's target did not resolve.
#: ``scroll`` is the one op whose target miss is not handed back -- panning is a
#: sensible default and is what a tester would do -- but the degradation was
#: SILENT: measured, a resolved scroll, a stale-target scroll and a target-less
#: pan all reported ``no_change`` with an empty detail, so the model could not
#: tell that the element it named was gone and planned its next turn as though
#: it had scrolled it. The stop itself is unchanged; only the record is.
#:
#: "DID NOT RESOLVE", never "was not on this screen". ``resolve_target``
#: answers None for a CONFLICT and for an ambiguous ROLE as well as for a plain
#: miss, and in both of those the elements ARE present -- which is why
#: :func:`missing_element_detail` puts its ambiguity branch first and says as
#: much. A note claiming absence would be false in two of the five cases.
#:
#: It carries no per-case remedy, and that is a length decision made against
#: two hard caps downstream: ``report.MAX_TEXT`` (200) truncates this string in
#: the HTML row, and ``mobile_run._trace_block`` caps it at 300 in the escape
#: packet -- the surface it exists for. The 285-character first draft was cut
#: mid-sentence by the first of them and left 15 characters for the device's
#: own detail. Unlike a boomerang the action ALREADY HAPPENED, and the reply
#: carries the new screen, so the remedy is the same whatever the miss was.
SCROLL_TARGET_MISSED = (
    "That target did not resolve, so the screen was panned instead of the "
    "element you named. Re-read the screen before planning from it."
)


def _route_budget_stop_reason(ran: int, remaining: int) -> str:
    """``budget_stop_reason`` for a saved route, which queues nothing."""
    return (
        "budget reached after "
        + str(int(ran))
        + " action(s) of this saved route; the remaining "
        + str(remaining)
        + " were NOT run and NOT queued, because a route step runs only "
        "after its screen is checked against the saved one: carry on from "
        "the screen below and send the steps still needed."
    )


def budget_stop_reason(
    ran: int, total: int, queued: int | None = None, route: bool = False
) -> str:
    """What the model is told when the wall clock ended a replay early.

    ``queued`` is how many of the remaining actions the queue really holds.
    ``_serialize_queued`` ends the queue at a literal typed at a credential
    field, so telling the model "do not resend" for the dropped tail would
    skip those steps silently: they are named and asked for again instead.
    ``route`` is a saved-route replay, which queues nothing: its remainder
    has not been checked against the saved screens, so it is asked for again.
    """
    remaining = max(0, int(total) - int(ran))
    limit = " One submit replays for at most " + str(int(SUBMIT_BUDGET_S)) + "s."
    if route:
        return _route_budget_stop_reason(ran, remaining) + limit
    kept = remaining if queued is None else max(0, min(int(queued), remaining))
    if kept == remaining:
        return (
            "budget reached after "
            + str(int(ran))
            + " action(s); the remaining "
            + str(remaining)
            + " action(s) are queued and will run FIRST on your next submit -- "
            "you do not need to resend them." + limit
        )
    first_dropped = int(ran) + kept + 1
    return (
        "budget reached after "
        + str(int(ran))
        + " action(s); of the remaining "
        + str(remaining)
        + ", "
        + str(kept)
        + " are queued and will run FIRST on your next submit -- do not resend "
        "those. Actions "
        + str(first_dropped)
        + " to "
        + str(int(total))
        + " of this script were NOT queued, because action "
        + str(first_dropped)
        + " types a value at a credential field and that value is never stored: "
        "send them again, with the value, in your next submit." + limit
    )


def _serialize_queued(pending: object) -> list[dict]:
    """Unreached actions from a budget-stopped replay, as plain dicts ready to
    run again on a later submit -- the SERVER-SIDE queue this defect exists to
    carry, instead of asking the model to retype a shorter script.

    A queued ``type`` action never carries a secret literal past this call:
    the FIELD NAME survives (so the tester supplies it again on the submit
    that finally runs it, exactly like a fresh secret field); the VALUE does
    not.

    A NON-secret action that types a literal at a credential-named field ends
    the queue: it and everything after it are dropped, and the model issues
    them again on the next submit. Blanking the literal instead would queue a
    step that cannot replay (``type`` needs text), and keeping it would write
    a credential in clear to the run folder and the reply.
    """
    out: list[dict] = []
    for action in list(pending or []):
        try:
            payload = action.model_dump(mode="json")
        except (AttributeError, TypeError, ValueError):  # pragma: no cover
            continue
        if payload.get("secret"):
            payload["text"] = ""
        elif payload.get("text") and actions_mod.is_credential_action(action):
            break
        out.append(payload)
    return out


ROUTE_MISMATCH_DETAIL = (
    "This screen is not the one this saved route was recorded from, so nothing was tapped "
    "for this step. Carry on from the packet."
)


def _route_expect_sentinel() -> str:
    return "<route-length-mismatch>"


def _route_expected(ctx: object, index: int, total: int) -> str:
    """The screen_id step *index* of *total* must start on, or ``""`` when it is unchecked.

    Aligned FROM THE END: a budget-stop queue is prepended to a script by case_runner, and the
    saved route is always the tail. A route longer than the script can never match, so it
    returns a sentinel no screen_id equals.
    """
    expect = getattr(ctx, "route_expect", None) or ()
    offset = total - len(expect)
    if offset < 0:
        return _route_expect_sentinel()
    if index < offset:
        return ""
    return str(expect[index - offset] or "")


def _actuated(trace: object) -> bool:
    """Whether anything in THIS submit has already touched the device.

    The distinction the escape budget needs. A stop AFTER a tap or a type is a
    case that made progress and was handed back -- exactly what an escape is
    for. A stop BEFORE any of them, caused by one of our own selectors having
    gone stale, is bookkeeping, and ``case_runner`` declines to charge for it.

    Reads :data:`ACTUATING_OPS` rather than ``actions.MUTATING_OPS``, and the
    difference is one op that matters: ``wait`` is in MUTATING_OPS because a
    wait exists precisely to let the screen change, but it touches nothing. A
    script of ``[wait, tap(stale id)]`` was therefore charged an escape for a
    stop that actuated NOTHING -- a third of ``MAX_ESCAPES`` spent on the exact
    case ``MAX_FREE_STOPS`` was added to protect, while this docstring claimed
    the opposite.
    """
    # `list(trace)` RAISES on a non-iterable -- `_actuated(5)` was a TypeError,
    # against this module's never-raise contract. The coercion is the same
    # shape every other reader here uses: anything that is not a sequence of
    # entries is no entries. Unreachable from today's callers, which pass the
    # trace list they just built.
    entries = trace if isinstance(trace, (list, tuple)) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("outcome") or "") not in ("ok", "no_change"):
            continue
        action = entry.get("action")
        action = action if isinstance(action, dict) else {}
        if str(action.get("op") or "") in ACTUATING_OPS:
            return True
    return False


# The note is capped at 300 by `AssertAction`; this bounds the prefixed
# detail so a later cap change cannot grow the trace unseen.
MAX_VISUAL_DETAIL_CHARS = 320


def _evaluate_assert(
    action: object,
    screen: object,
    trace: list[dict],
    baseline: object = None,
) -> tuple[bool, str]:
    """One assert against the current screen.

    *baseline* is the content set of the screen as it was before the last
    screen-changing action, and only ``new_text`` reads it.

    ``screen_changed`` is deliberately kept and deliberately WEAK: it compares
    ids, so any navigation satisfies it. It is not evidence that an app
    answered, and ``new_text`` exists because a live run read it as if it were.
    """
    kind = str(getattr(action, "kind", "") or "")
    text = str(getattr(action, "text", "") or "")
    if kind == "text_present":
        found = _screen_has(screen, text)
        return found, ("found " if found else "NOT found on this screen: ") + repr(
            text[:120]
        )
    if kind == "text_absent":
        found = _screen_has(screen, text)
        return (not found), (
            "still on this screen: " if found else "absent, as expected: "
        ) + repr(text[:120])
    if kind == "element":
        return _assert_element(action, screen)
    if kind == "new_text":
        return _assert_new_text(action, screen, trace, baseline)
    return _assert_screen_changed(screen, trace)


def _assert_element(action: object, screen: object) -> tuple[bool, str]:
    """``assert element``: is the named target on this screen."""
    resolved = actions_mod.resolve_target(getattr(action, "target", None), screen)
    body = (resolved or {}).get("content") or {}
    element = body.get("element")
    if element is None and (body.get("stale") or body.get("conflict")):
        # "The element is absent" and "the target could not be read on this
        # screen" are different answers, and an assert must not quietly
        # turn the second into the first -- that would report the app as
        # wrong when the PLAN was stale.
        return False, missing_element_detail(body)
    return bool(element), (
        "the element is on this screen"
        if element
        else "the element is not on this screen"
    )


def _new_text_added(screen: object, trace: list[dict], baseline: object) -> list:
    """The strings on *screen* that count as a reply since *baseline*.

    TWO things are subtracted before anything counts as a reply, and both
    came from a review that ran the code rather than read it.

    1. What this script TYPED. A chat composer echoes your question into
       the thread, so `[type, tap send, assert new_text]` passed on an app
       that never answered -- on the most ordinary screen there is.
    2. Furniture that only CHANGED. A clock going 10:42 -> 10:43 is a new
       string and is not new content. Recognised without a threshold: an
       added string whose digits normalise onto a string that was there
       before is a mutation of what was already on screen.
    """
    added = sorted(_texts(screen) - set(baseline))
    typed = _typed_by_script(trace)
    added = [item for item in added if item not in typed]
    before_shapes = {
        _digit_shape(item) for item in set(baseline) if _is_furniture(item)
    }
    # ONE condition, not two. The bound belongs on the BASELINE side -- only
    # a string that was already furniture can be furniture that ticked -- and
    # a second copy of it on the added side decided nothing, which mutation
    # proved by deleting it and changing no result. A guard nothing can kill
    # is not a guard.
    return [item for item in added if _digit_shape(item) not in before_shapes]


def _assert_new_text(
    action: object, screen: object, trace: list[dict], baseline: object
) -> tuple[bool, str]:
    """``assert new_text``: a reply appeared since *baseline*."""
    if baseline is None:
        return False, (
            "no earlier screen to compare against: nothing in this script "
            "has changed the screen yet, so there is no 'before' to diff"
        )
    contains = " ".join(str(getattr(action, "contains", "") or "").split())
    added = _new_text_added(screen, trace, baseline)
    if contains:
        added = [item for item in added if contains.lower() in item.lower()]
    if added:
        return True, (
            "a new reply appeared: "
            + repr(added[0][:160])
            + ((" (and " + str(len(added) - 1) + " more)") if len(added) > 1 else "")
        )
    return False, (
        "no new text appeared on this screen since the last action"
        + ((" containing " + repr(contains[:60])) if contains else "")
    )


def _assert_screen_changed(screen: object, trace: list[dict]) -> tuple[bool, str]:
    """``assert screen_changed`` (weak: compares ids, see ``_evaluate_assert``)."""
    seen = [
        entry.get("before_screen_id")
        for entry in trace
        if entry.get("before_screen_id")
    ]
    current = _screen_id(screen)
    if not seen:
        return False, "no earlier screen to compare against"
    changed = current != seen[0]
    return changed, (
        "the screen changed since the first action"
        if changed
        else "the screen did not change since the first action"
    )


#: What ``am start`` prints when the activity was not started because its
#: task already existed and was only moved to the front.
BROUGHT_TO_FRONT = "brought to the front"


def _brought_to_front(
    outcome: object, screen: object, ctx: object, *, named: object = None
) -> bool:
    """True when a `launch` only moved THIS app's task to the front of a screen
    that was already this app, so the screen in hand is still current.

    The package check is what makes the skip safe: `am start` says the same
    words when the app was in the BACKGROUND, and then the screen in hand is
    the launcher and must be re-read. A launch that NAMED another package
    (an App Tester, say) never counts: the screen in hand is the target's,
    the app now in front is not, so the redump must run.
    """
    if named and str(named) != str(getattr(ctx, "package", "") or ""):
        return False
    body = outcome.get("content") if isinstance(outcome, dict) else None
    if not isinstance(body, dict):
        return False
    said = str(body.get("out") or "") + str(body.get("err") or "")
    package = str(getattr(ctx, "package", "") or "")
    return (
        bool(package)
        and BROUGHT_TO_FRONT in said
        and _screen_package(screen) == package
    )
