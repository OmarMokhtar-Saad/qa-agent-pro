"""Update an installed app: pick it, install the new build, launch, verify.

One function composes the committed pieces and decides nothing they already
decide: ``device_pick`` and ``app_pick`` resolve the device and the package (an
ambiguous answer ASKS, it never guesses), an ``install_source`` brings the
build, ``app_info`` reads the version before and after, and
``run_verdict.auto_verify_install`` judges the result.

Two rules:

* nothing here removes an app. A source that reports ``needs_confirmation`` of
  ``uninstall`` gets a pending question back (``STATUS_CONFIRM`` plus
  ``confirm_action``); the handler asks the tester through ``user_confirm`` and
  runs the guarded removal itself, then retries once;
* an expected port failure (``PORT_FAILURES``) becomes a ``STATUS_FAILED`` report
  that names the exception class and never its text; a programming error
  (``TypeError``, ``AttributeError``) is not swallowed and propagates.

The report text may carry device or app text; the handler wraps it with
``untrusted.wrap_untrusted`` before it reaches the host model.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import logging
import time  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from functools import partial
from typing import Any, Awaitable, Callable, Optional  # noqa: F401

from tools.device_manager import valid_package_name  # noqa: F401
from tools.mobile import (
    app_info,
    app_pick,
    device_pick,
    install_errors,  # noqa: F401
    install_source,
    install_sources,
    run_verdict,  # noqa: F401
)
from tools.mobile.adb_device_ui import AdbDeviceUi
from tools.mobile.app_info import AppVersion  # noqa: F401
from tools.mobile.app_pick import PickRequest
from tools.mobile.install_source import (  # noqa: F401
    InstallOutcome,
    InstallRequest,
    InstallSource,
)
from tools.mobile.resolve import ASK, MAX_ASK_OPTIONS, AskCb, Resolution  # noqa: F401
from tools.mobile.run_verdict import OUTCOME_PASS, Verdict  # noqa: F401
from tools.mobile.update_app_run import _Phases, _Run, _verdict  # noqa: F401
from tools.mobile.update_app_types import (  # noqa: F401
    ASK_CODES,
    CONFIRM_UNINSTALL,
    MAX_UPDATE_MESSAGE_CHARS,
    PORT_FAILURES,
    SOURCE_DEFAULT,
    STATUS_ASK,
    STATUS_CONFIRM,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_UNVERIFIED,
    Clock,
    Landing,
    UpdatePorts,
    UpdateReport,
    UpdateRequest,
    _clip,
    _install_label,
    _status,
    _summary,
    _version_line,
)

log = logging.getLogger(__name__)


async def update_app(request: UpdateRequest, ports: UpdatePorts) -> UpdateReport:
    """Update one app on one device. Never raises (see PORT_FAILURES)."""
    run = _Run(request, ports)
    try:
        return await run.run()
    except PORT_FAILURES as exc:
        log.warning("update_app: a port failed (%s)", type(exc).__name__)
        return run.report(
            STATUS_FAILED,
            "The update stopped: a device step failed ("
            + type(exc).__name__
            + "). Nothing was uninstalled; ask the user whether to retry.",
        )


async def _pick_device(requested: str, ask: Optional[AskCb]) -> Resolution:
    return await device_pick.pick_device(requested, ask=ask)


async def _pick_package(request: PickRequest, ask: Optional[AskCb]) -> Resolution:
    return await app_pick.pick_package(request, app_pick.PickPorts(ask=ask))


async def _launch(serial: str, package: str) -> bool:
    return await AdbDeviceUi(serial).launch(package)


async def _foreground(serial: str) -> str:
    return await AdbDeviceUi(serial).foreground()


def default_ports(ask: Optional[AskCb] = None) -> UpdatePorts:
    """Ports over the real adb-backed modules; ``ask`` is the tester question.

    Registers the shipped install sources first (idempotent), so ``app_tester``
    resolves without depending on server start-up wiring.
    """

    install_sources.register_defaults()
    return UpdatePorts(
        partial(_pick_device, ask=ask),
        partial(_pick_package, ask=ask),
        app_info.read_version,
        install_source.get_source,
        _launch,
        _foreground,
        ask,
    )
