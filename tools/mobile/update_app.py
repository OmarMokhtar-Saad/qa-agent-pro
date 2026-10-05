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

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from tools.mobile import (
    app_info,
    app_pick,
    device_pick,
    install_errors,
    install_source,
    run_verdict,
)
from tools.mobile.adb_device_ui import AdbDeviceUi
from tools.mobile.app_info import AppVersion
from tools.mobile.app_pick import PickRequest
from tools.mobile.install_source import InstallOutcome, InstallRequest, InstallSource
from tools.mobile.resolve import ASK, MAX_ASK_OPTIONS, AskCb, Resolution
from tools.mobile.run_verdict import OUTCOME_PASS, Verdict

log = logging.getLogger(__name__)

#: Characters of one report message. The host model re-reads it every turn; it
#: is a verdict plus two version lines, or one question.
MAX_UPDATE_MESSAGE_CHARS = 800

STATUS_OK = "ok"
STATUS_ASK = "ask"
STATUS_CONFIRM = "confirm"
STATUS_FAILED = "failed"
STATUS_UNVERIFIED = "unverified"

SOURCE_DEFAULT = "app_tester"
CONFIRM_UNINSTALL = "uninstall"

#: App Tester outcome codes that are a question for the tester, not a failure.
ASK_CODES = frozenset({"app_ambiguous", "app_not_found", "release_ambiguous"})

#: What a port may raise. Narrow on purpose: a bug (TypeError, AttributeError)
#: must surface, and CancelledError is not an Exception so it always propagates.
PORT_FAILURES = (
    OSError,
    RuntimeError,
    TimeoutError,
    asyncio.TimeoutError,
    ValueError,
    LookupError,
)

Clock = Callable[[], float]


@dataclass(frozen=True)
class UpdateRequest:
    serial: str = ""
    app_text: str = ""
    package: str = ""
    source: str = SOURCE_DEFAULT
    app_label: str = ""
    target_version_code: Optional[int] = None


@dataclass(frozen=True)
class UpdatePorts:
    """Everything update_app touches, injected so tests need no device."""

    pick_device: Callable[[str], Awaitable[Resolution]]
    pick_package: Callable[[PickRequest], Awaitable[Resolution]]
    read_version: Callable[[str, str], Awaitable[Optional[AppVersion]]]
    get_source: Callable[[str], Optional[InstallSource]]
    launch: Callable[[str, str], Awaitable[bool]]
    foreground: Callable[[str], Awaitable[str]]
    ask: Optional[AskCb] = None
    clock: Clock = time.monotonic


@dataclass(frozen=True)
class UpdateReport:
    status: str
    message: str
    serial: str = ""
    package: str = ""
    before: Optional[AppVersion] = None
    after: Optional[AppVersion] = None
    verdict: Optional[Verdict] = None
    confirm_action: str = ""
    options: tuple = ()
    timings_ms: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "serial": self.serial,
            "package": self.package,
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
            "verdict": self.verdict.line() if self.verdict else "",
            "confirmAction": self.confirm_action,
            "options": list(self.options),
            "timingsMs": dict(self.timings_ms),
        }


def _clip(text: object) -> str:
    return str(text or "").strip()[:MAX_UPDATE_MESSAGE_CHARS]


