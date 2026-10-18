"""``qa_mobile_test`` content: the AVD actions, the note intake and the screenshot sink."""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from handlers import _common, mobile_gate
from handlers.mobile import run as mobile_run
from handlers.mobile import step as mobile_step
from handlers.mobile.content_args import StepCall, _mobile_arg, _test_arg
from tools.mobile import avd_manage, knowledge_run

logger = logging.getLogger("tools.mcp_handlers")


class AvdCall(NamedTuple):
    """The ``emulator`` arguments of one ``qa_mobile_test`` call."""

    action: str
    system_image: str
    confirm: object


def _pop_avd(kwargs: dict) -> AvdCall:
    """Pop the AVD kwargs, in the order the tool reads them."""
    action = str(kwargs.pop("emulator", "") or "").strip()
    system_image = str(kwargs.pop("system_image", "") or "")
    return AvdCall(action, system_image, kwargs.pop("confirm_destructive", False))


async def _avd_audit(avd: AvdCall, name: str, apply: bool) -> None:
    """Audit one AVD action: the verb, the AVD name, apply and confirm."""
    await _common.audit(
        "mcp_mobile_avd",
        detail={
            "action": avd.action[:12],
            "avd": name[:64],
            "apply": apply,
            "confirmed": avd.confirm is True,
        },
    )


def _effective_apply(call: StepCall) -> bool:
    """``apply`` as ``handle_mobile_test`` reads it: on, and ``dry_run`` not on."""
    on = _mobile_arg(call.args, call.kwargs, "apply") is True
    return on and _mobile_arg(call.args, call.kwargs, "dry_run") is not True


async def _avd_run(avd: AvdCall, call: StepCall, avd_manage) -> str:
    """Audit, then run the AVD action; ``apply`` and ``avd`` come from the ORIGINAL call."""
    apply = _effective_apply(call)
    name = str(_mobile_arg(call.args, call.kwargs, "avd") or "").strip()
    await _avd_audit(avd, name, apply)
    return await avd_manage.manage(
        avd.action,
        avd_manage.AvdOpts(
            avd=name,
            system_image=avd.system_image,
            apply=apply,
            confirm_destructive=avd.confirm is True,
        ),
    )


# ``qa_mobile_test(emulator=...)``: list, boot, create or delete an AVD. Never raises.
#
# The lane gate comes first. The logic lives in ``tools/mobile/avd_manage``
# (function-local import, like every ``tools.mobile`` module here). ``apply``
# and ``avd`` are read from the ORIGINAL call; ``confirm`` reaches the module
# only as the literal ``True``.
async def _mobile_avd_reply(avd: AvdCall, call: StepCall) -> str:
    """``qa_mobile_test(emulator=...)``: list, boot, create or delete an AVD."""
    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        return await _avd_run(avd, call, avd_manage)
    except Exception:
        logger.exception("mobile avd action failed")
        return "⚠️ The emulator action failed; nothing further was run."


def _mobile_parse_env(raw: object) -> tuple:
    """``(env, error)``; the refusal never repeats the value it refused."""
    try:
        return knowledge_run.parse_env_checked(raw)
    except mobile_step._MOBILE_TOOL_FAILURES:
        return {}, "env could not be read"


async def _mobile_note_intake_line(args: tuple, kwargs: dict, note: object) -> str:
    """The note-intake line for a ``mobile_test`` call (empty when no note)."""
    # The store is SQLite with a busy timeout: off the event loop. Values
    # the tester typed into an existing run are refused through run_id
    # (held inputs); this tool takes no tester value of its own.
    call = StepCall(args, kwargs)
    ask = mobile_step.NoteAsk(
        note,
        run_id=_test_arg(call, "run_id"),
        package=_test_arg(call, "package") or _test_arg(call, "app"),
    )
    return await asyncio.to_thread(mobile_step.mobile_note_intake, ask)


def _arm(kwargs: dict, run_env: dict) -> tuple:
    """Open the screenshot sink and the run env; the tokens to hand to ``_disarm``."""
    want = mobile_step._MOBILE_SHOT_WANT.set(
        {"asked": bool(kwargs.pop("screenshot", False))}
    )
    token = mobile_step._MOBILE_IMAGE_SPECS.set([])
    env_token = mobile_step._MOBILE_RUN_ENV.set(run_env)
    return want, token, env_token


def _disarm(tokens: tuple) -> None:
    """Close what ``_arm`` opened, in reverse order."""
    want, token, env_token = tokens
    mobile_step._MOBILE_RUN_ENV.reset(env_token)
    mobile_step._MOBILE_IMAGE_SPECS.reset(token)
    mobile_step._MOBILE_SHOT_WANT.reset(want)


# ``handle_mobile_test``'s text PLUS the image specs of the screens it took.
#
# ``(markdown, [{filename, mime, data}, ...])`` -- the shape
# ``mcp_server._image_content_blocks`` consumes, and the shape
# ``handle_capture_screens`` already returns. THE entry point for the tool
# layer; the ``str``-returning name stays exactly what it was for every other
# caller and is this function's text, projected.
#
# Opening the sink is what ARMS the capture: see ``_MOBILE_IMAGE_SPECS``.
#
# ``emulator`` (list/boot/create/delete an AVD) is answered BEFORE the sink
# opens: it is not a run step, takes no screenshot and shares no state with one.
async def handle_mobile_test_content(*args, **kwargs) -> tuple:
    """``handle_mobile_test``'s text PLUS the image specs of the screens it took."""
    avd = _pop_avd(kwargs)
    if avd.action:
        return await _mobile_avd_reply(avd, StepCall(args, kwargs)), []
    run_env, env_error = _mobile_parse_env(kwargs.pop("env", ""))
    if env_error:
        return "Not started: " + env_error + ".", []
    tokens = _arm(kwargs, run_env)
    try:
        note = kwargs.pop("note", "")
        note_line = await _mobile_note_intake_line(args, kwargs, note)
        text = note_line + await mobile_run.handle_mobile_test(*args, **kwargs)
        return text, list(mobile_step._MOBILE_IMAGE_SPECS.get() or [])
    finally:
        _disarm(tokens)
