"""App Tester UI: open the listing and select one app by its label (no install)."""

from __future__ import annotations

from typing import NamedTuple, Optional

from tools.mobile.dialog_handlers import visible_texts
from tools.mobile.install_source import InstallOutcome, InstallRequest
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_PACKAGE,
    _Flow,
    _Run,
)
from tools.mobile.resolve import AskCb


class ListedApp(NamedTuple):
    """What to select: the device, the label and the App Tester id installed."""

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
    """Select an app's card in the App Tester list."""

    async def open_listed_app(
        self: _Flow, want: ListedApp, ask: Optional[AskCb] = None
    ) -> InstallOutcome:
        """Open App Tester, scroll the whole list, select the card matching the
        label (asking only when none or several match). Installs nothing."""
        ui = self._ui_factory(want.serial)
        if not await ui.launch(want.tester):
            return _LAUNCH_FAILED
        shot = await ui.screen()
        if shot is None:
            return _LAUNCH_FAILED
        run = _Run(ui, InstallRequest(want.serial, "", want.label), ask, None)
        opened = await self._open_card(run, visible_texts(shot.xml))
        return opened or InstallOutcome(True, "", "Selected the app in App Tester.")
