"""Replay engine: the shared stop/close helpers and the budget / route-mismatch / knowledge-key stamps. Cut verbatim from tools/mobile/replay/steps.py (C5 w17)."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from tools.mobile import screen_key
from tools.mobile.replay.context import (
    STATUS_NEEDS_MODEL,
    Context,
    ReplayStop,
    _append,
    _ReplayRun,
    _result,
)
from tools.mobile.replay.screen import _stamp_after
from tools.mobile.replay.verdicts import (
    ROUTE_MISMATCH_DETAIL,
    QueueTail,
    _actuated,
    _serialize_queued,
    budget_stop_reason,
)

logger = logging.getLogger("tools.mobile.executor")


class StepAt(NamedTuple):
    """Where a step is: its position, its trace entry and the live screen."""

    position: int
    entry: dict
    screen: dict | None


def _reply(content: Any) -> dict:
    return {"error": None, "content": content}


def _close(run: _ReplayRun, at: StepAt) -> None:
    """Stamp the entry's after-state and record it on the trace."""
    _stamp_after(at.entry, at.screen)
    _append(run.trace, at.entry)


def _stop(run: _ReplayRun, at: StepAt, status: str) -> ReplayStop:
    """The ``ReplayStop`` for the step *at*, its detail already on the entry."""
    return ReplayStop(status, run.trace, at.screen, "", at.entry["detail"], at.position)


# SERVER-SIDE queue (defect F2): the unreached actions ride along on THIS
# reply so the next submit can run them first instead of the model
# retyping them.
# A route replay queues NOTHING: the remainder has not been checked
# against its saved screens and must not run blind.
def _budget_stop(run: _ReplayRun, index: int, screen: dict | None) -> dict:
    """The reply for a replay that ran out of wall-clock before action *index*."""
    is_route = bool(getattr(run.ctx, "route_expect", None))
    queued = [] if is_route else _serialize_queued(run.items[index:])
    tail = QueueTail(len(queued), is_route)
    reason = budget_stop_reason(index, len(run.items), tail)
    stop = ReplayStop(STATUS_NEEDS_MODEL, run.trace, screen, "", reason, index)
    return _reply(_result(stop, budget_stop=True, queued_actions=queued))


def _route_mismatch_stop(run: _ReplayRun, at: StepAt) -> dict:
    """The reply for a saved-route step whose live screen is not the one it was
    saved from. Nothing has been actuated for this step."""
    at.entry.update(outcome="route_mismatch", detail=ROUTE_MISMATCH_DETAIL)
    _close(run, at)
    stop = _stop(run, at, STATUS_NEEDS_MODEL)
    return _reply(_result(stop, selector_stale=True, actuated=_actuated(run.trace)))


def _stamp_screen_key(ctx: Context, entry: dict, screen: Any) -> None:
    """In-memory ``screen_key`` + ``activity`` for the knowledge learner."""
    try:
        activity = str(getattr(ctx, "activity", "") or "")
        entry["screen_key"] = screen_key.screen_key(
            screen, str(getattr(ctx, "package", "") or ""), activity
        ).key
        entry["activity"] = activity
        if isinstance(screen, dict):
            entry["screen_package"] = str(screen.get("package") or "")
    except Exception:
        logger.exception("mobile app knowledge: screen key failed")


def _stamp_after_key(ctx: Context, entry: dict, screen: Any) -> None:
    try:
        entry["after_screen_key"] = screen_key.screen_key(
            screen,
            str(getattr(ctx, "package", "") or ""),
            str(getattr(ctx, "activity", "") or ""),
        ).key
    except Exception:
        logger.exception("mobile app knowledge: after screen key failed")


def _mark_refused(ctx: Context, fired: Any) -> None:
    """An ``insert_steps`` the cursor refused (budget spent, route-locked): the
    plug's ``*_fired`` event for that item is marked ``refused`` so it is never
    counted as having run."""
    item = (fired or {}).get("item") if isinstance(fired, dict) else None
    for ev in reversed(getattr(ctx, "kbuf", None) or []):
        if (
            isinstance(ev, dict)
            and ev.get("item") == item
            and str(ev.get("kind", "")).endswith("_fired")
        ):
            ev["refused"] = True
            return
