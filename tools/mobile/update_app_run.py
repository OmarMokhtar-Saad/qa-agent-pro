"""One update_app call: the phase timer, the verdict and the run state."""

from __future__ import annotations

from typing import Any, Awaitable, Optional

from tools.mobile import install_errors, run_verdict
from tools.mobile.app_info import AppVersion
from tools.mobile.app_pick import PickRequest
from tools.mobile.install_source import InstallOutcome, InstallRequest, InstallSource
from tools.mobile.resolve import ASK, MAX_ASK_OPTIONS, Resolution
from tools.mobile.run_verdict import Verdict
from tools.mobile.update_app_types import (
    ASK_CODES,
    CONFIRM_UNINSTALL,
    STATUS_ASK,
    STATUS_CONFIRM,
    STATUS_FAILED,
    Clock,
    Landing,
    UpdatePorts,
    UpdateReport,
    UpdateRequest,
    _clip,
    _install_label,
    _status,
    _summary,
)


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


def _verdict(target: Optional[int], versions: tuple, front: tuple) -> Optional[Verdict]:
    """The install verdict; *versions* is ``(before, after)``, *front* is
    ``(foreground_package, package)``."""
    before, after = versions
    return run_verdict.auto_verify_install(
        run_verdict.InstallFacts(
            previous_code=before.version_code if before else None,
            installed_code=after.version_code if after else None,
            target_code=target,
            foreground_package=front[0],
            package=front[1],
        )
    )


class _Run:
    """State of one update_app call: the request, the ports, what was resolved."""

    def __init__(self, request: UpdateRequest, ports: UpdatePorts) -> None:
        self.request = request
        self.ports = ports
        self.phases = _Phases(ports.clock)
        self.serial = ""
        self.package = ""

    def report(self, status: str, message: str, **extra: Any) -> UpdateReport:
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
        verdict = _verdict(
            self.request.target_version_code, (before, after), (shown, package)
        )
        return self.report(
            _status(verdict, launched),
            _summary(Landing(package, launched, (before, after)), verdict),
            before=before,
            after=after,
            verdict=verdict,
        )

    async def _resolve(self) -> Optional[UpdateReport]:
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
        return None

    async def _install(self, source: Any) -> Optional[UpdateReport]:
        request, ports = self.request, self.ports
        install_request = InstallRequest(
            self.serial,
            self.package,
            _install_label(request),
            None,
            request.target_version_code,
        )
        outcome = await self.phases.run(
            "install", source.install(install_request, ports.ask)
        )
        return self.install_stop(outcome)

    async def run(self) -> UpdateReport:
        request, ports = self.request, self.ports
        stop = await self._resolve()
        if stop is not None:
            return stop
        source: Any = ports.get_source(request.source)
        problem = await self.source_problem(source)
        if problem is not None:
            return problem
        before = await self.phases.run(
            "version_before", ports.read_version(self.serial, self.package)
        )
        stop = await self._install(source)
        if stop is not None:
            return stop
        return await self.finish(before)
