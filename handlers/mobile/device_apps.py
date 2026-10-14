"""Mobile app handlers for ``mobile_device``: stop a run, read or update an app."""

from __future__ import annotations

from typing import cast

from handlers import _common, mobile_gate
from handlers.mobile import run as mobile_run
from handlers.mobile import step as mobile_step
from handlers.mobile.device_requests import AppArgs, UpdateArgs
from tools.mobile import (
    app_pick,
    app_tools,
    device_pick,
    session_finish,
    stop_run,
    update_app,
)
from tools.mobile.resolve import RESOLVED
from tools.untrusted import wrap_untrusted


async def _stop_result(target: str, choose: _common.ChooseCb):
    """Ask ``stop_run.stop`` to stop the run *target* names (None = ask)."""

    return await stop_run.stop(
        str(target or "").strip() or None,
        session_finish.stop_ports(),
        stop_run.StopOpts(ask=mobile_run._mobile_ask(choose)),
    )


def _stop_reply(result) -> str:
    """The reply for a stop *result*: the verdict word, then its message."""

    head = "Stopped." if result.ok else "⚠️ Not stopped."
    return (
        head
        + "\n"
        + wrap_untrusted("mobile_stop", result.message, app_tools.MAX_TOOL_TEXT_CHARS)
    )


async def handle_mobile_stop(
    target: str = "", *, choose: _common.ChooseCb = None, **_elicitors
) -> str:
    """``qa_mobile_stop``: stop ONE run, named by run id or device serial.

    ``stop_run.stop`` decides; a target that names no single run is asked,
    never guessed. Never raises."""
    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        result = await _stop_result(target, choose)
    except mobile_step._MOBILE_TOOL_FAILURES as exc:
        return mobile_step._mobile_tool_failed(exc, "qa_mobile_stop")
    if result.ok:
        mobile_step._mobile_learn_after(str(getattr(result, "run_id", "") or ""))
    return _stop_reply(result)


async def _pick_device_and_app(request: AppArgs, ask) -> tuple[str, str, str]:
    """``(device, package, "")``, or ``("", "", the question to ask)``."""

    device = await device_pick.pick_device(str(request.serial or "").strip(), ask=ask)
    if device.status != RESOLVED:
        return "", "", mobile_step._mobile_unresolved("device", device)
    picked = await app_pick.pick_package(
        app_pick.PickRequest(
            str(request.app or ""),
            cast("str", device.value),
            str(request.package or ""),
        ),
        app_pick.PickPorts(ask=ask),
    )
    if picked.status != RESOLVED:
        return "", "", mobile_step._mobile_unresolved("app", picked)
    return cast("str", device.value), cast("str", picked.value), ""


async def handle_app_info(
    args: AppArgs | None = None, *, choose: _common.ChooseCb = None, **_elicitors
) -> str:
    """``qa_app_info``: the installed version of one app on one device.

    The device and the app are resolved or asked, never guessed; a read
    touches nothing on the device. Never raises."""
    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        ask = mobile_run._mobile_ask(choose)
        device, package, unresolved = await _pick_device_and_app(args or AppArgs(), ask)
        if unresolved:
            return unresolved
        return await app_tools.app_info_text(device, package)
    except mobile_step._MOBILE_TOOL_FAILURES as exc:
        return mobile_step._mobile_tool_failed(exc, "qa_app_info")


def _update_request(args: UpdateArgs):
    """The ``update_app`` request for *args* (default source when empty)."""

    return update_app.UpdateRequest(
        serial=str(args.serial or "").strip(),
        app_text=str(args.app or ""),
        package=str(args.package or "").strip(),
        source=str(args.source or "").strip() or update_app.SOURCE_DEFAULT,
    )


async def handle_update_app(
    args: UpdateArgs | None = None, *, choose: _common.ChooseCb = None, **_elicitors
) -> str:
    """``qa_update_app``: update one app to the source's latest build through
    ``app_tools.run_update``.

    ``update_app`` resolves or asks the device and the app; a version it
    cannot install over is offered as an uninstall the tester must confirm.
    With no target version the verify needs a code above the previous one.
    Never raises."""
    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        request = _update_request(args or UpdateArgs())
        return await app_tools.run_update(request, ask=mobile_run._mobile_ask(choose))
    except mobile_step._MOBILE_TOOL_FAILURES as exc:
        return mobile_step._mobile_tool_failed(exc, "qa_update_app")