class _Phases:
    """Milliseconds per phase, from the injected clock."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.ms: dict = {}

    async def run(self, name: str, work: Awaitable):
        started = self._clock()
        try:
            return await work
        finally:
            self.ms[name] = int((self._clock() - started) * 1000)


def _status(verdict: Optional[Verdict], launched: bool) -> str:
    if not launched:
        return STATUS_FAILED
    if verdict is None:
        return STATUS_UNVERIFIED
    return STATUS_OK if verdict.outcome == OUTCOME_PASS else STATUS_FAILED


def _version_line(label: str, version: Optional[AppVersion]) -> str:
    return label + ": " + (version.line() if version else "not readable") + "."


def _summary(
    package: str,
    launched: bool,
    versions: tuple,
    verdict: Optional[Verdict],
) -> str:
    parts = []
    if not launched:
        parts.append("Installed, but launching " + package + " failed.")
    if verdict is None:
        parts.append(
            "Could not verify the update: the installed version was unreadable, "
            "or there was no earlier version or target to compare with."
        )
    else:
        parts.append(verdict.line())
    parts.append(_version_line("Before", versions[0]))
    parts.append(_version_line("After", versions[1]))
    return " ".join(parts)


class _Run:
    """State of one update_app call: the request, the ports, what was resolved."""

    def __init__(self, request: UpdateRequest, ports: UpdatePorts) -> None:
        self.request = request
        self.ports = ports
        self.phases = _Phases(ports.clock)
        self.serial = ""
        self.package = ""

    def report(self, status: str, message: str, **extra: object) -> UpdateReport:
        return UpdateReport(
            status,
            _clip(message),
            self.serial,
            self.package,
            timings_ms=dict(self.phases.ms),
            **extra,
        )

    def unresolved(self, got: Resolution) -> UpdateReport:
        if got.status == ASK:
            shown = tuple(c.display() for c in got.options[:MAX_ASK_OPTIONS])
            return self.report(STATUS_ASK, got.menu(), options=shown)
        return self.report(STATUS_FAILED, got.question or "Nothing matched.")

    async def source_problem(
        self, source: Optional[InstallSource]
    ) -> Optional[UpdateReport]:
        name = repr(str(self.request.source)[:40])
        if source is None:
            return self.report(
                STATUS_FAILED, "No install source named " + name + " is registered."
            )
        ready = await self.phases.run("source", source.available(self.serial))
        if ready:
            return None
        return self.report(
            STATUS_FAILED,
            "The install source " + name + " is not available on " + self.serial + ".",
        )

    def confirmation(self, outcome: InstallOutcome) -> UpdateReport:
        if outcome.needs_confirmation != CONFIRM_UNINSTALL:
            asked = repr(str(outcome.needs_confirmation)[:40])
            return self.report(
                STATUS_FAILED,
                "The install source asked for " + asked + ", which is not "
                "supported. Nothing was changed.",
            )
        action = CONFIRM_UNINSTALL + ":" + self.serial + ":" + self.package
        why = outcome.message or install_errors.explain(outcome.code).guidance
        head = (
            "The tester must agree before "
            + self.package
            + " is uninstalled on "
            + self.serial
            + " (that deletes its data; nothing is uninstalled "
            "automatically). "
        )
        return self.report(STATUS_CONFIRM, head + why, confirm_action=action)

    def install_stop(self, outcome: InstallOutcome) -> Optional[UpdateReport]:
        if outcome.needs_confirmation:
            return self.confirmation(outcome)
        if outcome.ok:
            return None
        if outcome.code in ASK_CODES:
            fallback = "Ask the user which one they mean; do not guess."
            return self.report(STATUS_ASK, outcome.message or fallback)
        why = outcome.message or install_errors.explain(outcome.code).guidance
        return self.report(STATUS_FAILED, why)

    async def finish(self, before: Optional[AppVersion]) -> UpdateReport:
        ports, serial, package = self.ports, self.serial, self.package
        launched = await self.phases.run("launch", ports.launch(serial, package))
        after = await self.phases.run(
            "version_after", ports.read_version(serial, package)
        )
        shown = await self.phases.run("foreground", ports.foreground(serial))
        verdict = run_verdict.auto_verify_install(
            previous_code=before.version_code if before else None,
            installed_code=after.version_code if after else None,
            target_code=self.request.target_version_code,
            foreground_package=shown,
            package=package,
        )
        return self.report(
            _status(verdict, launched),
            _summary(package, launched, (before, after), verdict),
            before=before,
            after=after,
            verdict=verdict,
        )

    async def run(self) -> UpdateReport:
        request, ports = self.request, self.ports
        device = await self.phases.run("device", ports.pick_device(request.serial))
        if not device.resolved:
            return self.unresolved(device)
        self.serial = str(device.value)
        pick = PickRequest(request.app_text, self.serial, request.package)
        app = await self.phases.run("package", ports.pick_package(pick))
        if not app.resolved:
            return self.unresolved(app)
        self.package = str(app.value)
        source = ports.get_source(request.source)
        problem = await self.source_problem(source)
        if problem is not None:
            return problem
        before = await self.phases.run(
            "version_before", ports.read_version(self.serial, self.package)
        )
        install_request = InstallRequest(
            self.serial,
            self.package,
            request.app_label,
            None,
            request.target_version_code,
        )
        outcome = await self.phases.run(
            "install", source.install(install_request, ports.ask)
        )
        stop = self.install_stop(outcome)
        if stop is not None:
            return stop
        return await self.finish(before)


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


def default_ports(ask: Optional[AskCb] = None) -> UpdatePorts:
    """Ports over the real adb-backed modules; ``ask`` is the tester question.

    Registers the shipped install sources first (idempotent), so ``app_tester``
    resolves without depending on server start-up wiring.
    """
    from tools.mobile import install_sources

    install_sources.register_defaults()

    async def pick_device(requested: str) -> Resolution:
        return await device_pick.pick_device(requested, ask=ask)

    async def pick_package(request: PickRequest) -> Resolution:
        return await app_pick.pick_package(request, ask=ask)

    async def launch(serial: str, package: str) -> bool:
        return await AdbDeviceUi(serial).launch(package)

    async def foreground(serial: str) -> str:
        return await AdbDeviceUi(serial).foreground()

    return UpdatePorts(
        pick_device,
        pick_package,
        app_info.read_version,
        install_source.get_source,
        launch,
        foreground,
        ask,
    )
