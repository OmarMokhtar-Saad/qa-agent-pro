"""Device stage of a resumed mobile run: no AVD, reboot preflight, result text."""

from __future__ import annotations

from typing import Any

from tools.mobile import preflight as mobile_preflight
from tools.mobile import render as mobile_render
from tools.mobile import session_device, session_finish, session_runs
from tools.mobile.session_core import STATE_NO_AVD


def _no_avd_text(state: dict) -> str:
    """The reply for a run whose emulator is gone from this machine."""

    # The name comes off our own manifest, but it is echoed into markdown,
    # so only AVD-name characters survive.
    chars = (c for c in str(state.get("avd") or "") if c.isalnum() or c in "._-")
    gone = "".join(chars)[:60]
    return (
        "The emulator this run used"
        + (" (`" + gone + "`)" if gone else "")
        + " is not on this machine any more, so this run cannot be re-booted "
        "on it. Set up an emulator as below, then start a new run.\n\n"
        + mobile_render.setup_guide_block(
            mobile_render.setup_guide(mobile_render.SETUP_NO_AVD)
        )
    )


async def _reboot_preflight(run_id: str) -> tuple[str, dict]:
    """``(error_text, preflight)`` of the re-booted device; the text is ``""`` if fine."""

    resolved = session_runs.resolve(run_id)
    if resolved.get("error"):
        return "⚠️ " + str(resolved["error"])[:300], {}
    checked = await session_finish.preflight_for(resolved.get("content") or {})
    if checked.get("error"):
        return "⚠️ " + str(checked["error"])[:300], {}
    return "", checked.get("content") or {}


async def _after_reboot(run_id: str) -> str:
    """``""`` when the re-booted device passes the preflight, else the block."""

    failure, pf = await _reboot_preflight(run_id)
    if failure:
        return failure
    if not pf.get("ok"):
        return mobile_render.preflight_block(pf, mobile_preflight.render(pf))
    return ""


# ``""`` to carry on, or the markdown a resumed run must be answered with.
#
# Two failures this closes: an emulator that died between chats made every
# adb call fail with a device error the tester could not act on, and a
# re-booted emulator can come back on a DIFFERENT serial -- so the preflight
# has to run again before a packet is produced against it.
async def _mobile_resume_device_stage(run_id: str, *, budget: Any = None) -> str:
    """``""`` to carry on, or the markdown a resumed run must be answered with."""

    ready = await session_device.ensure_run_device(run_id, budget=budget)
    if ready.get("error"):
        return "⚠️ " + str(ready["error"])[:300]
    state = ready.get("content") or {}
    if str(state.get("state") or "") == STATE_NO_AVD:
        return _no_avd_text(state)
    if str(state.get("state") or "") != "ready":
        return mobile_render.device_pending_block(state)
    return await _after_reboot(run_id) if state.get("rebooted") else ""
