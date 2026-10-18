"""Part of the mobile_run split (see handlers/mobile/run.py): prepare."""

from __future__ import annotations

from typing import Any

import tools.mobile.session_finish as session_finish
from handlers.mobile import run_skipped
from handlers.mobile.run_grant import mobile_grant_stage
from handlers.mobile.run_hooks import mobile_run
from handlers.mobile.run_request import _MobileTestRequest
from tools.mobile import adb as mobile_adb
from tools.mobile import preflight as mobile_preflight
from tools.mobile import render as mobile_render
from tools.mobile.goal_home_start import goal_checks_home, goal_starts_at_home
from tools.untrusted import single_line as _safe


async def _mobile_launch_target(serial: str, target: str) -> None:
    """Bring the confirmed app to the foreground; best-effort."""
    if target:
        # D3: bring the confirmed app to the foreground before the
        # preflight/first packet, so the first screen shown is the
        # app the tester asked to test rather than whatever the
        # emulator already had up -- matters most for
        # `installed_package`, whose app never goes through an
        # install step that would have launched it. Best-effort: a
        # launch failure here is not fatal, preflight still runs.

        await mobile_adb.launch(serial, target)


def _check_after(goal: str, serial: str) -> bool:
    """Whether the goal checks home and the server has not already done it."""
    return goal_checks_home(goal) and not run_skipped.home_checked(serial)


async def _launch_for_start(req: _MobileTestRequest, serial: str, target: str) -> None:
    """Launch the app unless ``launch=false`` or, with no install stage having
    run, the goal itself starts from the home screen; note which in the reply."""
    home = (
        req.launch
        and not run_skipped.tester_ran(serial)
        and goal_starts_at_home(req.goal)
    )
    launch = req.launch and not home
    state = "home" if home else "off"
    if launch:
        state = "launched+check" if _check_after(req.goal, serial) else "launched"
    run_skipped.mark_launch(serial, state)
    await _mobile_launch_target(serial, target if launch else "")


async def _mobile_preflight_reply(serial: str, target: str) -> str:
    """The reply that stops the run when preflight fails, or ``""``."""

    checked = await session_finish.preflight_for({"package": target, "serial": serial})
    if checked.get("error"):
        return "⚠️ " + _safe(checked["error"], 300)
    pf = checked.get("content") or {}
    if not pf.get("ok"):
        return mobile_render.preflight_block(pf, mobile_preflight.render(pf))
    return ""


async def _prepare_tail(
    req: _MobileTestRequest, serial: str, done: tuple[str, Any, Any]
) -> tuple[str, Any]:
    """``(stop_reply, prepared)`` after the app stage: launch, reset, preflight.

    ``done`` is ``(target, locale_taken, capture_result)``; ``prepared`` is
    ``(target, locale_taken, capture_result, reset_result)``."""
    target, locale_taken, capture_result = done
    await _launch_for_start(req, serial, target)
    reset_stage, reset_result = await mobile_run.mobile_reset_app_stage(
        mobile_run._ResetCall(serial, target, req.reset_app, req.apply)
    )
    if reset_stage:
        return reset_stage, None
    grant_stage = await mobile_grant_stage(req, serial, target)
    if grant_stage:
        return grant_stage, None
    failed = await _mobile_preflight_reply(serial, target)
    return failed, (target, locale_taken, capture_result, reset_result)
