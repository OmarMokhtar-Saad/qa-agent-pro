"""Tool-level orchestration for the mobile app tools, with no MCP knowledge.

The MCP handlers stay thin: they build an ``UpdateRequest`` and an ``ask``
callback and hand both here. Everything that decides something lives in this
module, and every function returns TEXT for the host model.

The one destructive step is the uninstall that a signing mismatch needs.
``update_app`` never removes an app: it returns ``STATUS_CONFIRM``. Here the
TESTER is asked through ``user_confirm`` (a tool argument written by the host
model is not consent), and only a real yes runs ``adb.uninstall``. Then the
update is retried exactly ONCE with the package named explicitly (an explicit
package skips the installed check in ``app_pick``). A second conflict after the
retry stops: there is no second uninstall and no second question.

Text that came from a device or a store is wrapped with
``untrusted.wrap_untrusted`` and capped at ``MAX_TOOL_TEXT_CHARS``; the fixed
sentences written here stay outside the wrapper. Nothing here raises except
``asyncio.CancelledError``.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import NamedTuple, Optional

from tools.mobile import adb, app_info, update_app, user_confirm  # noqa: F401
from tools.mobile.app_tools_uninstall import (
    is_uninstall_question,
    uninstall_prompt,
    uninstalled,
)
from tools.mobile.resolve import AskCb
from tools.mobile.update_app import UpdatePorts, UpdateReport, UpdateRequest
from tools.untrusted import wrap_untrusted

# What a device, store or prompt failure can raise; anything else is a bug.
FAILURES = (OSError, RuntimeError, TimeoutError, ValueError, LookupError, TypeError)

log = logging.getLogger(__name__)

#: Characters of device or store text kept in one tool result. The host model
#: re-reads it every turn; the report message is already clipped to
#: MAX_UPDATE_MESSAGE_CHARS, so this is the outer limit on message plus options.
MAX_TOOL_TEXT_CHARS = 4000

LABEL_UPDATE = "mobile_update_app"
LABEL_INFO = "mobile_app_info"


def _plain(head: str, text: str) -> str:
    """A result made only of sentences written in this module."""
    return head + "\n" + text


def _compose(head: str, device_text: str, label: str) -> str:
    """``head`` plus ``device_text`` as an untrusted, capped block."""
    body = wrap_untrusted(label, device_text, MAX_TOOL_TEXT_CHARS)
    return head + "\n" + body if body else head


def _render(report: UpdateReport) -> str:
    lines = [report.message]
    if report.options:
        lines.append("Options: " + "; ".join(report.options))
    return _compose("update_app: " + report.status, "\n".join(lines), LABEL_UPDATE)


def _target_code(value: object) -> Optional[int]:
    """The target version code as an int; ValueError when it is not one."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("a flag is not a version code")
    code = int(str(value).strip())
    if code < 0:
        raise ValueError("a version code is not negative")
    return code


class _Env(NamedTuple):
    ports: UpdatePorts
    ask: Optional[AskCb]


async def _consented(report: UpdateReport, ask: Optional[AskCb]) -> str:
    """The stop text when the tester did not agree or adb failed, else ``""``."""
    question = uninstall_prompt(report)
    consent = await user_confirm.confirm(question, report.confirm_action, ask)
    if consent is None:
        return _plain(
            "update_app: declined",
            "The tester declined the uninstall, so the app was left as it is and "
            "the update was not done. Do not uninstall without asking the tester "
            "again.",
        )
    if not await uninstalled(report):
        return _plain(
            "update_app: failed",
            "The uninstall did not succeed, so the update was not retried. Check "
            "the device and ask the tester how to continue.",
        )
    return ""


async def _resolve_conflict(req: UpdateRequest, report: UpdateReport, env: _Env) -> str:
    """Ask the tester; on a real yes uninstall once and retry once."""
    stop = await _consented(report, env.ask)
    if stop:
        return stop
    retry = await update_app.update_app(
        dataclasses.replace(req, package=report.package), env.ports
    )
    if is_uninstall_question(retry):
        return _plain(
            "update_app: failed",
            "The install still conflicts after the uninstall. Stopped without a "
            "second uninstall; ask the tester how to continue.",
        )
    return _render(retry)


async def _run(
    req: UpdateRequest, ask: Optional[AskCb], ports: Optional[UpdatePorts]
) -> str:
    try:
        req = dataclasses.replace(
            req, target_version_code=_target_code(req.target_version_code)
        )
    except ValueError:
        return _plain(
            "update_app: failed",
            "target_version_code must be a whole number such as 120. Ask the "
            "tester for the version code; nothing was changed.",
        )
    use = ports or update_app.default_ports(ask)
    report = await update_app.update_app(req, use)
    if is_uninstall_question(report):
        return await _resolve_conflict(req, report, _Env(use, ask))
    return _render(report)


async def run_update(
    req: UpdateRequest,
    *,
    ask: Optional[AskCb],
    ports: Optional[UpdatePorts] = None,
) -> str:
    """Update one app and return the tool text. Never raises but CancelledError."""
    try:
        return await _run(req, ask, ports)
    except FAILURES as exc:
        log.warning("run_update: unexpected failure (%s)", type(exc).__name__)
        return _plain(
            "update_app: failed",
            "The update stopped unexpectedly (" + type(exc).__name__ + "). Check "
            "the device before retrying.",
        )


async def app_info_text(serial: str, package: str) -> str:
    """The installed version of ``package`` on ``serial`` as tool text."""
    try:
        version = await app_info.read_version(serial, package)
    except FAILURES as exc:
        log.warning("app_info_text: unexpected failure (%s)", type(exc).__name__)
        return _plain(
            "app_info: failed",
            "The version read failed (" + type(exc).__name__ + "). Ask the "
            "tester whether to retry.",
        )
    if version is None:
        return _plain(
            "app_info: not readable",
            "No version could be read. The app may not be installed, or the "
            "package id is not valid.",
        )
    return _compose("app_info: ok", package + ": " + version.line(), LABEL_INFO)
