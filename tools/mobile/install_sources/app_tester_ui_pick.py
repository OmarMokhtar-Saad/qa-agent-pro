"""App Tester UI: open the app list and find one app's row by its label (no tap)."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any, NamedTuple, Optional, Union

from tools.mobile.install_source import InstallOutcome, InstallRequest
from tools.mobile.install_sources import app_tester_ui_memory as memory
from tools.mobile.install_sources.app_tester_ui_apk_id import learn_app_id
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_PACKAGE,
    _Flow,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_link import open_by_link
from tools.mobile.resolve import AskCb

logger = logging.getLogger(__name__)

#: Seconds a run left on the app page stays usable by install().
STAGE_TTL_S = 120


class ListedApp(NamedTuple):
    """What to find: the device, the label and the App Tester id installed."""

    serial: str
    label: str
    tester: str = APP_TESTER_PACKAGE
    package: str = ""
    #: Firebase app id (or a tester link holding it): opens the page by link.
    firebase_id: str = ""


_LAUNCH_FAILED = InstallOutcome(
    False,
    "launch_failed",
    "Could not open the Firebase App Tester. Check that it is installed and "
    "the screen is unlocked.",
)


def _stage(flow: _Flow, run: _Run, bundle: str) -> str:
    """Leave the run on the app's page: install() carries on from there."""
    flow._staged = (bundle, run, time.monotonic())
    return bundle


class _PickMixin:
    """Find an app's row in the App Tester list."""

    async def find_listed_app(
        self: _Flow, want: ListedApp, ask: Optional[AskCb] = None
    ) -> Union[str, InstallOutcome]:
        """Open App Tester, go back to its app list, scroll it, open the one row
        matching the label (asking only when several match) and return its
        bundle id. Installs nothing; install() continues from the app page."""
        ui = self._ui_factory(want.serial)
        shot = await ui.screen() if await ui.launch(want.tester) else None
        if shot is None:
            return _LAUNCH_FAILED
        run = _Run(ui, InstallRequest(want.serial, want.package, want.label), ask, None)
        memory.remember_app_id(want.package, want.firebase_id)
        await learn_app_id(want.serial, want.package)
        if await open_by_link(run, want.tester, self._sleep):
            run.card_tapped = run.via_link = True
            return _stage(self, run, want.package)
        return await self._scan_and_stage(run, shot.xml, want.firebase_id)

    async def _scan_and_stage(
        self: _Flow, run: _Run, xml: str, firebase_id: str
    ) -> Union[str, InstallOutcome]:
        picked = await self._pick_row(run, xml)
        if isinstance(picked, InstallOutcome):
            return picked
        memory.remember_app_id(picked, firebase_id)
        opened = await self._tap_card(run, picked, scroll_back=True)
        return opened if opened is not None else _stage(self, run, picked)

    def _take_staged(
        self: _Flow, request: InstallRequest, ask: Optional[AskCb], before: Any
    ) -> Optional[_Run]:
        """The staged run when it is for this app and still fresh (used once)."""
        staged, self._staged = self._staged, None
        if staged is None or staged[0] != request.package:
            return None
        if time.monotonic() - staged[2] > STAGE_TTL_S:
            return None
        return replace(staged[1], request=request, ask=ask, before=before)

    async def is_latest(self: _Flow, serial: str, package: str) -> bool:
        """True when the installed copy already is the newest build the app's
        page listed a moment ago (no App Tester screen is driven for it)."""
        verdict, why = await latest_verdict(self._versions, serial, package)
        logger.info("app_tester is_latest(%s)=%s: %s", package, verdict, why)
        return verdict

    async def is_proven_latest(self: _Flow, serial: str, package: str) -> bool:
        """True only when a remembered listing proves the install is current."""
        return await proven_latest(self._versions, serial, package)


async def _code_and_latest(read: Any, serial: str, package: str) -> tuple:
    """``(installed code, newest remembered listing)``; either may be None."""
    have = await read(serial, package)
    code = have.version_code if have else None
    if code is None:
        return None, None
    return code, memory.known_latest(memory.Installed(serial, package, code))


async def _why_no_code(read: Any, serial: str, package: str) -> str:
    """Two different reasons, told apart: the app is not on the device, or it is
    there and its version could not be read (the UI is needed either way)."""
    have = await read(serial, package)
    if have is None:
        return "app missing on the device: the UI is needed"
    return "version unreadable (app present): the UI is needed"


async def latest_verdict(read: Any, serial: str, package: str) -> tuple:
    """``(is_latest, reason)``: one cheap version read, no UI; says why."""
    code, latest = await _code_and_latest(read, serial, package)
    if code is None:
        return False, await _why_no_code(read, serial, package)
    if latest is None:
        return True, "installed code %s, no listing remembered: kept" % code
    if code >= latest:
        return True, "installed code %s >= listed %s" % (code, latest)
    return False, "installed code %s < listed %s: update needed" % (code, latest)


async def proven_latest(read: Any, serial: str, package: str) -> bool:
    """True only on positive proof: a remembered listing the installed code
    reaches. An unknown listing is not proof (the install must run)."""
    code, latest = await _code_and_latest(read, serial, package)
    return code is not None and latest is not None and code >= latest
