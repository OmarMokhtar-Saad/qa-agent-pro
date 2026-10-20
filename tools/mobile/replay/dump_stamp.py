"""Replay activity stamp: the ONE activity read per step, and the focus poll that needs a live read.

See `dump_activity` for the design. On a tree-source device (qa-ime) the
settled screen of a step is stamped here with the activity, always, with
exactly one ``dumpsys``; on the uiautomator fallback every dump already
carries its own activity, so there is nothing to add.
"""

from __future__ import annotations

import time
from typing import Any

from tools.mobile.providers import composite
from tools.mobile.replay import dump_activity
from tools.mobile.replay.context_ctx import Context
from tools.mobile.replay.dump_carry import _focus_probe
from tools.mobile.replay.dump_screen import _dump, resolve_activity


async def stamp_step(ctx: Context, screen: Any) -> Any:
    """Give the step's settled *screen* its activity, in place. One read, never skipped."""
    serial = str(ctx.serial or "")
    if not isinstance(screen, dict) or not dump_activity.tree_path(serial):
        return screen
    activity = await resolve_activity(ctx)
    package = str(screen.get("package") or "")
    dump_activity.remember(serial, package, activity)
    if activity and activity != screen.get("activity"):
        _restamp(serial, screen, activity)
    return screen


def _restamp(serial: str, screen: dict, activity: str) -> None:
    """Re-derive ``screen_id`` with *activity* from the dump that made *screen*."""
    raw, view = dump_activity.last_raw(serial)
    fresh = composite.observe(raw, activity, view).get("content")
    if isinstance(fresh, dict) and fresh.get("hash") == screen.get("hash"):
        screen["screen_id"] = fresh["screen_id"]
        screen["activity"] = fresh["activity"]


async def live_focus(ctx: Context, deadline: float) -> str:
    """A focus read that is NEW each call: a probe, or on a tree device one dump."""
    if not dump_activity.tree_path(ctx.serial):
        return await _focus_probe(ctx.serial, deadline - time.monotonic())
    dumped = await _dump(ctx, deadline=deadline)
    if dumped.get("error"):
        return ""
    return dump_activity.last_package(ctx.serial)
