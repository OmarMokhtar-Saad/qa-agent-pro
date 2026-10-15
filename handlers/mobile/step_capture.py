"""Screen capture of the mobile step handlers: take, store and attach this turn's picture."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from handlers.mobile.step_state import _MOBILE_IMAGE_SPECS, _MOBILE_SHOT_WANT
from tools.mobile import adb as mobile_adb
from tools.mobile import dump_reuse, step_timing
from tools.mobile import run_store as mobile_run_store
from tools.mobile import screenshot as mobile_screenshot

logger = logging.getLogger("tools.mcp_handlers")


@dataclass(frozen=True)
class ShotTurn:
    """What one capture needs of the turn: the packet, its run body, the budget."""

    packet: object
    body: dict
    budget: Any


async def _retry_screenshot(serial: str, shot: Any, budget: Any) -> Any:
    """The retried shot, or ``shot`` itself when the budget has no room for one."""

    wait = mobile_screenshot.retry_timeout_s(
        None if budget is None else budget.remaining(),
        mobile_adb.DEFAULT_TIMEOUT_S,
    )
    if wait is None:
        return shot
    return await step_timing.timed(
        "screenshot", mobile_adb.screencap(serial, timeout_s=wait)
    )


# At most ONE retry, and only when the turn's budget has room for it
# (fix round 3, item 3): a first timeout already spent up to 30 s,
# and a second full wait would overrun the call. The first timeout
# is already marked on the call by `adb.screencap` or
# `adb.screencap_raw`, so `_tracked` reports it as `timed_out`
# whatever the retry does.
async def _screen_shot(serial: str, budget: Any) -> tuple:
    """``(note, shot)``: capture, retrying a timeout once."""

    note = mobile_screenshot.CAPTURE_FAILED_NOTE
    shot = await step_timing.timed("screenshot", mobile_screenshot.capture(serial))
    if (shot or {}).get("timed_out"):
        shot = await _retry_screenshot(serial, shot, budget)
        if (shot or {}).get("timed_out"):
            # Said as a TIMEOUT, not as the generic failure.
            note = mobile_screenshot.CAPTURE_TIMED_OUT_NOTE
    return note, shot


async def _capture_and_pick_spec(serial: str, turn: ShotTurn) -> tuple:
    """``(note, spec)``: capture the screen, retrying a timeout once if the
    turn's budget has room. ``spec`` is ``None`` when nothing was captured."""

    note = mobile_screenshot.CAPTURE_FAILED_NOTE
    try:
        note, shot = await _screen_shot(serial, turn.budget)
        spec = mobile_screenshot.to_spec(
            (shot or {}).get("content"),
            run_id=turn.body.get("run_id"),
            tc_id=(turn.packet.get("tc_id") if isinstance(turn.packet, dict) else ""),
        )
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mcp mobile screen capture failed")
        spec = None
    return note, spec


# Keep the picture, so the HTML report can show the screen rather than
# only a drawing of it. The SAME bytes the model was handed.
#
# Keyed on the packet's `observation_id` -- the look -- and never on a screen
# id, which is invariant across the turns of a chat. Best-effort exactly like
# `case_runner`'s `write_screen`: a failure is swallowed, because a lost
# picture may never cost the tester the packet.
def _store_mobile_shot(packet: object, body: dict, spec: dict) -> None:
    """Store the picture the model was handed, best-effort."""
    try:
        mobile_run_store.write_shot(
            str(body.get("run_id") or ""),
            str(packet.get("observation_id") or "") if isinstance(packet, dict) else "",
            spec.get("data"),
        )
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mcp mobile screen store failed")


# Notes for the two early returns below. No image channel on this path: NOT an
# attempt that failed -- so no device call is made and the note says exactly
# that. A run with no device yet (an explore turn before provisioning): there
# is nothing to capture FROM.
async def _mobile_capture_screen_note(turn: ShotTurn, attach: bool) -> str:
    """Capture this turn's screen and return the note that says what happened;
    a captured picture is appended to the image sink when it is to be attached."""

    sink = _MOBILE_IMAGE_SPECS.get()
    serial = str(turn.body.get("serial") or "")
    if sink is None:
        return mobile_screenshot.NO_IMAGE_CHANNEL_NOTE
    if not serial:
        return mobile_screenshot.CAPTURE_FAILED_NOTE
    if dump_reuse.consume_shot_skip(serial) and not attach:
        return mobile_screenshot.NOT_ATTACHED_NOTE  # refused: screen unchanged
    note, spec = await _capture_and_pick_spec(serial, turn)
    if spec is None:
        return note
    _store_mobile_shot(turn.packet, turn.body, spec)
    if not attach:
        return mobile_screenshot.NOT_ATTACHED_NOTE
    sink.append(spec)
    return mobile_screenshot.ATTACHED_NOTE


# DUMP FIRST (S1). The picture is still taken and stored every step -- the
# report shows it -- but it goes to the model only when the element list is
# not enough, or the model asked. A packet without the routing key (not
# made by a `mobile_run` builder) reads as unusable: the safe direction.
def _mobile_screen_attach(packet: object) -> tuple[str, bool]:
    """(the packet's ``screen_unusable`` routing key, whether to attach the picture)."""
    unusable = (
        str(packet.get("screen_unusable", "unknown") or "")
        if isinstance(packet, dict)
        else "unknown"
    )
    want = _MOBILE_SHOT_WANT.get()
    want = want if isinstance(want, dict) else {}
    return unusable, bool(unusable or want.get("asked") or want.get("visual"))
