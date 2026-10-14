"""App Tester UI: open the app list and find one app's row by its label (no tap)."""

from __future__ import annotations

from typing import NamedTuple, Optional, Union

from tools.mobile.install_source import InstallOutcome, InstallRequest
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_PACKAGE,
    _Flow,
    _Run,
)
from tools.mobile.resolve import AskCb


class ListedApp(NamedTuple):
    """What to find: the device, the label and the App Tester id installed."""

    serial: str
    label: str
    tester: str = APP_TESTER_PACKAGE


_LAUNCH_FAILED = InstallOutcome(
    False,
    "launch_failed",
    "Could not open the Firebase App Tester. Check that it is installed and "
    "the screen is unlocked.",
)


class _PickMixin:
    """Find an app's row in the App Tester list."""

    async def find_listed_app(
        self: _Flow, want: ListedApp, ask: Optional[AskCb] = None
    ) -> Union[str, InstallOutcome]:
        """Open App Tester, go back to its app list, scroll it, and return the
        bundle id of the one row matching the label (asking only when several
        match). Taps no row and installs nothing."""
        ui = self._ui_factory(want.serial)
        if not await ui.launch(want.tester):
            return _LAUNCH_FAILED
        shot = await ui.screen()
        if shot is None:
            return _LAUNCH_FAILED
        run = _Run(ui, InstallRequest(want.serial, "", want.label), ask, None)
        return await self._pick_row(run, shot.xml)
