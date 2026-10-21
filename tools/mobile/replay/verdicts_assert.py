"""Replay engine: assert verdicts. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile import actions as actions_mod
from tools.mobile.replay.elements import missing_element_detail
from tools.mobile.replay.screen import (
    _is_furniture,
    _screen_has,
    _screen_id,
    _texts,
    _typed_by_script,
)
from tools.mobile.replay.verdicts_wait_evidence import change_evidence, wait_verdict


class AssertView(NamedTuple):
    """The screen, trace and (for ``new_text``) the baseline content one assert reads."""

    screen: Any
    trace: list[dict]
    baseline: Any = None


def _assert_text(kind: str, text: str, screen: Any) -> tuple[bool, str]:
    """``assert text_present`` / ``text_absent``."""
    found = _screen_has(screen, text)
    if kind == "text_present":
        return found, ("found " if found else "NOT found on this screen: ") + repr(
            text[:120]
        )
    return (not found), (
        "still on this screen: " if found else "absent, as expected: "
    ) + repr(text[:120])


# One assert against the current screen.
#
# *view.baseline* is the content set of the screen as it was before the last
# screen-changing action, and only ``new_text`` reads it.
#
# ``screen_changed`` is deliberately kept and deliberately WEAK: it compares
# ids, so any navigation satisfies it. It is not evidence that an app
# answered, and ``new_text`` exists because a live run read it as if it were.
def _evaluate_assert(action: Any, view: AssertView) -> tuple[bool, str]:
    """One assert against the current screen."""
    kind = str(getattr(action, "kind", "") or "")
    text = str(getattr(action, "text", "") or "")
    if kind in ("text_present", "text_absent"):
        return _assert_text(kind, text, view.screen)
    if kind == "element":
        return _assert_element(action, view.screen)
    if kind == "new_text":
        return _assert_new_text(action, view)
    return _assert_screen_changed(view.screen, view.trace)


def _assert_element(action: Any, screen: Any) -> tuple[bool, str]:
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


# The strings on *screen* that count as a reply since *baseline*.
#
# TWO things are subtracted before anything counts as a reply, and both
# came from a review that ran the code rather than read it.
#
# 1. What this script TYPED. A chat composer echoes your question into
#    the thread, so `[type, tap send, assert new_text]` passed on an app
#    that never answered -- on the most ordinary screen there is.
# 2. Furniture. A clock going 10:42 -> 10:43, or a countdown that shows up
#    after the tap, is a new string and is not new content. A string with
#    no letter in it is something that ticks, so it is dropped whether or
#    not the baseline held one.
def _new_text_added(screen: Any, trace: list[dict], baseline: Any) -> list:
    """The strings on *screen* that count as a reply since *baseline*."""
    added = sorted(_texts(screen) - set(baseline))
    typed = _typed_by_script(trace)
    added = [item for item in added if item not in typed]
    # Furniture is never a reply, baseline or not: a countdown that appears
    # AFTER the tap ('02:59' on an OTP screen) is a clock, not an answer.
    return [item for item in added if not _is_furniture(item)]


def _new_text_reply(added: list) -> str:
    """The pass detail naming the first new reply."""
    return (
        "a new reply appeared: "
        + repr(added[0][:160])
        + ((" (and " + str(len(added) - 1) + " more)") if len(added) > 1 else "")
    )


def _assert_new_text(action: Any, view: AssertView) -> tuple[bool, str]:
    """``assert new_text``: a reply appeared since *baseline*."""
    if view.baseline is None:
        return False, (
            "no earlier screen to compare against: nothing in this script "
            "has changed the screen yet, so there is no 'before' to diff"
        )
    contains = " ".join(str(getattr(action, "contains", "") or "").split())
    added = _new_text_added(view.screen, view.trace, view.baseline)
    if contains:
        added = [item for item in added if contains.lower() in item.lower()]
    if added:
        return True, _new_text_reply(added)
    return False, (
        "no new text appeared on this screen since the last action"
        + ((" containing " + repr(contains[:60])) if contains else "")
    )


def _id_verdict(current: str, trace: list[dict]) -> tuple[bool, str]:
    """The id comparison, with real change evidence when the ids are equal."""
    seen = [e.get("before_screen_id") for e in trace if e.get("before_screen_id")]
    if not seen:
        return False, "no earlier screen to compare against"
    if current != seen[0]:
        return True, "the screen changed since the first action"
    proof = change_evidence(trace)
    return bool(proof), proof or "the screen did not change since the first action"


def _assert_screen_changed(screen: Any, trace: list[dict]) -> tuple[bool, str]:
    """``assert screen_changed`` (weak: compares ids, see ``_evaluate_assert``)."""
    waited = wait_verdict(trace)
    if waited is not None:
        return waited
    return _id_verdict(_screen_id(screen), trace)
