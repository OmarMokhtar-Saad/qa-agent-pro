"""Replay engine: the single-gesture device ops (tap, launch, clear data, scroll, press)."""

from __future__ import annotations

from tools.mobile import actions as actions_mod
from tools.mobile import (
    adb,
    launch_guard,
)
from tools.mobile.adb_input import Swipe
from tools.mobile.replay.context import Context
from tools.mobile.replay.elements import (
    _SWIPE,
    TOO_SMALL_TO_SWIPE,
    _center,
    _swipe_points,
)
from tools.mobile.replay.elements_swipe import TouchSpec


async def _tap_op(element: object, serial: str) -> dict:
    """``tap`` / ``tap_text``: tap the centre of the chosen element."""
    # ONE device path for both. `tap_text` differs only in how its element
    # was chosen; once chosen a tap is a tap, and a second `adb.tap` call
    # site would be a second place for the bounds check to drift.
    center = _center(element) if isinstance(element, dict) else None
    if not center:
        return {"error": "That element has no usable bounds.", "content": None}
    return await adb.tap(serial, center[0], center[1])


async def _launch_op(action: object, ctx: Context) -> dict:
    """``launch``: start a named package (guarded) or this run's own package."""
    named = getattr(action, "package", None)
    if named:
        return await _launch_named(named, ctx)
    if not ctx.package:
        return {"error": "No app package is set for this run.", "content": None}
    return await adb.launch(ctx.serial, ctx.package)


async def _launch_named(named: str, ctx: Context) -> dict:
    """Start the named package once ``launch_guard`` allows it."""
    # A named package is judged by launch_guard BEFORE anything is
    # started; a refusal is recoverable (the model fixes the step).
    # `launch_allowed` is reached only when a package is set.
    allowed, why = launch_guard.launch_allowed(
        named,
        target=ctx.package,
        expect_apps=(
            *tuple(getattr(ctx, "expect_apps", ()) or ()),
            getattr(ctx, "home_package", ""),
        ),
    )
    if not allowed:
        return {"error": why, "content": None, "needs_model": True}
    return await adb.launch(ctx.serial, named)


async def _clear_app_data_op(ctx: Context) -> dict:
    """``clear_app_data``: wipe this run's own package, then relaunch it."""
    serial = ctx.serial
    # Only ever THIS run's own package -- `ctx.home_package` (falling back
    # to `ctx.package` for a Context built without one), never a value
    # read off the script, and never the app a multi-app goal handed over
    # to: a hand-over moves what the guard compares against, not what may
    # be wiped.
    home = getattr(ctx, "home_package", "") or ctx.package
    if not home:
        return {"error": "No app package is set for this run.", "content": None}
    cleared = await adb.clear_app_data(serial, home)
    if cleared.get("error"):
        return cleared
    return await adb.launch(serial, home)


async def _scroll_op(spec: TouchSpec, serial: str) -> dict:
    """``scroll``: swipe in the named direction, within the target if any."""
    direction = str(getattr(spec.action, "dir", "") or "")
    if direction not in _SWIPE:
        return {"error": "Unknown scroll direction.", "content": None}
    points = _swipe_points(direction, spec.element, spec.display)
    if not points:
        return {
            "error": TOO_SMALL_TO_SWIPE,
            "content": None,
            # A re-plan, not a device failure: the model can drop the
            # target or name the scrollable list instead.
            "needs_model": True,
        }
    return await adb.swipe(serial, Swipe(*points))


async def _press_op(action: object, element: object, serial: str) -> dict:
    """``press``: focus the target by a tap, then send the allowlisted key."""
    # The key goes to whatever holds FOCUS, so the target is not decorative:
    # tap it first, exactly as `type` and `clear` do. The keycode comes from
    # `actions.PRESS_KEYS` -- our own allowlist, never the model's string --
    # and `adb.keyevent` validates it a second time.
    code = actions_mod.PRESS_KEYS.get(str(getattr(action, "key", "") or ""))
    if not code:
        return {"error": "Unsupported press key.", "content": None}
    center = _center(element) if isinstance(element, dict) else None
    if not center:
        return {"error": "That field has no usable bounds.", "content": None}
    focused = await adb.tap(serial, center[0], center[1])
    if focused.get("error"):
        return focused
    return await adb.keyevent(serial, code)
