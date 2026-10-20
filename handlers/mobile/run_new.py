"""Part of the mobile_run split (see handlers/mobile/run.py): new."""

from __future__ import annotations

import tools.mobile.device_lock as device_lock
import tools.mobile.session_finish as session_finish
from handlers.mobile.run_hooks import mobile_run
from handlers.mobile.run_request import _MobileTestRequest
from tools.mobile import prerun_inflight
from tools.mobile import render as mobile_render

# ---------------------------------------------------------------------------
# Notes for ``_mobile_new_run`` (the sequence lives in the facade; the lock rules stay here)
# The new-run body lives in `_mobile_new_run`; the notes on WHY the lock is
# taken where it is stay here, with the call that starts the sequence.
# THE LOCK IS TAKEN BEFORE THE DEVICE STAGE. Booting, downloading and
# installing are device-touching too, and they run before any run_id
# exists -- so the lock is held under a label THIS CALL mints, and
# handed to the run inside `_mobile_start` the moment there is a run to
# hand it to.
#
# PER CALL, not per process. It was per process, and `acquire` grants a
# matching label reentrantly, so two concurrent new-run calls in one MCP
# server both got `acquired=True` and both drove the emulator -- and
# whichever returned first released the other's hold in its `finally`.
# An owner label must identify exactly ONE holder.
#
# HELD FOR EXACTLY THIS ONE CALL, and that is the deliberate part. A
# placeholder owner has no run and no lease heartbeat, so nothing could
# release it if this chat vanished mid-provisioning, and the device
# would stay dead until the editor was restarted. Once the run is
# planned the identity IS durable -- on disk, resumable,
# heartbeat-backed -- and the lock is then kept across calls.
#
# Holding it ACROSS the pointer replies would not close the double-spawn
# it looks like it should: the next call decides from the same
# `adb devices` answer, so serialising two calls that read the same
# state produces the same outcome twice. See
# `.claude/plans/plan-emulator-lock-2026-09-04.md` §5.2.
# ---------------------------------------------------------------------------


# Notes for ``_mobile_pick_or_menu`` (moved out of the body to keep it short):
# THE SOURCE IS PICKED BEFORE ANY DEVICE WORK (fix round 2, item 3).
# The menu is a question about the arguments, not the device, and it
# used to be asked LAST -- after the device was selected and locked,
# the locale read, the app resolved and launched and the preflight run
# -- so a bare "what should the emulator run?" cost 50 s on the Air,
# and up to the boot wait on a cold emulator. `_mobile_pick_source`
# touches no device (it elicits through `choose` at most), so moving it
# here changes the order of questions only: source, then device.
#
# An INSTALL-menu key in `source` (`play_store`, `installed_package`,
# ...) answers the app question, not this one: that call still runs
# the device and app stages first and is asked this menu after them,
# exactly as before the move.
#
# Two start arguments implying different lanes ask the NARROW
# question; nothing given asks the menu. One entry point, so a
# conflict cannot be rendered by an untested path.
# `source` reaches here ONLY when it matched no option key --
# `_mobile_pick_source` returns the key for everything it
# recognises -- so passing it cannot mislabel a good answer.
async def _mobile_pick_or_menu(req: _MobileTestRequest) -> tuple[str, str]:
    """``(picked, menu_reply)``: the source key, or the menu to send back.

    No device work happens here, and the menu is shown before any lock."""

    picked = await mobile_run._mobile_pick_source(_source_call(req))
    install_only = bool(
        mobile_render.install_source_for_label(str(req.source or "").strip())
    )
    if not picked and not install_only:
        return picked, req.source_menu()
    return picked, ""


def _source_call(req: _MobileTestRequest):
    """The source-pick call for one request."""
    return mobile_run._SourceCall(
        req.source, req.suite_id, req.goal, req.cases, req.choose
    )


# Notes for ``_mobile_claim_device`` (moved out of the body to keep it short):
# Given back BEFORE the long steps, not after, or the concurrency
# this sequencing exists for would be nominal: install and preflight
# are the minutes, and they touch only the device now named.
# Notes for ``_mobile_claim_device`` (moved out of the body to keep it short):
#
#     An empty ``serial`` means stop and send ``reply``. Otherwise ``reply`` is
#     the idle-release note to put above the run-start reply (often empty).
async def _mobile_claim_device(req: _MobileTestRequest, owner: str) -> tuple[str, str]:
    """``(reply, serial)``: stage and lock the device, then give selection back."""
    staged, serial = await mobile_run.mobile_device_stage(
        mobile_run._DeviceCall(
            req.apply,
            serial=req.serial,
            avd=req.avd,
            locale=req.locale,
            choose=req.choose,
        )
    )
    if staged:
        return staged, ""
    return await _mobile_lock_device(owner, serial)


async def _mobile_lock_device(owner: str, serial: str) -> tuple[str, str]:
    """Take the device lock for ``serial``, then release the select lock."""

    held = (device_lock.take_device_lock(owner, serial=serial) or {}).get(
        "content"
    ) or {}
    held, idle_note = await mobile_run._mobile_retake_after_idle(held, owner, serial)
    if not held.get("acquired"):
        return mobile_render.device_busy_block(held), ""
    session_finish.release_select_lock(owner)
    return idle_note, serial


async def _new_run_open(req: _MobileTestRequest) -> tuple[str, str, str]:
    """``(picked, owner, early_reply)``: pick the source, then take the select lock."""

    picked, menu_reply = await _mobile_pick_or_menu(req)
    if menu_reply:
        return picked, "", menu_reply
    owner = device_lock.new_provisioning_owner()
    # SELECTION FIRST, THEN THE DEVICE (see the lock notes above).
    taken = (session_finish.take_select_lock(owner) or {}).get("content") or {}
    if not taken.get("acquired"):
        return picked, owner, mobile_render.device_busy_block(taken)
    # From here the hold is this call's and its setup call is RUNNING; the
    # heal in `take_device_lock` leaves a running owner alone (fix/1520).
    prerun_inflight.enter(owner)
    return picked, owner, ""


async def _new_run_start(
    req: _MobileTestRequest, run: tuple[str, str, str], prepared: tuple
) -> tuple[str, bool]:
    """``(reply, handed_off)`` from ``_mobile_start``; the caller adds the AVD note."""
    picked, owner, serial = run
    target, locale_taken, capture_result, reset_result = prepared
    ask = mobile_run._StartAsk(
        picked,
        **req.start_kwargs(),
        package=target,
        serial=serial,
        pre_run_owner=owner,
        locale=locale_taken,
        capture=capture_result,
        reset_app=reset_result,
        permissions=req.permission_record(),
    )
    return await mobile_run.mobile_start(ask)
