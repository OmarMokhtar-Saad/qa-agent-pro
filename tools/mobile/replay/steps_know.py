"""Replay engine: the app-knowledge hooks around a step (pre, post, target, stop). Cut verbatim from tools/mobile/replay/steps.py (C5 w17)."""

from __future__ import annotations

import time
from typing import Any, NamedTuple, cast

from tools.mobile import knowledge_hooks
from tools.mobile.replay.context import (
    STATUS_NEEDS_MODEL,
    Splice,
    _ReplayRun,
    _result,
    _StepCursor,
)
from tools.mobile.replay.steps_ready import _adaptive_wait
from tools.mobile.replay.steps_stop import (
    StepAt,
    _close,
    _mark_refused,
    _reply,
    _stamp_after_key,
    _stop,
)


class Ahead(NamedTuple):
    """The step about to run: ``(index, action, entry)``, the screen and the deadline."""

    step: tuple[int, object, dict]
    screen: Any
    deadline: float


class Found(NamedTuple):
    """What the default resolution found: the action, its element and its reply."""

    action: Any
    element: Any
    reply: Any


def _insert_steps(run: _ReplayRun, cursor: _StepCursor, found: tuple) -> bool:
    """Splice the directive's steps in; a refused splice is marked on the plug."""
    index, directive = found
    consume = int(cast("dict", directive.fired or {}).get("consume") or 0)
    route = bool(run.ctx.route_expect)
    inserted = cursor.insert(Splice(index, directive.steps, route, consume))
    if not inserted:
        _mark_refused(run.ctx, directive.fired)
    return inserted


# ``reply`` ends the replay (a refuse or stop directive); ``inserted`` means
# steps were spliced in and the loop must re-enter at the same index.
async def _knowledge_pre(
    run: _ReplayRun, cursor: _StepCursor, ahead: Ahead
) -> tuple[dict | None, bool, Any]:
    """``pre_step`` then the adaptive wait, once each: ``(reply, inserted, screen)``."""
    ctx, (index, action, entry) = run.ctx, ahead.step
    ctx.script_ahead = list(cursor.items[index:])
    pre = knowledge_hooks.Pre(entry, action, ahead.screen)
    directive = await knowledge_hooks.pre_step(ctx, pre)
    if directive.kind in ("refuse", "stop"):
        why = directive.reason or "stopped by app knowledge"
        stop = _knowledge_stop(run, StepAt(index, entry, ahead.screen), why)
        return stop, False, ahead.screen
    if directive.kind == "insert_steps":
        return None, _insert_steps(run, cursor, (index, directive)), ahead.screen
    return None, False, await _adaptive_wait(run, action, ahead)


def _knowledge_post(run: _ReplayRun, post: knowledge_hooks.Post) -> None:
    """After a step: stamp ``after_screen_key``, then ``post_step`` (in memory)."""
    _stamp_after_key(run.ctx, post.entry, post.after)
    knowledge_hooks.post_step(run.ctx, post)


def _retract_miss(run: _ReplayRun, entry: dict) -> None:
    """Take back the miss the default resolution already committed to the trace."""
    if run.trace and run.trace[-1] is entry:
        run.trace.pop()
    for key in ("outcome", "detail", "after_screen_id", "after_screen_hash"):
        entry.pop(key, None)
    # ``_append`` popped ``_started``; restore it so the final ``ms`` covers
    # the whole step, not just the miss.
    if "ms" in entry:
        entry["_started"] = time.monotonic() - int(entry.pop("ms")) / 1000.0


# ``found`` is ``(element, reply)`` of the default resolution. A
# ``replace_target`` directive sets the element and clears the reply (the plug
# found what the default could not); a miss the default already committed to
# the trace is taken back, so the step is recorded once, by its real outcome.
async def _knowledge_target(
    run: _ReplayRun, at: StepAt, found: Found
) -> tuple[Any, Any]:
    """``resolve_target`` once on the found element and reply."""
    resolve = knowledge_hooks.Resolve(found.action, at.screen, found.element)
    got = await knowledge_hooks.resolve_target(run.ctx, resolve)
    if got.kind != "replace_target" or got.target is None:
        return found.element, found.reply
    if found.reply is not None:
        _retract_miss(run, at.entry)
    return got.target, None


def _knowledge_stop(run: _ReplayRun, at: StepAt, note_stop: str) -> dict:
    """The reply for a step a saved ``avoid`` note refuses."""
    at.entry.update(outcome="knowledge_avoid", detail=note_stop)
    _close(run, at)
    return _reply(_result(_stop(run, at, STATUS_NEEDS_MODEL)))
