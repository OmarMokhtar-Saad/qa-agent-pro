"""App Tester UI: constants, the per-install state and the mixin contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, NamedTuple, Optional, Protocol

from tools.mobile.app_info import AppVersion
from tools.mobile.install_source import DeviceUi, InstallOutcome, InstallRequest
from tools.mobile.resolve import AskCb

APP_TESTER_PACKAGE = "dev.firebase.appdistribution"

#: Scrolls of the App Tester list made to look for the package id; each one is
#: a screen dump, so this bounds the search, not the list.
MAX_LISTING_SCROLLS = 6

#: Pause between screens while something loads or installs.
APP_TESTER_POLL_S = 1.0

ACTION_LABELS = ("Update", "Download", "Install")
OPEN_LABEL = "Open"
_PROGRESS_STARTS = ("downloading", "installing", "pending", "preparing")
_VERSION_RE = re.compile(r"\b(\d+(?:\.\d+)*)\s*\((\d+)\)")
#: Listing lines that are not app names: a card's release count, the list header.
_RELEASE_COUNT_RE = re.compile(r"^\d+\s+releases?$", re.IGNORECASE)
_LISTING_HEADERS = ("Test apps",)

SleepFn = Callable[[float], Awaitable[None]]
VersionFn = Callable[[str, str], Awaitable[Optional[AppVersion]]]
UiFactory = Callable[[str], DeviceUi]


@dataclass
class _Run:
    """State of one install attempt."""

    ui: DeviceUi
    request: InstallRequest
    ask: Optional[AskCb]
    before: Optional[AppVersion]
    card_tapped: bool = False
    release_done: bool = False
    tapped: int = 0


class Seen(NamedTuple):
    """A screen the flow could not place: the front package, its XML and texts."""

    front: str
    xml: str
    texts: Any


# What the mixins (screens, cards, detail) call on each other and on the class.
# Mirrors the methods defined in app_tester_ui_screens, _cards and _detail; the
# mypy-strict gate catches drift.
class _Flow(Protocol):
    _sleep: SleepFn
    _ui_factory: UiFactory

    async def _own_app(self, run: _Run) -> Optional[InstallOutcome]: ...

    async def _foreign(self, run: _Run, seen: Seen) -> Optional[InstallOutcome]: ...

    async def _perform(self, ui: DeviceUi, action: Any) -> None: ...

    async def _tester_screen(
        self, run: _Run, texts: Any
    ) -> Optional[InstallOutcome]: ...

    async def _open_card(self, run: _Run, texts: Any) -> Optional[InstallOutcome]: ...

    async def _scan_listing(self, run: _Run, texts: Any) -> tuple: ...

    async def _tap_card(
        self, run: _Run, text: str, scroll_back: bool = False
    ) -> Optional[InstallOutcome]: ...

    async def _detail(self, run: _Run, texts: Any) -> Optional[InstallOutcome]: ...

    async def _tap_release(
        self, run: _Run, releases: list
    ) -> Optional[InstallOutcome]: ...
