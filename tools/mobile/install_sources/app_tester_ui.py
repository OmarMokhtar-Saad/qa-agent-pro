"""The on-device Firebase App Tester as an InstallSource.

Drives ``dev.firebase.appdistribution`` through a :class:`DeviceUi`; no Firebase
API, CLI or credential is involved (owner decision, 2026-10-05). The flow, one
screen at a time, is bounded by ``MAX_APP_TESTER_STEPS`` steps and
``APP_TESTER_FLOW_TIMEOUT_S`` seconds:

1. open App Tester, press back (at most ``MAX_BACK_TO_LIST`` times) until its
   app list shows -- it reopens on the last app page viewed -- and find the
   app's list row (scrolling at most ``MAX_LISTING_SCROLLS`` times): by the
   package id when a row's bundle id is it, else by its label, every word of
   which must be in the row's name or bundle id. Only list rows are candidates,
   never release notes; several matching rows are asked about, never picked;
2. on the app page, choose a release when several are listed (the request's
   release or target version code decides; ``latest`` leaves it to the page's
   own button; otherwise ask) and tap Update / Download / Install ONCE. An AAB
   one-time setup screen (Get started) stops the flow as the tester's step;
3. hand every screen of another app (package installer, permission controller)
   to ``dialog_handlers.handle``, which taps only visible controls and never a
   destructive one;
4. stop as soon as the installed versionCode reaches the target (or rises).

Never blind-taps: a screen the flow does not recognise ends it with
``unrecognised_screen`` and nothing tapped. Never uninstalls: a signing mismatch
is reported as ``needs_confirmation="uninstall"`` and the CALLER asks the
tester. The App Tester screen strings below are UNVERIFIED against a real
device; the real-device script (verify_mobile_run_fixes.py) is the check.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from tools.mobile import adb
from tools.mobile.adb_device_ui import AdbDeviceUi
from tools.mobile.app_info import read_version
from tools.mobile.dialog_handlers import visible_texts
from tools.mobile.install_source import InstallOutcome, InstallRequest, clip_releases
from tools.mobile.install_sources.app_tester_ui_cards import _CardMixin
from tools.mobile.install_sources.app_tester_ui_consts import (  # noqa: F401
    ACTION_LABELS,
    APP_TESTER_PACKAGE,
    APP_TESTER_POLL_S,
    MAX_LISTING_SCROLLS,
    OPEN_LABEL,
    SleepFn,
    UiFactory,
    VersionFn,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_detail import _DetailMixin
from tools.mobile.install_sources.app_tester_ui_funcs import (  # noqa: F401
    _already_current,
    _improved,
    _refuse_request,
    parse_releases,
)
from tools.mobile.install_sources.app_tester_ui_pick import _PickMixin
from tools.mobile.install_sources.app_tester_ui_screens import _ScreenMixin
from tools.mobile.resolve import AskCb, PickOpts, decide, resolve_or_ask  # noqa: F401

#: Whole flow, seconds. A slow device dumps a screen in ~11 s and an install
#: downloads over the network, so this is minutes, not seconds.
APP_TESTER_FLOW_TIMEOUT_S = 240

#: Loop bound: screens examined before the flow gives up.
MAX_APP_TESTER_STEPS = 60


class AppTesterUiSource(_ScreenMixin, _CardMixin, _DetailMixin, _PickMixin):
    """InstallSource ``app_tester``: update or install through the App Tester UI."""

    name = "app_tester"

    def __init__(
        self,
        ui_factory: Optional[UiFactory] = None,
        *,
        versions: Optional[VersionFn] = None,
        sleep: Optional[SleepFn] = None,
    ) -> None:
        self._ui_factory = ui_factory or AdbDeviceUi
        self._versions = versions or read_version
        self._sleep = sleep or asyncio.sleep

    async def available(self, serial: str) -> bool:
        listing = await adb.installed_packages(serial)
        return APP_TESTER_PACKAGE in (listing.get("content") or [])

    async def list_releases(self, request: InstallRequest) -> list:
        """Open the app page and read its releases; taps nothing but the card."""
        run = _Run(self._ui_factory(request.serial), request, None, None)
        if not await run.ui.launch(APP_TESTER_PACKAGE):
            return []
        shot = await run.ui.screen()
        if shot is None or await self._open_card(run, shot.xml):
            return []
        detail = await run.ui.screen()
        if detail is None:
            return []
        return clip_releases(parse_releases(visible_texts(detail.xml)))

    async def install(
        self, request: InstallRequest, ask: Optional[AskCb] = None
    ) -> InstallOutcome:
        refusal = _refuse_request(request)
        if refusal is not None:
            return refusal
        before = await self._versions(request.serial, request.package)
        current = _already_current(before, request)
        if current is not None:
            return current
        ui = self._ui_factory(request.serial)
        if not await ui.launch(APP_TESTER_PACKAGE):
            return InstallOutcome(
                False,
                "launch_failed",
                "Could not open the Firebase App Tester. Check that it is "
                "installed and the screen is unlocked.",
            )
        return await self._drive(_Run(ui, request, ask, before))

    async def _drive(self, run: _Run) -> InstallOutcome:
        deadline = time.monotonic() + APP_TESTER_FLOW_TIMEOUT_S
        for _ in range(MAX_APP_TESTER_STEPS):
            if time.monotonic() >= deadline:
                return await self._gave_up(run, "flow_timeout")
            landed = await self._landed(run)
            if landed is not None:
                return landed
            stop = await self._step(run)
            if stop is not None:
                return stop
        return await self._gave_up(run, "step_limit")

    async def _gave_up(self, run: _Run, code: str) -> InstallOutcome:
        landed = await self._landed(run)
        if landed is not None:
            return landed
        return InstallOutcome(
            False,
            code,
            "The App Tester flow stopped (" + code + ") before the version "
            "changed. Nothing was uninstalled.",
        )

    async def _landed(self, run: _Run) -> Optional[InstallOutcome]:
        if not run.tapped:
            return None
        after: Any = await self._versions(run.request.serial, run.request.package)
        if not _improved(run.before, after, run.request.target_version_code):
            return None
        return InstallOutcome(
            True, "", "Installed through the App Tester.", after.version_code
        )
