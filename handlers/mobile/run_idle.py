"""Part of the mobile_run split (see handlers/mobile/run.py): idle."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from handlers.mobile import step as mobile_step
from tools.mobile import adb as mobile_adb
from tools.mobile import app_tools, avd_guard, device_lock, lock_reaper, run_store
from tools.mobile import session_finish as finish
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mcp_handlers")


def _mobile_retake(device_lock, idle: _Idle) -> dict:
    """The lock answer once the holder was freed; ``held`` (busy) if the retake fails."""
    held = idle.held
    try:
        taken = device_lock.take_device_lock(idle.owner, serial=idle.serial) or {}
    except mobile_step._MOBILE_TOOL_FAILURES:
        logger.warning("retake of device %s failed", idle.serial, exc_info=True)
        return held
    return taken.get("content") or held


@dataclass(frozen=True)
class _Idle:
    """The device lock answer and who is asking, for one idle retake."""

    held: dict
    owner: str
    serial: str
    holder: str


# Notes for ``_mobile_retake_after_idle`` (moved out of the body to keep it short):
# Waiting on the TESTER (a credential, a confirm) is not abandonment:
# the agent has nothing to submit until they answer.
#
# A holder the reaper just ended but could not report freed may
# have a free lock: ask once instead of answering a stale busy.
# Notes for ``_mobile_retake_after_idle`` (moved out of the body to keep it short):
#
#     Only a holder shaped like a run id this lane mints is considered; a
#     pre-run label or a lease from another process is left alone. The reaper
#     decides idleness (``lock_reaper.release_if_idle``) and a holder with no known
#     activity is never freed. The note names the old run, so it is wrapped as
#     untrusted text. Never raises: a failure keeps the busy answer.
async def _mobile_retake_after_idle(held: dict, owner: str, serial: str) -> tuple:
    """``(held, note)``: end an IDLE run that holds the device, then retake it."""
    holder = str(held.get("holder") or "")
    if held.get("acquired") or not holder:
        return held, ""
    try:
        return await _idle_release(_Idle(held, owner, serial, holder))
    except mobile_step._MOBILE_TOOL_FAILURES:
        logger.warning("idle release of %s failed", holder, exc_info=True)
        return held, ""


async def _idle_release(idle: _Idle) -> tuple:
    """Ask the reaper to free an idle holder; retake the lock if it did."""

    holder = idle.holder
    if not run_store.looks_like_a_run_id(holder) or finish.awaits_tester(holder):
        return idle.held, ""
    ctx = lock_reaper.ReleaseCtx(
        idle.serial, finish.idle_ports(), persisted_ts=finish.last_checkpoint_ts(holder)
    )
    notice = await lock_reaper.release_if_idle(holder, ctx)
    if notice is None:
        return _idle_if_ended(device_lock, idle, finish.run_ended(holder)), ""
    cap = app_tools.MAX_TOOL_TEXT_CHARS
    text = wrap_untrusted("mobile_release", notice.message(), cap)
    return _mobile_retake(device_lock, idle), text + "\n"


def _idle_if_ended(device_lock, idle: _Idle, ended: bool) -> dict:
    """Retake the lock when the holder's run has ended, else keep the busy answer."""
    return _mobile_retake(device_lock, idle) if ended else idle.held


# Notes for ``_mobile_heavy_avd_note`` (moved out of the body to keep it short):
#
#     Fix round 2, item 7: the Air drove a 1440x3120 / 600 dpi AVD on a host at
#     load ~3 per core and nothing said so. ``display_size`` and
#     ``display_density`` are cached per serial. Advice only -- never raises,
#     never turns a started run into an error.
#
async def _mobile_heavy_avd_note(serial: str) -> str:
    """The heavy-AVD warning to put above a run-start reply, or ``""``."""
    if not serial:
        return ""
    try:
        size = (await mobile_adb.display_size(serial) or {}).get("content") or [0, 0]
        density = (await mobile_adb.display_density(serial) or {}).get("content")
        panel = avd_guard.Panel(size[0], size[1], density, avd_guard.host_load())
        note = avd_guard.assess(panel)
        return ("⚠️ " + note + "\n\n") if note else ""
    except Exception:
        logger.exception("mobile heavy-AVD check failed")
        return ""
