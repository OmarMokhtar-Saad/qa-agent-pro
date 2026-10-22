"""Replay: re-check a failed assert on a screen that may still be settling (H06, D-04).

Measured (``mrun-20261021-163646-1b4628`` TC-009): ``wait_until_changed`` passed
on a small change, the next ``assert text_absent`` failed "still on this screen",
and the target screen arrived a moment later. Again in ``mrun-20261021-224708``
TC-017: ``wait_until_changed`` -> ``wait_until_idle`` -> ``assert element`` failed
"not on this screen" on what was most likely still the half-drawn frame the idle
step had read. A failed assert on a SUSPECT screen (a degraded or blank read, or
right after a wait, tap, type or scroll) polls (probe frames, never a sleep)
until the assert holds or ``SETTLE_MS`` passes, then judges once more. A stable,
fully keyed screen after a non-transition step fails at once.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import actions as actions_mod
from tools.mobile.replay.context import _ReplayRun, _Step
from tools.mobile.replay.screen_has import screen_readable
from tools.mobile.replay.steps_stop import StepAt
from tools.mobile.replay.verdicts_assert import AssertView, _evaluate_assert
from tools.mobile.replay.waits_poll import _wait_for
from tools.mobile.replay.waits_types import Probe
from tools.mobile.screen_key import screen_key

#: How long the assert may wait to hold before it is judged again.
SETTLE_MS = 3000
#: How many earlier non-assert steps are looked at for a screen transition.
LOOKBACK = 2
#: Ops that move or redraw the screen (a prefix match: ``wait_*``, ``tap_*``...).
_TRANSITION_PREFIXES = ("wait", "tap", "scroll", "swipe", "type", "fill")
SUFFIX_MAX = 120
_IDS_SHOWN = 3


def _after_transition(trace: list, index: int) -> bool:
    """True when one of the last steps before *index* (asserts skipped) redrew the screen."""
    earlier = [
        (entry.get("action") or {}).get("op") or ""
        for entry in trace
        if entry.get("index") != index
    ]
    recent = [op for op in earlier if op != "assert"][-LOOKBACK:]
    return any(str(op).startswith(_TRANSITION_PREFIXES) for op in recent)


def suspect(trace: list, index: int, screen: Any) -> bool:
    """Whether the screen an assert read may still be mid-transition."""
    if not screen_readable(screen) or screen_key(screen).degraded:
        return True
    return _after_transition(trace, index)


def shown_suffix(screen: Any) -> str:
    """``"; the screen showed N elements, first ids: a, b"``: resource ids only."""
    rows = screen.get("elements") if isinstance(screen, dict) else None
    rows = [r for r in rows or [] if isinstance(r, dict)]
    ids = [str(r["rid"]) for r in rows if r.get("rid")][:_IDS_SHOWN]
    head = f"; the screen showed {len(rows)} elements, "
    tail = "first ids: " + ", ".join(ids) if ids else "none with an id"
    return (head + tail)[:SUFFIX_MAX]


async def polled_screen(run: _ReplayRun, screen: Any, probe: Probe) -> Any:
    """*screen* once *probe* is met, else the latest screen read (or *screen*)."""
    _found, latest, _timed_out = await _wait_for(run.ctx, screen, probe)
    run.ctx.wait_interruption = ""
    return latest if isinstance(latest, dict) else screen


def _probe_for(step: _Step, view: AssertView) -> Probe:
    """The watched condition: the assert itself holds on the screen read."""

    def met(_previous: Any, current: Any) -> bool:
        return _evaluate_assert(step.action, view._replace(screen=current))[0]

    return Probe(SETTLE_MS, met, "waiting for the assert to hold", False)


def unreadable_target(step: _Step, screen: Any) -> bool:
    """True when an element assert failed because its target could not be READ.

    A conflict or stale target is a deterministic answer about the plan, not a
    frame that is still drawing, so it is neither polled nor given a suffix."""
    if str(getattr(step.action, "kind", "") or "") != "element":
        return False
    resolved = actions_mod.resolve_target(getattr(step.action, "target", None), screen)
    body = (resolved or {}).get("content") or {}
    return body.get("element") is None and bool(
        body.get("stale") or body.get("conflict")
    )


async def recheck_after_wait(
    run: _ReplayRun, step: _Step, seen: tuple[AssertView, StepAt]
) -> tuple[bool, str, StepAt]:
    """Judge a failed assert again, on a later screen when the first read is suspect."""
    view, at = seen
    if unreadable_target(step, view.screen) or not suspect(
        run.trace, step.entry["index"], view.screen
    ):
        ok, detail = _evaluate_assert(step.action, view)
        return ok, detail, at
    late = await polled_screen(run, view.screen, _probe_for(step, view))
    ok, detail = _evaluate_assert(step.action, view._replace(screen=late))
    return ok, detail if ok else detail + shown_suffix(late), at._replace(screen=late)
