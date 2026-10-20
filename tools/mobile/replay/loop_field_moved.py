"""Replay engine: did the app move on after a typed field read empty?

A wait-until-condition, never a sleep: the dump is the poll (and the cheap focus
probe for a masked field), until the app is proved to have moved on or the deadline passes. The deadline
is the settle bound already configured (``AUTO_SETTLE_MAX_S``), stretched by the
device's dump latency. Moving on is STRUCTURAL: another package or activity, a
different structural screen key (anchor resource ids, never text), or fewer
editable fields. The raw content hash is NOT a signal: a toast or an error line
changes it and the field is still dead.
"""

from __future__ import annotations

import time
from typing import Any, NamedTuple

from tools.mobile import dump_latency, latency_scale
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import AUTO_SETTLE_MAX_S
from tools.mobile.replay.dump import _dump, _focus_probe
from tools.mobile.replay.dump_stamp import live_focus
from tools.mobile.screen_key import screen_key


class Moved(NamedTuple):
    """Whether the app moved on, and the post-action screen read that proved it.

    ``screen`` is ``None`` when the proof was the focus probe alone (nothing was
    read) or the app did not move.
    """

    moved: bool
    screen: Any = None


def _fields(screen: dict) -> int:
    """How many editable fields the screen shows."""
    rows = screen.get("elements") or []
    return sum(1 for r in rows if isinstance(r, dict) and r.get("editable"))


def _structure_differs(before: dict, after: dict) -> bool:
    """Whether the structural screen key changed (text never counts).

    A degraded key is built from class counts, which a toast changes, so two
    degraded keys are compared by their anchors alone.
    """
    a, b = screen_key(before), screen_key(after)
    if a.degraded or b.degraded:
        return a.anchors != b.anchors
    return a.key != b.key


def _moved_on(before: object, after: object) -> bool:
    """Whether the app navigated: another activity or package, another structure,
    or fewer editable fields. An unreadable screen is not moving on."""
    if not (isinstance(before, dict) and isinstance(after, dict)):
        return False
    if any(before.get(k) != after.get(k) for k in ("package", "activity")):
        return True
    return _fields(after) < _fields(before) or _structure_differs(before, after)


def _deadline(ctx: Context) -> float:
    """The monotonic cut, scaled to the device's dump latency."""
    return time.monotonic() + latency_scale.scale_settle(
        AUTO_SETTLE_MAX_S, dump_latency.p90_ms(ctx.serial)
    )


async def _focus_left(ctx: Context, focus: str, deadline: float) -> bool:
    """Probe the focus (no dump; the probe is the poll) until the window changes or the cut."""
    while time.monotonic() < deadline:
        now = await live_focus(ctx, deadline)
        if now and now != focus:
            return True
    return False


async def _read_moved(ctx: Context, before: object, deadline: float) -> Moved:
    """Dump (the dump is the poll) until the app moved on or *deadline*; a failed
    dump (no window while the app transitions) is retried, not read as "did not move"."""
    while time.monotonic() < deadline:
        dumped = await _dump(ctx, deadline=deadline)
        content = dumped.get("content")
        if not dumped.get("error") and _moved_on(before, content):
            return Moved(True, content)
    return Moved(False)


async def moved_on_masked(ctx: Context, before: object) -> Moved:
    """A masked field cannot be read back: focus probes, then ONE dump as proof.

    Only a dump that FAILED is retried (bounded by the same deadline).
    """
    deadline = _deadline(ctx)
    focus = await _focus_probe(ctx.serial, deadline - time.monotonic())
    if focus and await _focus_left(ctx, focus, deadline):
        return Moved(True)
    dumped = await _dump(ctx)
    while dumped.get("error") and time.monotonic() < deadline:
        dumped = await _dump(ctx, deadline=deadline)
    content = dumped.get("content")
    if dumped.get("error") or not _moved_on(before, content):
        return Moved(False)
    return Moved(True, content)


async def moved_on_by_deadline(ctx: Context, before: object) -> Moved:
    """Poll until the app is proved to have moved on; ``Moved(False)`` when nothing did.

    The read that proved it is returned: it is the screen after the action, so
    the caller's settle need not dump it again.
    """
    return await _read_moved(ctx, before, _deadline(ctx))
