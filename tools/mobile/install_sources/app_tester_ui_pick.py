"""App Tester UI: open the app list and find one app's row by its label (no tap)."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, NamedTuple, Optional, Union

from tools.mobile.install_source import InstallOutcome, InstallRequest
from tools.mobile.install_sources import app_tester_ui_memory as memory
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_PACKAGE,
    _Flow,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_link import open_by_link
from tools.mobile.resolve import AskCb

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
        if await open_by_link(run, want.tester, self._sleep):
            run.card_tapped = True
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
        have = await self._versions(serial, package)  # one cheap read, no UI
        code = have.version_code if have else None
        if code is None:
            return False  # package missing (or unreadable): the UI is needed
        latest = memory.known_latest(memory.Installed(serial, package, code))
        return (
            latest is None or code >= latest
        )  # no listing known: only a missing app opens it
