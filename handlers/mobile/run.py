"""Moved from handlers (refactor P2): mobile_run.

This file is the facade: the entry point ``handle_mobile_test`` and the three
sequencing bodies tests pin by source (``_mobile_offer_u2``, ``_mobile_prepare_app``,
``_mobile_new_run``). Everything else lives in ``handlers/mobile_run_*.py``;
siblings reach the names tests patch here through ``mobile_run_hooks.mobile_run``.
"""

from __future__ import annotations

import logging
from typing import Any

from handlers import _common, _elicit, mobile_gate
from handlers.mobile import run_background
from handlers.mobile.run_app import _mobile_app_stage as mobile_app_stage
from handlers.mobile.run_capture import _CaptureCall, _mobile_capture_stage
from handlers.mobile.run_device import (  # noqa: F401
    _DeviceCall,
)
from handlers.mobile.run_device import (
    _mobile_device_stage as mobile_device_stage,  # noqa: F401
)
from handlers.mobile.run_device_prefs import (
    _mobile_animations_stage as mobile_animations_stage,  # noqa: F401
)
from handlers.mobile.run_device_prefs import (
    _mobile_keyboard_stage as mobile_keyboard_stage,  # noqa: F401
)
from handlers.mobile.run_early_a11y import early_a11y
from handlers.mobile.run_handoff import (
    _mobile_hand_off as mobile_hand_off,  # noqa: F401
)
from handlers.mobile.run_idle import (  # noqa: F401
    _mobile_heavy_avd_note,
    _mobile_retake_after_idle,
)
from handlers.mobile.run_locale import (  # noqa: F401
    _mobile_locale_stage,
    _ResetCall,
)
from handlers.mobile.run_locale import (
    _mobile_reset_app_stage as mobile_reset_app_stage,  # noqa: F401
)
from handlers.mobile.run_new import (
    _mobile_claim_device,
    _new_run_open,
    _new_run_start,
)
from handlers.mobile.run_permission_hint import with_hint
from handlers.mobile.run_prepare import _prepare_tail
from handlers.mobile.run_prerun_end import _mobile_end_pre_run
from handlers.mobile.run_request import (  # noqa: F401
    _app_call,
    _AppCall,
    _mobile_ask,
    _MobileTestRequest,
)
from handlers.mobile.run_route import _mobile_test_route
from handlers.mobile.run_skipped import take_note
from handlers.mobile.run_source import _mobile_pick_source, _SourceCall  # noqa: F401
from handlers.mobile.run_start import _mobile_avd_of as mobile_avd_of  # noqa: F401
from handlers.mobile.run_start import _mobile_start as mobile_start  # noqa: F401
from handlers.mobile.run_start import _StartAsk  # noqa: F401
from handlers.mobile.run_suite import _mobile_repeat_offer  # noqa: F401
from tools.mobile import perm_groups, u2_consent
from tools.suite_store import load_suite  # noqa: F401
from tools.untrusted import single_line as _safe

logger = logging.getLogger("tools.mcp_handlers")


async def handle_mobile_test(  # noqa: PLR0913 - MCP tool schema
    source: str = "",
    suite_id: str = "",
    goal: str = "",
    cases: str = "",
    app: str = "",
    package: str = "",
    run_id: str = "",
    session_token: str = "",
    apply: bool = True,
    dry_run: bool = False,
    continue_run: bool = False,
    serial: str = "",
    avd: str = "",
    new_run: bool = False,
    locale: str = "",
    capture: str = "",
    capture_ack: bool = False,
    reset_app: bool = False,
    charter: str = "",
    grant_permissions: bool = True,
    launch: bool = True,
    deny_permissions: list[str] | None = None,
    *,
    choose: _common.ChooseCb = None,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
) -> str:
    """Drive the mobile emulator lane. Returns markdown; never raises.

    Gates, delegates, renders and audits; the notes above this function say
    why (guards, function-local ``tools.mobile`` imports, the ``_safe`` import).
    """

    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()

    apply = apply and not dry_run
    request = _MobileTestRequest.from_call(locals())
    if bad := _bad_deny(request):
        return bad
    try:
        return await _mobile_test_route(request, (run_id, session_token, continue_run))
    except Exception as exc:  # never-raise contract
        logger.exception("mcp mobile_test failed")
        _common.capture_error(exc, "qa_mobile_test")
        return "⚠️ The mobile run could not continue: " + _safe(str(exc), 200)


def _bad_deny(req: _MobileTestRequest) -> str:
    """A refusal when ``deny_permissions`` names an unknown permission, else ``""``."""
    names, error = perm_groups.parse_deny(req.deny_permissions)
    if names and not error and req.grant_permissions is False:
        error = "deny_permissions needs grant_permissions=true (the default)"
    return "Not started: " + error if error else ""


async def _mobile_offer_u2(req: _MobileTestRequest, serial: str) -> None:
    """Offer the helper app on an applying call."""
    if req.apply:
        # Item 4: asked once per device per server, only when the helper
        # is importable and not yet on; a no never blocks the run.
        await u2_consent.offer(serial, _mobile_ask(req.choose))


async def _mobile_prepare_app(
    req: _MobileTestRequest, serial: str, owner: str
) -> tuple[str, Any]:
    """``(stop_reply, prepared)``: locale, capture, app, then the tail steps."""
    stage, taken = await _mobile_locale_stage(serial, req.locale, req.apply)
    if stage:
        return stage, None
    capture_stage, captured = await _mobile_capture_stage(
        _CaptureCall(serial, req.apply, req.capture, req.capture_ack, owner)
    )
    if capture_stage:
        return capture_stage, None
    await _mobile_offer_u2(req, serial)
    if req.apply:  # the fast reader first: App Tester reads the list through it
        await early_a11y(serial, owner, req.progress)
    stage, target = await mobile_app_stage(_app_call(req, serial))
    if stage:
        return stage, None
    return await _prepare_tail(req, serial, (target, taken, captured))


async def _start_notes(serial: str) -> str:
    """Notes that go before the start reply: skipped install, heavy AVD."""
    return await take_note(serial) + await _mobile_heavy_avd_note(serial)


async def _mobile_new_run(req: _MobileTestRequest) -> str:
    """The new-run path: adopt an abandoned setup, else pick, claim, prepare, start.

    The setup body runs as a background job (fix/1530): when the host abandons
    this call the setup goes on, and the next call for the same request adopts it.
    """
    adopted = await run_background.adopt(req)
    if adopted is not None:
        return adopted
    picked, owner, early = await _new_run_open(req)
    if early:
        return early
    return await run_background.launch(req, _mobile_setup(req, picked, owner))


async def _mobile_setup(
    req: _MobileTestRequest, picked: str, owner: str
) -> tuple[str, str, bool]:
    """``(reply, serial, handed_off)``: claim a device, prepare, start."""
    handed_off = False
    try:
        idle_note, serial = await _mobile_claim_device(req, owner)
        if not serial:
            return idle_note, "", False
        stopped, prepared = await _mobile_prepare_app(req, serial, owner)
        if stopped:
            return await with_hint(req, serial, stopped), serial, False
        if not picked:
            return req.source_menu(), serial, False
        reply, handed_off = await _new_run_start(req, (picked, owner, serial), prepared)
        return idle_note + await _start_notes(serial) + reply, serial, handed_off
    finally:
        await _mobile_end_pre_run(owner, handed_off)
