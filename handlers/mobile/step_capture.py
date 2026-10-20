"""Screen capture of the mobile step handlers: the report PNG a step asked for."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from handlers.mobile.step_state import _MOBILE_SHOT_WANT
from tools.mobile import adb as mobile_adb
from tools.mobile import run_io, step_timing
from tools.mobile import run_store as mobile_run_store
from tools.mobile import screenshot as mobile_screenshot

logger = logging.getLogger("tools.mcp_handlers")

BLIND_NOTE = (
    "This screen has no readable elements (canvas or WebView). Use "
    "wait-until-text or coordinates."
)
SAVED_NOTE = " A PNG of it was saved for the report at {path}; it is not shown to you."


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


# Keep the picture, so the HTML report can show the screen. Keyed on the
# packet's `observation_id` -- the look -- and never on a screen id, which is
# invariant across the turns of a chat. Best-effort: a lost picture may never
# cost the tester the packet.
def _store_mobile_shot(packet: object, body: dict, spec: dict) -> bool:
    """Store the picture in the run folder, best-effort; True when it was written."""
    try:
        wrote = mobile_run_store.write_shot(
            str(body.get("run_id") or ""),
            str(packet.get("observation_id") or "") if isinstance(packet, dict) else "",
            spec.get("data"),
        )
        return not (wrote or {}).get("error")
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mcp mobile screen store failed")
        return False


def _screen_blind(packet: object) -> bool:
    """True when the packet says its screen has no usable element tree."""
    return isinstance(packet, dict) and bool(packet.get("screen_unusable"))


def _report_shot_wanted(turn: ShotTurn, serial: str) -> bool:
    """True when a report PNG is due: asked for, a visual stop, or a blind screen."""
    want = _MOBILE_SHOT_WANT.get()
    asked = isinstance(want, dict) and (want.get("asked") or want.get("visual"))
    return bool(serial and (asked or _screen_blind(turn.packet)))


def _blind_note(turn: ShotTurn, stored: bool) -> str:
    """The blind-screen line; it names the PNG path only when the PNG was written."""
    if not stored:
        return BLIND_NOTE
    packet = turn.packet if isinstance(turn.packet, dict) else {}
    folder = run_io.run_path(str(turn.body.get("run_id") or ""))
    name = str(packet.get("observation_id") or "") + ".png"
    return BLIND_NOTE + SAVED_NOTE.format(
        path=folder / mobile_run_store.SHOTS_DIR / name
    )


# Fast steps (WP-D): a plain step takes NO picture and no reply carries one.
# Only `screenshot=true`, a `visual` assert stop, or a screen with no readable
# elements (canvas / WebView) writes ONE PNG into the run folder for the
# report. The blind case also returns a text line, so the host can switch to
# wait-until-text or coordinates instead of looking.
async def _mobile_report_shot(turn: ShotTurn) -> str:
    """Capture and store a report PNG when due; the blind-screen note, else ``""``."""
    serial = str(turn.body.get("serial") or "")
    if not _report_shot_wanted(turn, serial):
        return ""
    _note, spec = await _capture_and_pick_spec(serial, turn)
    if spec is None:
        return ""
    stored = _store_mobile_shot(turn.packet, turn.body, spec)
    return _blind_note(turn, stored) if _screen_blind(turn.packet) else ""


def _screen_unusable(packet: object) -> str:
    """The packet's ``screen_unusable`` routing key (unknown when absent)."""
    if not isinstance(packet, dict):
        return "unknown"
    return str(packet.get("screen_unusable", "unknown") or "")
