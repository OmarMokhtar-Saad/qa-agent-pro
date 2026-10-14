"""Replay engine: the element a step acts on. Cut verbatim from tools/mobile/replay/steps.py (C5 w17)."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile import actions as actions_mod
from tools.mobile.replay.context import STATUS_NEEDS_MODEL, _ReplayRun, _result
from tools.mobile.replay.elements import missing_element_detail
from tools.mobile.replay.steps_stop import StepAt, _close, _reply, _stop
from tools.mobile.replay.steps_text import _fill_element, _tap_text_element
from tools.mobile.replay.verdicts import _actuated


class Aim(NamedTuple):
    """What a step aims at: its op and the ref (the action, or the target)."""

    op: str
    ref: Any


# Read by ``case_runner``: a stop caused by OUR OWN selector going stale,
# before anything touched the device, is not the tester's case failing to
# make progress -- see MAX_FREE_STOPS there.
#
# Intersected with ``actions.OURS`` rather than taken from the bare ``stale``
# flag. Measured: ``{"rid": <gone>, "text": <matches>}`` -- no `id` anywhere
# -- reported stale and was UNCHARGED, while OURS' own docstring said a rid
# or text naming something absent is charged like any other boomerang. A
# conflict is charged too: two selectors the MODEL chose disagreeing is a
# stale plan, not our bookkeeping.
def _selector_stale(resolution: dict) -> bool:
    """Whether a stop was caused by OUR OWN selector going stale (see above)."""
    return any(
        name in actions_mod.OURS for name in (resolution.get("stale_selectors") or ())
    )


def _missing_stop(run: _ReplayRun, at: StepAt, resolution: dict) -> dict:
    """The reply that stops on an element the target names but the screen lacks."""
    at.entry.update(
        outcome="missing_element", detail=missing_element_detail(resolution)
    )
    _close(run, at)
    stop = _stop(run, at, STATUS_NEEDS_MODEL)
    stale = _selector_stale(resolution)
    return _reply(_result(stop, selector_stale=stale, actuated=_actuated(run.trace)))


# ``assert`` is exempt for the same reason ``scroll`` is, and it
# matters more: a missing element IS the answer an
# ``assert kind="element"`` was asked for. Letting the generic
# boomerang intercept it meant that assert could never FAIL --
# the one verdict it exists to produce. It returned
# ``needs_model`` instead, so a tester who required an element
# that was absent got a re-plan, three spent escapes and a
# ``blocked`` case rather than the failure they asked for.
# ``_evaluate_assert`` decides presence itself.
# The DEGRADATION below is deliberate and stays; what changes
# is that it is now visible. Recorded before the branch so the
# flag cannot drift from the condition that produces it.
def _target_element(
    run: _ReplayRun, aim: Aim, at: StepAt
) -> tuple[Any, bool, dict | None]:
    """The element a step's ``target`` names, whether a ``scroll`` missed it,
    and the reply that stops on a missing element (``None`` to go on)."""
    resolved = actions_mod.resolve_target(aim.ref, at.screen)
    resolution = (resolved or {}).get("content") or {}
    element = resolution.get("element")
    scroll_missed = element is None and aim.op == "scroll"
    if element is None and aim.op not in ("scroll", "assert"):
        return element, scroll_missed, _missing_stop(run, at, resolution)
    return element, scroll_missed, None


# Per ACTION, not per replay: a scroll whose target missed must not
# colour the note of a later scroll that resolved.
# ``tap_text`` is resolved HERE, into the same `element` every other op uses, so
# the destructive guard judges `actuated_element` for this op exactly as it does
# for a `tap`. An op that resolved its own element AFTER the guard would be a
# bypass, which is the defect class `NON_ACTUATING_OPS` exists to make explicit.
# ``fill`` is resolved HERE, for the reason `tap_text` is: the destructive
# guard must judge the field a fill will touch. The refusal names the label and,
# when ambiguous, bounded candidates -- never a field's contents.
def _resolve_element(
    run: _ReplayRun, aim: Aim, at: StepAt
) -> tuple[Any, bool, dict | None]:
    """The element a step acts on, whether a ``scroll`` missed its target, and
    the reply that stops the replay on a miss (``None`` to go on)."""
    target = getattr(aim.ref, "target", None)
    element = None
    if aim.op == "tap_text":
        element, reply = _tap_text_element(run, aim.ref, at)
        if reply is not None:
            return element, False, reply
    if aim.op == "fill":
        element, reply = _fill_element(run, aim.ref, at)
        if reply is not None:
            return element, False, reply
    if target is not None:
        return _target_element(run, Aim(aim.op, target), at)
    return element, False, None
