"""Replay engine: did the app move on after a typed field read empty?

A wait-until-condition, never a sleep: poll the cheap focus probe, then a dump,
until the app is proved to have moved on or the deadline passes. The deadline
is the settle bound already configured (``AUTO_SETTLE_MAX_S``), stretched by the
device's dump latency. Moving on is STRUCTURAL: another package or activity, a
different structural screen key (anchor resource ids, never text), or fewer
editable fields. The raw content hash is NOT a signal: a toast or an error line
changes it and the field is still dead.
"""

from __future__ import annotations

import time

from tools.mobile import dump_latency, latency_scale
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import AUTO_SETTLE_MAX_S
from tools.mobile.replay.dump import _focus_probe
from tools.mobile.replay.waits_changed import _changed_poll, _focus_moved
from tools.mobile.screen_key import screen_key


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


async def moved_on_by_deadline(ctx: Context, before: object) -> bool:
    """Poll until the app is proved to have moved on; False when nothing proved it."""
    deadline = _deadline(ctx)
    focus = await _focus_probe(ctx.serial, deadline - time.monotonic())
    while time.monotonic() < deadline:
        if focus and await _focus_moved(ctx.serial, focus, deadline):
            return True
        kind, content = await _changed_poll(ctx, deadline)
        if kind == "cut":
            return False
        if kind == "read" and _moved_on(before, content):
            return True
    return False
