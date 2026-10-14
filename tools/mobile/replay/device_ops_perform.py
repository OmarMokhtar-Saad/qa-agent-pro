"""Replay engine: the device-op dispatcher."""

from __future__ import annotations

from tools.mobile import actions as actions_mod
from tools.mobile import adb
from tools.mobile.replay.context import Context
from tools.mobile.replay.device_ops_basic import (
    _clear_app_data_op,
    _launch_op,
    _press_op,
    _scroll_op,
    _tap_op,
)
from tools.mobile.replay.device_ops_consts import _SYSTEM_KEY_OPS
from tools.mobile.replay.device_ops_text import _text_entry_op
from tools.mobile.replay.dump import _drop_carried
from tools.mobile.replay.elements_swipe import TouchSpec


async def _perform(spec: TouchSpec, ctx: Context) -> dict:
    """One device op. Every argv is built by ``adb``, which validates again."""
    op, serial = spec.op, ctx.serial
    # Any device op can change the screen, so a carried read is stale from here.
    _drop_carried(serial)
    if op in _SYSTEM_KEY_OPS:
        return await adb.keyevent(serial, _SYSTEM_KEY_OPS[op])
    if op == "launch":
        return await _launch_op(spec.action, ctx)
    if op == "clear_app_data":
        return await _clear_app_data_op(ctx)
    if op == "open_url":
        return await adb.open_url(serial, str(getattr(spec.action, "url", "")))
    if op in actions_mod.WAIT_OPS:
        # Every wait is handled, and polled, by `replay` before any device op;
        # a wait reaching here would otherwise fall through to the unknown op.
        return {"error": "A wait is not a device op.", "content": None}
    return await _dispatch_touch(spec, ctx)


async def _dispatch_touch(spec: TouchSpec, ctx: Context) -> dict:
    """The element ops: scroll, tap, press and the text-entry trio."""
    op, serial = spec.op, ctx.serial
    if op == "scroll":
        return await _scroll_op(spec, serial)
    if op in ("tap", "tap_text"):
        return await _tap_op(spec.element, serial)
    if op == "press":
        return await _press_op(spec.action, spec.element, serial)
    if op in ("type", "fill", "clear"):
        return await _text_entry_op(spec, ctx)
    return {"error": "Unsupported action " + repr(op), "content": None}
